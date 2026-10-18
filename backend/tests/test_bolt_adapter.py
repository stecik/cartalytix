"""Bolt Food adapter and normalization; synthetic HTTP transport and orders only."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from cartalytix.providers.bolt.client import (
    BoltClient,
    BoltError,
    account_subject,
    confirm_verification,
    start_verification,
)
from cartalytix.providers.bolt.normalization import validate_order
from cartalytix.providers.common import ProviderPayload

PHONE = "+420000000000"
DEVICE = "device-1"
CREDENTIAL = json.dumps({"refresh_token": "refresh-1", "device_id": DEVICE})


def ok(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "message": "OK", "data": data})


def price(value: str, original: str | None = None) -> dict[str, Any]:
    # The client decodes JSON floats as Decimal, so fixtures use Decimal too.
    result: dict[str, Any] = {"value": Decimal(value), "currency": "czk"}
    if original is not None:
        result["original_price"] = {"value": Decimal(original), "currency": "czk"}
    return result


def component(kind: str, name: str, amount: str) -> dict[str, Any]:
    return {"type": kind, "data": {"name": {"value": name}, "amount": price(amount)}}


def order(state: str = "delivered", total: str = "202.70") -> dict[str, Any]:
    return {
        "order_id": 7,
        "order_state": state,
        "order_created_timestamp": 1792000000,
        "provider_name": {"value": "Synthetic Bistro"},
        "total_price": price(total),
        "user_baskets": [
            {
                "items": [
                    {
                        "name": {"value": "Noodles"},
                        "menu_item_id": 1,
                        "amount": 1,
                        "unit_item_price": price("159", original="169"),
                        "total_item_price": price("179"),
                        "options": [
                            {
                                "name": {"value": "Chicken"},
                                "amount": 1,
                                "unit_item_price": price("20"),
                                "total_item_price": price("20"),
                            }
                        ],
                    }
                ]
            }
        ],
        "total_price_components": [
            component("discount", "Sleva", "-10"),
            component("total", "Mezisoučet", "179"),
            component("fee", "Poplatek za služby", "14"),
            component("discount", "Sleva Bolt Plus", "-4.20"),
            component("fee", "Poplatek za doručení", "78.90"),
            component("discount", "Sleva na doručení", "-65"),
        ],
    }


def test_verification_bodies_and_token_extraction() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/start"):
            return ok({"state": "code_sent"})
        return ok({"auth": {"refresh_token": "refresh-1", "access_token": "a"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        start_verification(PHONE, DEVICE, http=http)
        account = confirm_verification(PHONE, DEVICE, "1234", http=http)
    start = json.loads(seen[0].content)
    assert start == {
        "type": "phone",
        "method": "sms",
        "phone_number": PHONE,
        "phone_uuid": DEVICE,
        "last_known_state": {},
    }
    assert seen[0].url.params["deviceId"] == DEVICE
    assert json.loads(seen[1].content).get("code") == "1234"
    assert json.loads(account.credential) == {"refresh_token": "refresh-1", "device_id": DEVICE}
    assert account.subject == account_subject(PHONE) and PHONE not in account.subject


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (ok({"challenge": {"type": "recaptcha"}}), "challenge_required"),
        (httpx.Response(200, json={"code": 1003, "message": "private"}), "phone_rejected"),
        (httpx.Response(429), "login_rate_limited"),
    ],
)
def test_verification_failures_are_not_bypassed(response: httpx.Response, code: str) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http,
        pytest.raises(BoltError) as error,
    ):
        start_verification(PHONE, DEVICE, http=http)
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_client_rotates_refresh_pages_by_token_and_details_with_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cartalytix.providers.bolt.client.time.sleep", lambda _: None)
    saved: list[str] = []
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("getAccessTokenWithRotation"):
            assert request.headers["Authorization"] == "Bearer refresh-1"
            return ok({"access_token": "access", "refresh_token": "refresh-2"})
        assert saved and request.headers["Authorization"] == "Bearer access"
        if request.url.path.endswith("getOrderDetails"):
            assert json.loads(request.content) == {"order_id": 7, "city_id": 3}
            return ok({"order_id": 7})
        if request.url.params.get("page_token") == "t2":
            return ok({"orders": [{"order_id": 8, "city_id": 3}], "next_page_token": ""})
        return ok({"orders": [{"order_id": 7, "city_id": 3}], "next_page_token": "t2"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = BoltClient(CREDENTIAL, saved.append, http=http, delay=0)
        first = client.page(1)
        assert first.get("orders") == [{"order_id": 7, "city_id": 3}]
        assert client.page(2).get("orders") == [{"order_id": 8, "city_id": 3}]
        assert client.page(3).get("orders") == []
        assert client.detail("7") == {"order_id": 7}
    assert json.loads(saved[0]) == {"refresh_token": "refresh-2", "device_id": DEVICE}
    assert isinstance(first, ProviderPayload) and first.raw.startswith(b'{"code"')
    assert len([r for r in seen if "getAccessToken" in r.url.path]) == 1


def test_rejected_refresh_requires_reauthorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cartalytix.providers.bolt.client.time.sleep", lambda _: None)
    transport = httpx.MockTransport(lambda _: httpx.Response(401))
    with httpx.Client(transport=transport) as http, pytest.raises(BoltError) as error:
        BoltClient(CREDENTIAL, lambda _: None, http=http, delay=0).page(1)
    assert error.value.code == "auth_required"


def test_normalization_reconciles_items_fees_and_discounts() -> None:
    result = validate_order({}, order(), "7")
    assert result is not None
    assert result.get("issues") == []
    assert result.get("merchant") == "Synthetic Bistro"
    assert result.get("currency") == "CZK"
    assert result.get("date", "").startswith("2026-")
    items = result.get("items", [])
    assert [item.get("name") for item in items] == [
        "Noodles",
        "Poplatek za služby",
        "Poplatek za doručení",
    ]
    noodles = items[0]
    assert (noodles.get("unit_price"), noodles.get("original_amount")) == ("179", "189")
    assert noodles.get("discounts") == [{"name": "Sleva", "amount": "10"}]
    assert result.get("discounts") == [
        {"name": "Sleva Bolt Plus", "amount": "4.20"},
        {"name": "Sleva na doručení", "amount": "65"},
    ]
    gross = sum(Decimal(item.get("original_amount")) for item in items)
    savings = Decimal("10") + Decimal("4.2") + Decimal("65")
    assert gross - savings == Decimal("202.70")


def test_undelivered_orders_are_skipped_and_mismatch_is_flagged() -> None:
    assert validate_order({}, order(state="rejected"), "7") is None
    result = validate_order({}, order(total="999"), "7")
    assert result is not None and "totals_unreconciled" in result.get("issues", [])
    with pytest.raises(BoltError):
        validate_order({}, order(), "8")
