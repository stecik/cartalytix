"""Mixit adapter and normalization; synthetic HTTP transport and orders only."""

from decimal import Decimal
from typing import Any

import httpx
import pytest

from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.mixit.client import MixitClient, MixitError, sign_in
from cartalytix.providers.mixit.normalization import (
    DISCOUNT_NAME,
    SHIPPING_NAME,
    validate_order,
)

TOKEN = "synthetic-mixit-token"


def order(total: str = "199.50", state: str = "shipped") -> dict[str, Any]:
    return {
        "order": {
            "hash": "order-a",
            "state": state,
            "createdAt": "02. 01. 2026",
            "currency": "Kč",
            "totalPrice": total,
            "orderProducts": [
                {
                    "productID": 11,
                    "name": "Synthetic oats",
                    "quantity": 2,
                    "price": "49.95",
                    "totalPrice": "99.90",
                }
            ],
            "orderMixes": [
                {
                    "mixID": 7,
                    "name": "Synthetic mix",
                    "quantity": 1,
                    "price": "99.60",
                    "totalPrice": "99.60",
                    "mixProducts": [{"productID": 2, "name": "A", "totalPrice": "99.60"}],
                }
            ],
        }
    }


def test_sign_in_returns_token_and_customer_subject() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://admin.mixit.cz/api/v1/sign_in"
        assert request.read() == b'{"email":"a@example.com","password":"secret"}'
        return httpx.Response(200, json={"token": TOKEN, "customer": {"id": 42}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        account = sign_in("a@example.com", "secret", http=http)
    assert (account.token, account.subject) == (TOKEN, "42")


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "credentials_rejected"),
        (422, "credentials_rejected"),
        (429, "login_rate_limited"),
        # A missing route must never be reported to the user as a wrong password.
        (404, "provider_unavailable"),
    ],
)
def test_sign_in_maps_rejections_without_response_body(status: int, code: str) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="secret-body"))
    with httpx.Client(transport=transport) as http, pytest.raises(MixitError) as error:
        sign_in("a@example.com", "secret", http=http)
    assert error.value.code == code
    assert "secret" not in str(error.value)


def test_client_sends_token_header_and_keeps_raw_bytes() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"orders":[],"totalRecords":0}')

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = MixitClient(TOKEN, lambda _: None, http=http, delay=0)
        page = client.page(3)
    assert page == {"orders": [], "totalRecords": 0}
    assert isinstance(page, ProviderPayload)
    assert page.raw == b'{"orders":[],"totalRecords":0}'
    assert str(seen[0].url) == "https://admin.mixit.cz/api/v1/customer/orders?page=3"
    assert seen[0].headers["Token"] == TOKEN


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_token_requires_reauthorization(status: int) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(status))
    with httpx.Client(transport=transport) as http, pytest.raises(MixitError) as error:
        MixitClient(TOKEN, lambda _: None, http=http, delay=0).detail("order-a")
    assert error.value.code == "auth_required"


def test_transient_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cartalytix.providers.mixit.client.time.sleep", lambda _: None)
    responses = iter([httpx.Response(503), httpx.Response(200, json={"order": {}})])
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as http:
        assert MixitClient(TOKEN, lambda _: None, http=http, delay=0).detail("x") == {"order": {}}


def test_normalization_keeps_mix_as_one_item_and_date_only() -> None:
    result = validate_order({}, order(), "order-a")
    assert result is not None
    assert result.get("issues") == []
    assert result.get("date") == "2026-01-02"
    assert result.get("currency") == "CZK"
    assert [item.get("name") for item in result.get("items", [])] == [
        "Synthetic oats",
        "Synthetic mix",
    ]
    assert result.get("items", [])[1].get("code") == "mix:7"
    assert result.get("discounts") == []


def test_positive_gap_becomes_shipping_item() -> None:
    result = validate_order({}, order(total="278.50"), "order-a")
    assert result is not None
    shipping = result.get("items", [])[-1]
    assert (shipping.get("name"), shipping.get("original_amount")) == (SHIPPING_NAME, "79.00")
    assert sum(Decimal(item.get("original_amount")) for item in result.get("items", [])) == (
        Decimal("278.50")
    )


def test_negative_gap_becomes_positive_order_discount() -> None:
    result = validate_order({}, order(total="150.00"), "order-a")
    assert result is not None
    assert result.get("discounts") == [{"name": DISCOUNT_NAME, "amount": "49.50"}]
    assert len(result.get("items", [])) == 2


def test_unfinished_order_is_skipped_and_identity_is_checked() -> None:
    assert validate_order({}, order(state="new"), "order-a") is None
    with pytest.raises(MixitError):
        validate_order({}, order(), "order-b")


def test_line_mismatch_is_a_quality_issue() -> None:
    broken = order()
    broken["order"]["orderProducts"][0]["totalPrice"] = "100.00"
    result = validate_order({}, broken, "order-a")
    assert result is not None
    assert "quantity_unit_price_mismatch" in result.get("issues", [])
