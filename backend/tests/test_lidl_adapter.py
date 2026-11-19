"""Lidl network boundary regressions using synthetic token responses."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from cartalytix.providers.lidl.client import LidlClient, LidlError, exchange_refresh


def token() -> dict[str, Any]:
    return {"access_token": "access", "refresh_token": "rotated", "expires_in": 3600}


def test_rotation_persisted_before_get_and_observed_list_request() -> None:
    saved: list[str] = []
    progress: list[None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url == "https://accounts.lidl.com/connect/token"
            assert b"grant_type=refresh_token" in request.content
            return httpx.Response(200, json=token())
        assert len(saved) == 1
        assert json.loads(saved[0]).get("refresh_token") == "rotated"
        assert json.loads(saved[0]).get("access_token") == "access"
        assert len(progress) == 2
        assert request.url.path == "/api/v2/CZ/tickets"
        assert request.url.params.get("pageNumber") == "2"
        assert request.url.params.get("onlyFavorite") == "false"
        assert request.headers.get("Accept-Language") == "cs-CZ"
        return httpx.Response(200, json={"tickets": [], "totalCount": 0})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient(
            "old", saved.append, http=http, delay=0, heartbeat=lambda: progress.append(None)
        )
        assert client.page(2).get("tickets") == []


def test_failed_rotation_persistence_blocks_all_further_requests() -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(200, json=token())

    def save(value: str) -> None:
        raise OSError("private credential payload")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient("private-refresh", save, http=http, delay=0)
        for _ in range(2):
            with pytest.raises(LidlError, match="auth_required"):
                client.page(1)
    assert calls == ["POST"]


@pytest.mark.parametrize("failure", ["timeout", "server", "invalid_json", "missing_access"])
def test_uncertain_refresh_never_replays_post_or_leaks_secrets(failure: str) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if failure == "timeout":
            raise httpx.ReadTimeout("private token URL", request=request)
        if failure == "server":
            return httpx.Response(503, text="private-token")
        if failure == "invalid_json":
            return httpx.Response(200, text="private-token")
        return httpx.Response(200, json={"refresh_token": "private-token"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient("private-refresh", lambda _: None, http=http, delay=0)
        for _ in range(2):
            with pytest.raises(LidlError) as caught:
                client.page(1)
            assert caught.value.code == "auth_required"
            assert str(caught.value) == "auth_required"
    assert calls == ["POST"]


def test_detail_get_retry_preserves_bare_czech_language_and_encoded_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    waits: list[float] = []
    monkeypatch.setattr("cartalytix.providers.lidl.client.time.sleep", waits.append)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json=token())
        assert request.headers.get("Accept-Language") == "cs"
        assert request.url.raw_path == b"/api/v3/CZ/tickets/test%2Fid%3Fsecret"
        if calls.count("GET") == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"id": "test/id?secret"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient("old", lambda _: None, http=http, delay=0)
        assert client.detail("test/id?secret").get("id") == "test/id?secret"
    assert calls == ["POST", "GET", "GET"]
    assert 2 in waits


@pytest.mark.parametrize("retry_after", ["61", "-1", "NaN", "inf", "tomorrow"])
def test_unbounded_retry_after_stops(retry_after: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=token())
        return httpx.Response(429, headers={"Retry-After": retry_after})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient("old", lambda _: None, http=http, delay=0)
        with pytest.raises(LidlError, match="rate_limited"):
            client.page(1)


def test_get_retries_bounded_without_replaying_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("cartalytix.providers.lidl.client.time.sleep", lambda _: None)

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return (
            httpx.Response(200, json=token()) if request.method == "POST" else httpx.Response(503)
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = LidlClient("old", lambda _: None, http=http, delay=0)
        with pytest.raises(LidlError, match="provider_unavailable"):
            client.page(1)
    assert calls == ["POST", "GET", "GET", "GET", "GET"]


def test_exchange_preserves_nonrotating_refresh_and_trusted_response_subject() -> None:
    response = {"access_token": "e30.eyJzdWIiOiJzeW50aGV0aWMtc3ViIn0.signature", "expires_in": 3600}
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as http:
        result = exchange_refresh("old", http=http)
    assert result.get("refresh_token") == "old"
    assert result.get("subject") == "synthetic-sub"


def test_original_json_and_decimal_prices_survive_http_decode() -> None:
    raw = b'{ "id": "one", "unitPrice": 999999999999.999999, "totalAmount": 0.10 }\n'

    def handle(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=token())
            if request.method == "POST"
            else httpx.Response(200, content=raw)
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = LidlClient("old", lambda _: None, http=http, delay=0).detail("one")
    assert result.get("unitPrice") == Decimal("999999999999.999999")
    assert getattr(result, "raw", None) == raw


def test_cached_access_survives_new_clients_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr("cartalytix.providers.lidl.client.time.time", lambda: now[0])
    credentials = json.dumps(
        {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "expires_at": 2200.0}
    )
    saved: list[str] = []
    refreshes: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            refreshes.append(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 1200,
                },
            )
        expected = "fresh-access" if now[0] == 1000 else "new-access"
        assert request.headers.get("Authorization") == f"Bearer {expected}"
        return httpx.Response(200, json={"tickets": [], "totalCount": 0})

    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        for _ in range(2):
            client = LidlClient(credentials, saved.append, http=http, delay=0)
            assert client.page(1).get("tickets") == []
            client.close()
        assert not refreshes and not saved
        now[0] = 2201.0
        client = LidlClient(credentials, saved.append, http=http, delay=0)
        assert client.page(1).get("tickets") == []
        client.close()
        stored = json.loads(saved[-1])
        assert stored.get("refresh_token") == "new-refresh"
        assert stored.get("access_token") == "new-access"
        assert stored.get("expires_at") == 3401.0
        next_client = LidlClient(saved[-1], saved.append, http=http, delay=0)
        assert next_client.page(1).get("tickets") == []
    assert len(refreshes) == 1
    assert "refresh_token=fresh-refresh" in refreshes[0]
    assert len(saved) == 1


@pytest.mark.parametrize(
    "credentials",
    [
        "{broken",
        "{}",
        '{"refresh_token":"private-refresh","expires_at":2200}',
        '{"access_token":"private-access","refresh_token":"private-refresh","expires_at":true}',
        '{"access_token":"private-access","refresh_token":"private-refresh","expires_at":"NaN"}',
    ],
)
def test_malformed_cached_credentials_fail_closed(credentials: str) -> None:
    with pytest.raises(LidlError, match="^auth_required$"):
        LidlClient(credentials, lambda _: None)
