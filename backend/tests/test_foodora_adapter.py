"""foodora adapter and normalization; synthetic tokens, transport and orders only."""

import base64
import json
from typing import Any

import httpx
import pytest

from cartalytix.providers.foodora.client import FoodoraClient, FoodoraError, link
from cartalytix.providers.foodora.normalization import validate_order


def jwt(user_id: str = "42", expires: int = 4102444800) -> str:
    payload = {"user_id": user_id, "expires": expires}
    claims = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"header.{claims.rstrip('=')}.signature"


def order(code: str = "a1", *, total: int = 235, delivered: bool = True) -> dict[str, Any]:
    return {
        "order_code": code,
        "ordered_at": {"date": "2024-01-12 11:31:27", "timezone": "Europe/Prague"},
        "subtotal": 315,
        "total_value": total,
        "rider_tip": 0,
        "status_flags": {"is_delivered": delivered, "is_canceled": False},
        "dynamic_fees": [
            {"translation_key": "NEXTGEN_CART_SUBTOTAL", "value": 315},
            {"translation_key": "NEXTGEN_DELIVERY_FEE", "value": 0},
            {"translation_key": "NEXTGEN_CART_DISCOUNT", "value": -63},
            {"translation_key": "NEXTGEN_CART_CONTAINER_CHARGES", "value": 20},
            {"translation_key": "NEXTGEN_CART_SERVICE_FEE", "value": 13},
            {"translation_key": "NEXTGEN_COUT_VOUCHER", "value": -50},
            {"translation_key": "NEXTGEN_TOTAL_VAT", "value": total},
        ],
        "order_products": [
            {"name": "Polévka", "quantity": 1, "total_price": 80, "variation_id": "1"},
            {"name": "Burger", "quantity": 2, "total_price": 235, "variation_id": "2"},
        ],
    }


def test_order_maps_products_fees_and_discounts() -> None:
    result = validate_order({}, order(), "a1")
    assert result is not None
    assert result.get("issues") == []
    assert (result.get("total"), result.get("currency"), result.get("date")) == (
        "235",
        "CZK",
        "2024-01-12T11:31:27",
    )
    items = result.get("items") or []
    assert [item.get("name") for item in items] == [
        "Polévka",
        "Burger",
        "container_charges",
        "service_fee",
    ]
    assert items[1].get("unit_price") == "117.5"
    assert result.get("discounts") == [
        {"name": "discount", "amount": "63"},
        {"name": "voucher", "amount": "50"},
    ]


def test_order_records_unitemized_discount_and_skips_undelivered() -> None:
    legacy = validate_order({}, order(total=135), "a1")
    assert legacy is not None and legacy.get("issues") == []
    assert (legacy.get("discounts") or [])[-1] == {
        "name": "discounts_and_credits",
        "amount": "100",
    }
    broken = validate_order({}, order(total=300), "a1")
    assert broken is not None and "totals_unreconciled" in (broken.get("issues") or [])
    assert validate_order({}, order(delivered=False), "a1") is None
    with pytest.raises(FoodoraError):
        validate_order({}, order("other"), "a1")


def transport(*, status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {jwt()}":
            return httpx.Response(401, json={"status_code": 401})
        if status != 200:
            return httpx.Response(status, json={"status_code": status})
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", "20"))
        items = [order(f"o{offset + i}") for i in range(min(limit, 20 if offset == 0 else 3))]
        return httpx.Response(200, json={"data": {"items": items, "total_count": "999"}})

    return httpx.MockTransport(handle)


def test_link_verifies_token_and_reads_subject() -> None:
    with httpx.Client(transport=transport()) as http:
        account = link(jwt(), http=http)
    assert account.subject == "42"
    assert json.loads(account.credential) == {"access_token": jwt()}
    expired = jwt(expires=1)
    for token, status in ((jwt(), 401), (expired, 200), ("not-a-jwt", 200)):
        with (
            httpx.Client(transport=transport(status=status)) as http,
            pytest.raises(FoodoraError) as error,
        ):
            link(token, http=http)
        assert error.value.code == "auth_required"


def test_client_pages_by_offset_and_serves_details_from_history() -> None:
    credential = json.dumps({"access_token": jwt()})
    with httpx.Client(transport=transport()) as http:
        client = FoodoraClient(credential, lambda _value: None, http=http, delay=0)
        first = client.page(1)
        second = client.page(2)
        detail = client.detail("o21")
    assert (len(first.get("orders") or []), len(second.get("orders") or [])) == (20, 3)
    assert detail.get("order_code") == "o21"
    with pytest.raises(FoodoraError) as error:
        FoodoraClient(json.dumps({"access_token": jwt(expires=1)}), lambda _value: None)
    assert error.value.code == "auth_required"
