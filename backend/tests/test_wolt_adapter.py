"""Wolt adapter and normalization; synthetic HTTP transport and orders only."""

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.wolt.client import (
    WoltAccount,
    WoltClient,
    WoltError,
    exchange_magic_link,
    parse_magic_link,
)
from cartalytix.providers.wolt.normalization import validate_order

LINK = "https://wolt.com/me/magic_login?email=a%40example.com&email_hash=h&token=magic-1"


def purchase(status: str = "delivered", total: int = 23900) -> dict[str, Any]:
    return {
        "order_id": "p1",
        "status": status,
        "creation_time": "14/10/2026, 13:19",
        "venue_timezone": "Europe/Prague",
        "venue_name": "Synthetic Venue",
        "currency": "CZK",
        "items": [
            {
                "id": "i1",
                "name": "Pizza",
                "count": 2,
                "price": 9900,
                "end_amount": 21800,
                "options": [{"id": "o1", "values": [{"id": "v1", "count": 1, "price": 1000}]}],
            }
        ],
        "items_price": 21800,
        "delivery_price": 4900,
        "service_fee": 1500,
        "discounts": [{"title": "Promo", "amount": 2000}],
        "credits": 800,
        "tokens": 0,
        "total_price": total,
    }


def jwt(claims: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"h.{body}.s"


ACCESS = jwt({"user": {"id": "user-1", "email": "real@example.com"}})


def test_magic_link_yields_token_and_rejects_foreign_or_partial_links() -> None:
    assert parse_magic_link(f" {LINK} ") == "magic-1"
    for bad in (
        "https://evil.example/me/magic_login?token=x",
        "https://wolt.com/me/magic_login?email=a%40b.cz",
        "magic-1",
    ):
        with pytest.raises(WoltError):
            parse_magic_link(bad)


def test_exchange_takes_identity_from_token_not_masked_link() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": ACCESS, "refresh_token": "r1"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        account = exchange_magic_link("magic-1", http=http)
    assert account == WoltAccount(refresh_token="r1", subject="user-1", email="real@example.com")
    form = parse_qs(seen[0].read().decode())
    assert form == {
        "grant_type": ["email_login"],
        "token": ["magic-1"],
        "audience": ["wolt-com"],
        "capabilities": ["access_confirmation"],
    }


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(400, json={"error_code": 1}), "magic_link_rejected"),
        (httpx.Response(200, json={"access_confirmation": {}}), "access_confirmation_required"),
        (httpx.Response(429), "login_rate_limited"),
        (
            httpx.Response(200, json={"access_token": jwt({}), "refresh_token": "r"}),
            "account_identity_unavailable",
        ),
    ],
)
def test_exchange_failures(response: httpx.Response, code: str) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http,
        pytest.raises(WoltError) as error,
    ):
        exchange_magic_link("magic-1", http=http)
    assert error.value.code == code


def test_login_rejection_keeps_safe_diagnostic() -> None:
    response = httpx.Response(
        401,
        json={"error": "invalid_credentials", "token": "private", "description": "private"},
    )
    with (
        httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http,
        pytest.raises(WoltError) as error,
    ):
        exchange_magic_link("magic-1", http=http)
    assert error.value.code == "magic_link_rejected"
    assert error.value.diagnostic == {
        "phase": "login",
        "status": 401,
        "reason": "invalid_credentials",
    }


def test_refresh_rejection_keeps_safe_diagnostic_without_retry() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            401,
            json={
                "error": "invalid_grant",
                "error_description": "secret refresh token",
                "access_token": "secret access token",
                "refresh_token": "secret refresh token",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = WoltClient("r1", lambda _: None, http=http, delay=0)
        with pytest.raises(WoltError) as error:
            client.renew()
        assert error.value.code == "auth_required"
        assert error.value.diagnostic == {
            "phase": "refresh",
            "status": 401,
            "reason": "invalid_grant",
        }
        with pytest.raises(WoltError):
            client.renew()
    assert calls == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ConnectError, "provider_unavailable"),
        (httpx.ConnectTimeout, "provider_unavailable"),
        (httpx.PoolTimeout, "provider_unavailable"),
        (httpx.ReadTimeout, "auth_required"),
        (httpx.WriteError, "auth_required"),
    ],
)
def test_refresh_transport_failure_keeps_only_exception_kind(
    failure: type[httpx.RequestError], code: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise failure("secret refresh token", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(WoltError) as error,
    ):
        WoltClient("r1", lambda _: None, http=http, delay=0).renew()
    assert error.value.code == code
    assert error.value.diagnostic == {"phase": "refresh", "transport": failure.__name__}


def test_save_refresh_failure_keeps_safe_diagnostic_without_retry() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r2"})

    def save(_: str) -> None:
        raise RuntimeError("private persistence details")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = WoltClient("r1", save, http=http, delay=0)
        with pytest.raises(WoltError) as error:
            client.renew()
        assert error.value.code == "auth_required"
        assert error.value.diagnostic == {"phase": "save_refresh", "reason": "persistence_failed"}
        with pytest.raises(WoltError):
            client.renew()
    assert calls == 1


@pytest.mark.parametrize("phase", ["history", "detail"])
def test_data_rejection_keeps_request_phase(phase: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "authentication.wolt.com":
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r1"})
        return httpx.Response(401, json={"error": "invalid_token", "token": "private"})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(WoltError) as error,
    ):
        client = WoltClient("r1", lambda _: None, http=http, delay=0)
        if phase == "history":
            client.page(1)
        else:
            client.detail("p1")
    assert error.value.code == "auth_required"
    assert error.value.diagnostic == {"phase": phase, "status": 401, "reason": "invalid_token"}


def test_client_rotates_refresh_and_reads_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cartalytix.providers.wolt.client.time.sleep", lambda _: None)
    saved: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "authentication.wolt.com":
            assert parse_qs(request.read().decode()).get("refresh_token") == ["r1"]
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r2"})
        assert saved == ["r2"] and request.headers["Authorization"] == "Bearer a"
        if request.url.path.endswith("/order_history/"):
            assert request.url.params["limit"] == "50"
            return httpx.Response(200, json={"orders": [{"purchase_id": "p1"}]})
        assert request.url.path.endswith("/purchase/p1")
        return httpx.Response(200, json={"order_id": "p1"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = WoltClient("r1", saved.append, http=http, delay=0)
        first = client.page(1)
        assert first.get("orders") == [{"purchase_id": "p1"}]
        assert isinstance(first, ProviderPayload)
        assert client.page(2).get("orders") == []
        assert client.detail("p1") == {"order_id": "p1"}


def test_full_page_is_never_claimed_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cartalytix.providers.wolt.client.time.sleep", lambda _: None)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "authentication.wolt.com":
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r1"})
        return httpx.Response(200, json={"orders": [{"purchase_id": str(n)} for n in range(50)]})

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as http,
        pytest.raises(WoltError) as error,
    ):
        WoltClient("r1", lambda _: None, http=http, delay=0).page(1)
    assert error.value.code == "history_incomplete"


def test_normalization_reconciles_minor_units() -> None:
    result = validate_order({}, purchase(), "p1")
    assert result is not None
    assert result.get("issues") == []
    assert result.get("merchant") == "Synthetic Venue"
    assert result.get("total") == "239"
    assert result.get("date") == "2026-10-14T13:19:00+02:00"
    # Czech app-language formats detail times differently; the history epoch wins.
    localized = {**purchase(), "creation_time": "14.10.26 13:19"}
    paid = int(datetime(2026, 10, 14, 11, 19, tzinfo=UTC).timestamp() * 1000)
    stamped = validate_order({"payment_time_ts": paid}, localized, "p1")
    assert stamped is not None and stamped.get("issues") == []
    assert stamped.get("date") == "2026-10-14T11:19:00+00:00"
    pizza, delivery = result.get("items", [])
    assert (pizza.get("unit_price"), pizza.get("original_amount")) == ("109", "218")
    assert delivery.get("original_amount") == "49"
    assert result.get("discounts") == [
        {"name": "Promo", "amount": "20"},
        {"name": "Wolt credits", "amount": "8"},
    ]
    gross = sum(Decimal(item.get("original_amount")) for item in result.get("items", []))
    assert gross - Decimal("28") == Decimal("239")


def test_undelivered_skipped_and_mismatch_flagged() -> None:
    assert validate_order({}, purchase(status="rejected"), "p1") is None
    result = validate_order({}, purchase(total=1), "p1")
    assert result is not None and "totals_unreconciled" in result.get("issues", [])
    with pytest.raises(WoltError):
        validate_order({}, purchase(), "p2")
