"""Isolated Globus login/refresh experiment; passwords are never stored."""

import argparse
import json
import logging
import math
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, replace
from getpass import getpass
from gettext import gettext as _
from typing import Any
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, Request, Response, Route, sync_playwright

from cartalytix.export import ExportError

from .auth import Vault

SITE = "https://www.globus.cz"
PROFILE = f"{SITE}/api/v1/gsoa/customer/profiles/me/auth-status"
REFRESH = f"{SITE}/api/v1/oidc/session/refresh"
COOKIE = "oidc_session"
COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,4096}")
LOGIN_HOSTS = frozenset(
    {"www.globus.cz", "ucet.globus.cz", "api.ucet.globus.cz", "fidm.eu1.gigya.com"}
)
ASSET_HOSTS = frozenset(
    {
        "cdns.eu1.gigya.com",
        "cdns.gigya.com",
        "consent.cookiebot.com",
        "consentcdn.cookiebot.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
    }
)
CAPTCHA_HOSTS = frozenset({"www.google.com", "www.recaptcha.net", "www.gstatic.com"})


class ExperimentError(ExportError):
    """Only fixed diagnostic codes, never upstream bodies or exception messages."""


@dataclass(frozen=True, repr=False)
class Session:
    cookie: str
    session_id: str
    subject: str
    user_agent: str
    cookie_expires: float | None

    def payload(self) -> dict[str, Any]:
        return {
            "session_cookie": self.cookie,
            "session_id": self.session_id,
            "subject": self.subject,
            "user_agent": self.user_agent,
            "cookie_expires": self.cookie_expires,
        }


class SessionVault(Vault):
    service = "Cartalytix.Globus.Headless"

    def save(self, value: dict[str, Any]) -> None:
        kept = {
            key: value.get(key)
            for key in (
                "session_cookie",
                "session_id",
                "subject",
                "user_agent",
                "cookie_expires",
            )
        }
        self.backend.set_password(self.service, self.account, json.dumps(kept))


def _expiry(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def load_session(vault: SessionVault) -> Session:
    stored = vault.load() or {}
    values = [
        stored.get(key)
        for key in ("session_cookie", "session_id", "subject", "user_agent")
    ]
    if not all(
        isinstance(value, str) and value and "\r" not in value and "\n" not in value
        for value in values
    ):
        raise ExperimentError("stored_session_invalid_or_missing")
    cookie, session_id, subject, user_agent = (str(value) for value in values)
    if not COOKIE_VALUE.fullmatch(cookie) or not COOKIE_VALUE.fullmatch(session_id):
        raise ExperimentError("stored_session_invalid_or_missing")
    return Session(
        cookie, session_id, subject, user_agent, _expiry(stored.get("cookie_expires"))
    )


def _headers(session: Session) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Cookie": f"{COOKIE}={session.cookie}",
        "User-Agent": session.user_agent,
        "Origin": SITE,
        "Referer": f"{SITE}/",
    }


def _json(response: httpx.Response, stage: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise ExperimentError(f"{stage}_http_{response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise ExperimentError(f"{stage}_response_invalid") from None
    if not isinstance(data, dict):
        raise ExperimentError(f"{stage}_response_invalid")
    return data


def identity(session: Session, http: httpx.Client) -> str | None:
    try:
        response = http.get(PROFILE, headers=_headers(session), follow_redirects=False)
    except httpx.HTTPError:
        raise ExperimentError("identity_network_error") from None
    if response.status_code == 401:
        return None
    data = _json(response, "identity")
    if data.get("authenticated") is False:
        return None
    if data.get("authenticated") is not True:
        raise ExperimentError("identity_response_invalid")
    profile = data.get("profile")
    registration = profile.get("registration") if isinstance(profile, dict) else None
    subject = registration.get("customerID") if isinstance(registration, dict) else None
    if (
        isinstance(subject, bool)
        or not isinstance(subject, int | str)
        or not str(subject).strip()
    ):
        raise ExperimentError("identity_unavailable")
    return str(subject)


def refresh_session(
    session: Session, http: httpx.Client
) -> tuple[Session, dict[str, object]]:
    try:
        response = http.post(
            REFRESH,
            json={"session_id": session.session_id},
            headers=_headers(session),
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise ExperimentError("refresh_network_error") from None
    data = _json(response, "refresh")
    session_id = data.get("session_id", session.session_id)
    if not isinstance(session_id, str) or not COOKIE_VALUE.fullmatch(session_id):
        raise ExperimentError("refresh_session_id_invalid")
    cookies = [
        cookie
        for cookie in response.cookies.jar
        if cookie.name == COOKIE
        and cookie.domain.lstrip(".") in {"globus.cz", "www.globus.cz"}
        and cookie.path == "/"
    ]
    if len(cookies) > 1:
        raise ExperimentError("refresh_cookie_ambiguous")
    cookie = cookies[0].value if cookies else session.cookie
    if not isinstance(cookie, str) or not COOKIE_VALUE.fullmatch(cookie):
        raise ExperimentError("refresh_cookie_invalid")
    updated = replace(
        session,
        cookie=cookie,
        session_id=session_id,
        cookie_expires=_expiry(cookies[0].expires)
        if cookies
        else session.cookie_expires,
    )
    subject = identity(updated, http)
    if subject is None:
        raise ExperimentError("refresh_not_authenticated")
    if subject != session.subject:
        raise ExperimentError("account_identity_mismatch")
    return updated, {
        "authenticated": True,
        "refresh_attempted": True,
        "refresh_http_status": response.status_code,
        "cookie_rotated": updated.cookie != session.cookie,
        "session_id_rotated": updated.session_id != session.session_id,
        "cookie_expires": updated.cookie_expires,
    }


def check_session(
    session: Session, http: httpx.Client
) -> tuple[Session, dict[str, object]]:
    subject = identity(session, http)
    if subject is None:
        return refresh_session(session, http)
    if subject != session.subject:
        raise ExperimentError("account_identity_mismatch")
    return session, {
        "authenticated": True,
        "refresh_attempted": False,
        "cookie_expires": session.cookie_expires,
    }


def trusted_navigation(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in LOGIN_HOSTS
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def allowed_request(url: str, method: str) -> bool:
    if trusted_navigation(url):
        return True
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        if parsed.hostname in CAPTCHA_HOSTS and parsed.path.startswith("/recaptcha/"):
            return True
        return parsed.hostname in ASSET_HOSTS and method in {"GET", "HEAD"}
    except ValueError:
        return False


def guard_request(route: Route) -> None:
    request = route.request
    if not allowed_request(request.url, request.method):
        route.abort()
        return
    if request.method == "POST" and urlsplit(request.url).path == "/accounts.login":
        # Never let a 307/308 forward the password to another destination.
        try:
            response = route.fetch(max_redirects=0, timeout=30000)
        except BrowserError:
            route.abort()
            return
        try:
            if 300 <= response.status < 400:
                route.abort()
            else:
                route.fulfill(response=response)
        finally:
            response.dispose()
        return
    route.continue_()


def submit_login(
    page: Page, username: str, password: str, timeout: float
) -> tuple[str, float | None]:
    pending: deque[Response] = deque()
    failures: list[str] = []

    def failed(request: Request) -> None:
        parsed = urlsplit(request.url)
        if (
            parsed.hostname in {"api.ucet.globus.cz", "ucet.globus.cz"}
            and parsed.path == "/accounts.login"
        ):
            failures.append("login_request_failed")

    def observe(request: Request) -> None:
        parsed = urlsplit(request.url)
        if (
            parsed.hostname not in {"api.ucet.globus.cz", "ucet.globus.cz"}
            or parsed.path != "/accounts.login"
        ):
            return
        response = request.response()
        if response is not None:
            pending.append(response)

    deadline = time.monotonic() + timeout
    page.on("requestfinished", observe)
    page.on("requestfailed", failed)
    try:
        field = page.locator("input[type=password]:visible").first
        field.wait_for(timeout=timeout * 1000)
        parsed = urlsplit(page.url)
        if not trusted_navigation(page.url) or (parsed.hostname, parsed.path) != (
            "ucet.globus.cz",
            "/prihlaseni/login",
        ):
            raise ExperimentError("login_origin_untrusted")
        form = field.locator("xpath=ancestor::form")
        form.locator("input[name=username],input[type=email]").first.fill(
            username, timeout=max(1, (deadline - time.monotonic()) * 1000)
        )
        field.fill(password, timeout=max(1, (deadline - time.monotonic()) * 1000))
        form.locator("button[type=submit],input[type=submit]").first.click(
            timeout=max(1, (deadline - time.monotonic()) * 1000)
        )
        while time.monotonic() < deadline:
            if failures:
                raise ExperimentError(failures[0])
            while pending:
                response = pending.popleft()
                if response.status != 200:
                    raise ExperimentError(f"login_http_{response.status}")
                try:
                    data = response.json()
                except (ValueError, BrowserError):
                    raise ExperimentError("login_response_invalid") from None
                code = data.get("errorCode") if isinstance(data, dict) else None
                if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                    raise ExperimentError(f"gigya_login_error_{code}")
            cookies = [
                raw for raw in page.context.cookies(SITE) if raw.get("name") == COOKIE
            ]
            if len(cookies) == 1:
                cookie = cookies[0].get("value", "")
                if not COOKIE_VALUE.fullmatch(cookie):
                    raise ExperimentError("login_cookie_invalid")
                return cookie, _expiry(cookies[0].get("expires"))
            if len(cookies) > 1:
                raise ExperimentError("login_cookie_ambiguous")
            page.wait_for_timeout(200)
        raise ExperimentError("login_timeout_or_interactive_challenge")
    finally:
        page.remove_listener("requestfinished", observe)
        page.remove_listener("requestfailed", failed)


def login(
    username: str, password: str, *, channel: str = "chrome", timeout: float = 90
) -> Session:
    if not username.strip() or not password:
        raise ExperimentError("username_and_password_required")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=None if channel == "chromium" else channel,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                blank = browser.new_page()
                user_agent = str(blank.evaluate("navigator.userAgent")).replace(
                    "HeadlessChrome/", "Chrome/"
                )
                blank.close()
                context = browser.new_context(
                    locale="cs-CZ",
                    user_agent=user_agent,
                    accept_downloads=False,
                    service_workers="block",
                )
                context.route("**/*", guard_request)
                context.route_web_socket("**/*", lambda socket: socket.close())
                response = context.request.get(
                    f"{SITE}/api/v1/oidc/authorize",
                    params={"redirect_url": "/callbacks/login", "prompt": "login"},
                    max_redirects=0,
                    timeout=30000,
                )
                if response.status != 200:
                    raise ExperimentError(f"authorize_http_{response.status}")
                try:
                    data = response.json()
                except ValueError:
                    raise ExperimentError("authorize_response_invalid") from None
                url = data.get("authorize_url") if isinstance(data, dict) else None
                session_id = data.get("session_id") if isinstance(data, dict) else None
                if not isinstance(url, str) or not trusted_navigation(url):
                    raise ExperimentError("authorize_origin_untrusted")
                if not isinstance(session_id, str) or not COOKIE_VALUE.fullmatch(
                    session_id
                ):
                    raise ExperimentError("authorize_session_id_invalid")
                page = context.new_page()
                page.set_default_timeout(timeout * 1000)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                cookie, expires = submit_login(
                    page, username.strip(), password, timeout
                )
            finally:
                browser.close()
    except BrowserError:
        raise ExperimentError("browser_unavailable_or_login_failed") from None
    candidate = Session(cookie, session_id, "", user_agent, expires)
    with httpx.Client(timeout=30) as http:
        subject = identity(candidate, http)
    if subject is None:
        raise ExperimentError("login_not_authenticated")
    return replace(candidate, subject=subject)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=_("Experimental Globus headless login and session refresh")
    )
    parser.add_argument("command", choices=["login", "status", "refresh", "logout"])
    parser.add_argument(
        "--browser", choices=["chrome", "chromium", "msedge"], default="chrome"
    )
    parser.add_argument(
        "--timeout", type=float, default=90, help=_("Login timeout in seconds (5-300)")
    )
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 5 <= args.timeout <= 300:
        parser.error(_("Timeout must be between 5 and 300 seconds"))
    logging.disable(logging.CRITICAL)
    try:
        vault = SessionVault()
        if args.command == "logout":
            vault.clear()
            print(_("Local experimental session deleted; Globus session not revoked."))
            return
        if args.command == "login":
            if not sys.stdin.isatty():
                raise ExperimentError("interactive_terminal_required")
            username = input(_("Globus email or card number: ")).strip()
            password = getpass(_("Globus password (not stored): "))
            print(_("Signing in without opening a browser window..."), flush=True)
            try:
                session = login(
                    username, password, channel=args.browser, timeout=args.timeout
                )
            finally:
                password = ""
            report: dict[str, object] = {
                "authenticated": True,
                "cookie_expires": session.cookie_expires,
                "long_term_refresh_verified": False,
            }
        else:
            session = load_session(vault)
            with httpx.Client(timeout=30) as http:
                operation = (
                    refresh_session if args.command == "refresh" else check_session
                )
                session, report = operation(session, http)
        vault.save(session.payload())
        print(json.dumps(report, indent=2, allow_nan=False))
    except ExperimentError as error:
        print(
            _("Globus experiment failed: {code}").format(code=str(error)),
            file=sys.stderr,
        )
        sys.exit(1)
    except KeyboardInterrupt:
        print(_("Cancelled."), file=sys.stderr)
        sys.exit(130)
    except Exception:
        print(
            _("Experiment failed; no credentials or provider response were logged."),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
