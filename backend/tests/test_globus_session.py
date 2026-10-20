"""Synthetic Globus sessions; no account credentials or live API calls."""

import json

import httpx
import pytest

from cartalytix.providers.globus.client import GlobusClient
from cartalytix.providers.globus.session import (
    PROFILE_URL,
    REFRESH_URL,
    SESSION_PREFIX,
    GlobusError,
    GlobusSession,
    decode_session,
    refresh_session,
)


def session() -> GlobusSession:
    return GlobusSession("old-cookie", "old-session", "42", "Chrome/140", None)


def profile(subject: object = 42) -> dict[str, object]:
    return {
        "authenticated": True,
        "profile": {
            "registration": {"customerID": subject},
            "contacts": {"email": "account@example.invalid"},
        },
    }


def test_session_serialization_preserves_only_refresh_credentials() -> None:
    original = session()
    encoded = original.serialize()
    assert encoded.startswith(SESSION_PREFIX)
    restored = decode_session(encoded)
    assert restored == original
    assert set(json.loads(encoded.removeprefix(SESSION_PREFIX))) == {
        "session_cookie",
        "session_id",
        "subject",
        "user_agent",
        "cookie_expires",
    }
    assert "old-cookie" not in repr(original)
    assert "old-session" not in repr(original)
    assert decode_session("legacy-cookie") is None


@pytest.mark.parametrize(
    "patch",
    [
        {"session_cookie": "a;b"},
        {"session_id": ""},
        {"subject": ""},
        {"subject": "x" * 257},
        {"user_agent": "Chrome\r\nInjected: 1"},
        {"cookie_expires": True},
        {"cookie_expires": float("inf")},
    ],
)
def test_invalid_stored_session_requires_reauthentication(patch: dict[str, object]) -> None:
    payload = json.loads(session().serialize().removeprefix(SESSION_PREFIX))
    payload.update(patch)
    with pytest.raises(GlobusError) as error:
        decode_session(SESSION_PREFIX + json.dumps(payload))
    assert error.value.code == "auth_required"


def test_refresh_rotates_session_after_verifying_same_account() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "www.globus.cz"
        assert request.headers.get("user-agent") == "Chrome/140"
        if str(request.url) == REFRESH_URL:
            assert json.loads(request.content) == {"session_id": "old-session"}
            assert request.headers.get("cookie") == "oidc_session=old-cookie"
            return httpx.Response(
                200,
                json={"session_id": "new-session"},
                headers={"set-cookie": "oidc_session=new-cookie; Secure; HttpOnly; Path=/"},
            )
        assert str(request.url) == PROFILE_URL
        assert request.headers.get("cookie") == "oidc_session=new-cookie"
        return httpx.Response(200, json=profile())

    original = session()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        updated = refresh_session(original, http=http)
    assert (updated.cookie, updated.session_id, updated.subject) == (
        "new-cookie",
        "new-session",
        "42",
    )
    assert original.cookie == "old-cookie"
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "auth_required"),
        (httpx.Response(302, headers={"location": "https://evil.example/"}), "auth_required"),
        (httpx.Response(403), "provider_unavailable"),
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(500), "provider_unavailable"),
        (httpx.Response(200, text="private-challenge"), "provider_response_invalid"),
        (httpx.Response(200, json={"session_id": None}), "provider_response_invalid"),
    ],
)
def test_refresh_failure_does_not_follow_redirects_or_expose_response(
    response: httpx.Response, code: str
) -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return response

    with (
        httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as http,
        pytest.raises(GlobusError) as error,
    ):
        refresh_session(session(), http=http)
    assert error.value.code == code
    assert calls == [REFRESH_URL]
    assert "private-challenge" not in str(error.value)
    assert "old-cookie" not in str(error.value)


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        (profile(99), "account_mismatch"),
        (profile(True), "account_identity_unavailable"),
        ({"authenticated": False}, "auth_required"),
    ],
)
def test_refresh_rejects_missing_or_changed_account(identity: dict[str, object], code: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={} if str(request.url) == REFRESH_URL else identity)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        pytest.raises(GlobusError) as error,
    ):
        refresh_session(session(), http=http)
    assert error.value.code == code


def test_client_refreshes_once_per_sync_and_saves_rotation_before_receipts() -> None:
    calls: list[str] = []
    saved: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url) == REFRESH_URL:
            return httpx.Response(
                200,
                json={"session_id": "new-session"},
                headers={"set-cookie": "oidc_session=new-cookie; Secure; Path=/"},
            )
        assert request.headers.get("cookie") == "oidc_session=new-cookie"
        if str(request.url) == PROFILE_URL:
            return httpx.Response(200, json=profile())
        assert saved
        return httpx.Response(200, json={"receipts": [], "totalCount": 0})

    def save(value: str) -> None:
        calls.append("save")
        saved.append(value)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GlobusClient(session().serialize(), save, http=http, delay=0)
        client.page(1)
        client.page(2)
    restored = decode_session(saved[0])
    assert restored is not None and restored.cookie == "new-cookie"
    assert calls == [
        "/api/v1/oidc/session/refresh",
        "/api/v1/gsoa/customer/profiles/me/auth-status",
        "save",
        "/api/v1/gsoa/customer/receipts",
        "/api/v1/gsoa/customer/receipts",
    ]


def test_client_does_not_persist_refresh_for_another_account() -> None:
    saved: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={} if str(request.url) == REFRESH_URL else profile(99))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GlobusClient(session().serialize(), saved.append, http=http, delay=0)
        with pytest.raises(GlobusError) as error:
            client.page(1)
    assert error.value.code == "account_mismatch"
    assert saved == []


def test_client_retries_auth_failure_once_without_endless_refresh() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url) == REFRESH_URL:
            return httpx.Response(200, json={})
        if str(request.url) == PROFILE_URL:
            return httpx.Response(200, json=profile())
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GlobusClient(session().serialize(), lambda value: None, http=http, delay=0)
        with pytest.raises(GlobusError) as error:
            client.page(1)
    assert error.value.code == "auth_required"
    assert calls.count("/api/v1/oidc/session/refresh") == 2
    assert calls.count("/api/v1/gsoa/customer/receipts") == 2


def test_forbidden_receipts_with_verified_identity_do_not_require_reauthentication() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == REFRESH_URL:
            return httpx.Response(200, json={})
        if str(request.url) == PROFILE_URL:
            return httpx.Response(200, json=profile())
        return httpx.Response(403)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        pytest.raises(GlobusError) as error,
    ):
        client = GlobusClient(session().serialize(), lambda value: None, http=http, delay=0)
        client.page(1)
    assert error.value.code == "provider_unavailable"


def test_auth_recovery_has_its_own_retry_after_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [503, 503, 503, 401, 200]

    def respond(request: httpx.Request) -> httpx.Response:
        if str(request.url) == REFRESH_URL:
            return httpx.Response(200, json={})
        if str(request.url) == PROFILE_URL:
            return httpx.Response(200, json=profile())
        return httpx.Response(responses.pop(0), json={"receipts": [], "totalCount": 0})

    monkeypatch.setattr("cartalytix.providers.globus.client.time.sleep", lambda seconds: None)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = GlobusClient(session().serialize(), lambda value: None, http=http, delay=0)
        assert client.page(1).get("totalCount") == 0
    assert responses == []
