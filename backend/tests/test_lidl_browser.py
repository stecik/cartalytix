from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from cartalytix.providers.lidl.browser import (
    REDIRECT,
    authorization_url,
    callback_code,
    link_connection,
    server_origin,
)
from cartalytix.providers.lidl.client import LidlError


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com/?token=secret",
        "https://example.com/#secret",
        "ftp://localhost",
        "http://localhost:wrong",
        "https://example.com:99999",
        "https://",
    ],
)
def test_server_rejects_unsafe_credentials_destination(value: str) -> None:
    with pytest.raises(ValueError):
        server_origin(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://localhost:18080/", "http://localhost:18080"),
        ("http://127.0.0.1:18080", "http://127.0.0.1:18080"),
        ("http://[::1]:18080/", "http://[::1]:18080"),
        ("https://APP.example.com/", "https://app.example.com"),
    ],
)
def test_server_accepts_local_or_verified_https_origin(value: str, expected: str) -> None:
    assert server_origin(value) == expected


@pytest.mark.parametrize(
    "callback",
    [
        f"{REDIRECT}?code=code&state=wrong",
        f"{REDIRECT}?code=code&state=state&state=state",
        f"{REDIRECT}?code=a&code=b&state=state",
        f"{REDIRECT}?code=&state=state",
        f"{REDIRECT}?code=a&state=state&error=denied",
        f"{REDIRECT}?code=a&state=state#fragment",
        "com.lidlplus.app://callback.evil?code=a&state=state",
        "com.lidlplus.app://callback/path?code=a&state=state",
    ],
)
def test_oauth_callback_is_bound_to_exact_target_and_one_state(callback: str) -> None:
    with pytest.raises(LidlError, match="authorization_callback_invalid"):
        callback_code(callback, "state")


def test_pkce_and_valid_callback() -> None:
    query = parse_qs(urlsplit(authorization_url("verifier", "state")).query)
    assert query.get("state") == ["state"]
    assert query.get("code_challenge_method") == ["S256"]
    assert query.get("code_challenge") != ["verifier"]
    assert callback_code(f"{REDIRECT}?code=one-time&state=state", "state") == "one-time"


def test_local_handoff_limits_secrets_to_correct_origins_and_logs_out() -> None:
    identifier = uuid4()
    calls: list[str] = []

    def application(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "localhost"
        assert request.headers.get("Origin") == "http://localhost:18080"
        calls.append(request.url.path)
        if request.url.path.endswith("login"):
            assert b"app-password" in request.content
            return httpx.Response(200, json={}, headers={"Set-Cookie": "session=local; Path=/"})
        if request.url.path.endswith("connections"):
            return httpx.Response(
                200, json={"items": [{"id": str(identifier), "provider": "lidl"}]}
            )
        if request.url.path.endswith("authorize"):
            assert request.content == b'{"refresh_token":"fresh-refresh"}'
            assert "app-password" not in request.content.decode()
            return httpx.Response(200, json={})
        assert request.url.path.endswith("logout")
        return httpx.Response(204)

    def token(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://accounts.lidl.com/connect/token"
        assert b"code=one-time" in request.content
        assert b"code_verifier=" in request.content
        assert b"app-password" not in request.content
        return httpx.Response(200, json={"refresh_token": "fresh-refresh"})

    def browser(url: str, state: str) -> str:
        assert parse_qs(urlsplit(url).query).get("state") == [state]
        return "one-time"

    with httpx.Client(
        base_url="http://localhost:18080", transport=httpx.MockTransport(application)
    ) as app:
        with httpx.Client(transport=httpx.MockTransport(token)) as provider:
            link_connection(
                "http://localhost:18080",
                identifier,
                "owner@example.com",
                "app-password",
                http=app,
                token_http=provider,
                open_browser=browser,
            )
        assert not list(app.cookies)
    assert calls[-2:] == [
        f"/api/v1/providers/lidl/connections/{identifier}/authorize",
        "/api/v1/auth/logout",
    ]


def test_uncertain_authorization_post_never_retries_and_redacts_exception() -> None:
    identifier = uuid4()
    posts: list[str] = []

    def application(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("connections"):
            return httpx.Response(
                200, json={"items": [{"id": str(identifier), "provider": "lidl"}]}
            )
        return httpx.Response(200, json={})

    def token(request: httpx.Request) -> httpx.Response:
        posts.append(str(request.url))
        raise httpx.ReadTimeout("secret-authorization-code")

    with (
        httpx.Client(
            base_url="http://localhost", transport=httpx.MockTransport(application)
        ) as app,
        httpx.Client(transport=httpx.MockTransport(token)) as provider,
        pytest.raises(LidlError, match="^authorization_exchange_failed$"),
    ):
        link_connection(
            "http://localhost",
            identifier,
            "owner@example.com",
            "password",
            http=app,
            token_http=provider,
            open_browser=lambda url, state: "code",
        )
    assert len(posts) == 1
