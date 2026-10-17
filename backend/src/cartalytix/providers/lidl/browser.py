"""Local Chrome OAuth handoff; credentials stay in memory or on Lidl's login page."""

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import httpx

from cartalytix.i18n import translate as _
from cartalytix.providers.lidl.client import AUTH, CLIENT_ID, LidlError

REDIRECT = "com.lidlplus.app://callback"


def server_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(_("Use an HTTPS application origin or HTTP localhost"))
    port = parsed.port
    try:
        loopback = ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if parsed.scheme == "http" and not loopback:
        raise ValueError(_("Use an HTTPS application origin or HTTP localhost"))
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{host}{suffix}"


def callback_code(value: str, expected_state: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme, parsed.netloc, parsed.path) != ("com.lidlplus.app", "callback", ""):
        raise LidlError("authorization_callback_invalid")
    params = parse_qs(parsed.query, keep_blank_values=True)
    states, codes = params.get("state", []), params.get("code", [])
    if (
        parsed.fragment
        or len(states) != 1
        or not hmac.compare_digest(states[0].encode(), expected_state.encode())
        or "error" in params
        or len(codes) != 1
        or not codes[0]
    ):
        raise LidlError("authorization_callback_invalid")
    return codes[0]


def authorization_url(verifier: str, state: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return f"{AUTH}/connect/authorize?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT,
            "scope": "openid profile offline_access lpprofile lpapis",
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "state": state,
            "nonce": secrets.token_urlsafe(32),
            "Country": "CZ",
            "language": "cs-CZ",
        }
    )


def browser_code(url: str, state: str, *, timeout: float = 600) -> str:
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise LidlError("browser_extra_required") from None
    captured: list[str] = []

    def capture(value: str) -> None:
        if value.startswith(REDIRECT) and not captured:
            captured.append(value)

    def request_event(event: dict[str, Any]) -> None:
        capture(event.get("request", {}).get("url", ""))

    def header_event(event: dict[str, Any]) -> None:
        for name, value in event.get("headers", {}).items():
            if name.lower() == "location":
                capture(value)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            try:
                context = browser.new_context(locale="cs-CZ")
                page = context.new_page()
                page.on("request", lambda request: capture(request.url))
                page.on("response", lambda response: capture(response.headers.get("location", "")))
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.on("Network.requestWillBeSent", request_event)
                cdp.on("Network.responseReceivedExtraInfo", header_event)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                except BrowserError:
                    if not captured:
                        raise LidlError("browser_login_failed") from None
                print(_("Sign in only in the Lidl browser window; never paste passwords or codes."))
                deadline = time.monotonic() + timeout
                while not captured and time.monotonic() < deadline:
                    if page.is_closed():
                        raise LidlError("browser_login_cancelled")
                    page.wait_for_timeout(200)
                if not captured:
                    raise LidlError("browser_login_timeout")
                return callback_code(captured[0], state)
            finally:
                browser.close()
    except BrowserError:
        raise LidlError("browser_login_failed") from None


def _api(
    http: httpx.Client, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        response = http.request(method, path, json=payload, follow_redirects=False)
    except httpx.HTTPError:
        raise LidlError("application_unavailable") from None
    if response.status_code not in {200, 201, 202}:
        # Do not print response bodies/URLs containing sensitive upstream context.
        raise LidlError(
            "application_login_failed"
            if response.status_code == 401
            else "application_request_failed"
        )
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise LidlError("application_response_invalid") from None


def link_connection(
    server: str,
    identifier: UUID,
    email: str,
    password: str,
    *,
    http: httpx.Client | None = None,
    token_http: httpx.Client | None = None,
    open_browser: Callable[[str, str], str] = browser_code,
) -> None:
    origin = server_origin(server)
    owns_app, owns_token = http is None, token_http is None
    app = (
        http
        if http is not None
        else httpx.Client(base_url=origin, timeout=30, follow_redirects=False)
    )
    provider = (
        token_http if token_http is not None else httpx.Client(timeout=30, follow_redirects=False)
    )
    app.headers["Origin"] = origin
    authenticated = False
    try:
        _api(app, "POST", "/api/v1/auth/login", {"email": email, "password": password})
        authenticated = True
        connections = _api(app, "GET", "/api/v1/connections")
        items = connections.get("items", []) if isinstance(connections, dict) else []
        if not isinstance(items, list) or not any(
            isinstance(item, dict)
            and item.get("id") == str(identifier)
            and item.get("provider") == "lidl"
            for item in items
        ):
            raise LidlError("connection_not_found")
        verifier, state = secrets.token_urlsafe(64), secrets.token_urlsafe(32)
        code = open_browser(authorization_url(verifier, state), state)
        try:
            response = provider.post(
                f"{AUTH}/connect/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "code_verifier": verifier,
                },
                auth=(CLIENT_ID, "secret"),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise LidlError("authorization_exchange_failed") from None
        if response.status_code != 200:
            raise LidlError("authorization_exchange_failed")
        try:
            payload = response.json()
            refresh = payload.get("refresh_token") if isinstance(payload, dict) else None
        except ValueError:
            raise LidlError("authorization_exchange_failed") from None
        if not isinstance(refresh, str) or not refresh:
            raise LidlError("authorization_exchange_failed")
        _api(
            app,
            "POST",
            f"/api/v1/providers/lidl/connections/{identifier}/authorize",
            {"refresh_token": refresh},
        )
    finally:
        if authenticated:
            with suppress(httpx.HTTPError):
                app.post("/api/v1/auth/logout", follow_redirects=False)
        app.cookies.clear()
        if owns_app:
            app.close()
        if owns_token:
            provider.close()
