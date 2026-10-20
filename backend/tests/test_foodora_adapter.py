"""foodora adapter and normalization; synthetic tokens, transport and orders only."""

import base64
import json
import time
from typing import Any

import httpx
import pytest

from cartalytix.providers.foodora import client as foodora_client
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


def session_credential(*, expires: int = 4102444800, subject: str = "42") -> str:
    return json.dumps(
        {
            "access_token": jwt(user_id=subject, expires=expires),
            "refresh_token": "old-refresh",
            "device_token": "device",
            "subject": subject,
            "expires": expires,
        }
    )


def test_expired_session_refreshes_and_persists_all_rotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []
    calls: list[dict[str, Any]] = []
    heartbeats: list[None] = []
    fresh = json.loads(session_credential())
    fresh["refresh_token"] = "new-refresh"

    def refresh(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"session": fresh, "report": {"authenticated": True}}

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=transport()) as http:
        client = FoodoraClient(
            session_credential(expires=1),
            saved.append,
            http=http,
            delay=0,
            heartbeat=lambda: heartbeats.append(None),
        )
        assert len(client.page(1).get("orders", [])) == 20
        client.page(2)
    assert len(calls) == 1
    assert calls[0].get("command") == "refresh"
    assert json.loads(saved[0]) == fresh
    assert len(heartbeats) >= 4


def test_401_recovers_once_independently_of_transient_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = [503, 503, 503, 401, 200]
    refreshes: list[dict[str, Any]] = []

    def refresh(payload: dict[str, Any]) -> dict[str, Any]:
        refreshes.append(payload)
        return {"session": json.loads(session_credential()), "report": {"authenticated": True}}

    def handle(request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return httpx.Response(status, json={"data": {"items": []}})

    monkeypatch.setattr(foodora_client, "execute", refresh)
    monkeypatch.setattr(foodora_client.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = FoodoraClient(session_credential(), lambda _value: None, http=http, delay=0)
        assert client.page(1).get("orders") == []
    assert len(refreshes) == 1
    assert statuses == []


def test_repeated_401_has_bounded_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshes: list[dict[str, Any]] = []

    def refresh(payload: dict[str, Any]) -> dict[str, Any]:
        refreshes.append(payload)
        return {"session": json.loads(session_credential()), "report": {"authenticated": True}}

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=transport(status=401)) as http:
        client = FoodoraClient(session_credential(), lambda _value: None, http=http, delay=0)
        with pytest.raises(FoodoraError, match="^auth_required$"):
            client.page(1)
    assert len(refreshes) == 1


def test_refresh_cannot_replace_account_or_persist_foreign_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[str] = []

    def refresh(_payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "session": json.loads(session_credential(subject="99")),
            "report": {"authenticated": True},
        }

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=transport()) as http:
        client = FoodoraClient(session_credential(expires=1), saved.append, http=http, delay=0)
        with pytest.raises(FoodoraError, match="^account_mismatch$"):
            client.page(1)
    assert saved == []


def test_refresh_challenge_preserves_still_valid_access(monkeypatch: pytest.MonkeyPatch) -> None:
    expires = int(time.time()) + 60

    def refresh(_payload: dict[str, Any]) -> dict[str, Any]:
        raise FoodoraError("challenge_required")

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == f"Bearer {jwt(expires=expires)}"
        return httpx.Response(200, json={"data": {"items": []}})

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = FoodoraClient(
            session_credential(expires=expires), lambda _value: None, http=http, delay=0
        )
        assert client.page(1).get("orders") == []


def test_history_403_is_not_misclassified_as_expired_session() -> None:
    with httpx.Client(transport=transport(status=403)) as http:
        client = FoodoraClient(session_credential(), lambda _value: None, http=http, delay=0)
        with pytest.raises(FoodoraError, match="^challenge_required$"):
            client.page(1)


@pytest.mark.parametrize("status,code", [(429, "rate_limited"), (503, "provider_unavailable")])
def test_transient_history_retries_are_bounded(
    status: int,
    code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={})

    monkeypatch.setattr(foodora_client.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = FoodoraClient(session_credential(), lambda _value: None, http=http, delay=0)
        with pytest.raises(FoodoraError, match=f"^{code}$"):
            client.page(1)
    assert len(requests) == 4


def test_legacy_access_cannot_refresh_without_refresh_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refresh(_payload: dict[str, Any]) -> dict[str, Any]:
        pytest.fail("Legacy credentials cannot be sent to refresh")

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=transport(status=401)) as http:
        client = FoodoraClient(
            json.dumps({"access_token": jwt()}), lambda _value: None, http=http, delay=0
        )
        with pytest.raises(FoodoraError, match="^auth_required$"):
            client.page(1)


def test_expired_session_does_not_try_history_after_transient_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refresh(_payload: dict[str, Any]) -> dict[str, Any]:
        raise FoodoraError("provider_unavailable")

    def handle(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Expired access token must be refreshed before history")

    monkeypatch.setattr(foodora_client, "execute", refresh)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = FoodoraClient(
            session_credential(expires=1), lambda _value: None, http=http, delay=0
        )
        with pytest.raises(FoodoraError, match="^provider_unavailable$"):
            client.page(1)
