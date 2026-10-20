"""Bounded, isolated Globus browser login; credentials never persisted."""

import math
import re
import time
from collections import deque
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError
from playwright.sync_api import Page, Request, Response, Route, sync_playwright
from playwright.sync_api import TimeoutError as BrowserTimeoutError

from .session import GlobusError, GlobusSession, fetch_identity

SITE = "https://www.globus.cz"
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


_LOGIN_SLOTS = BoundedSemaphore(1)


def _expiry(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


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


def _login_error(code: int) -> str:
    if code == 403042:
        return "credentials_rejected"
    if code in {403041, 403120}:
        return "account_locked"
    if code in {400021, 401020, 401021}:
        return "captcha_required"
    return "login_failed"


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
            failures.append("provider_unavailable")

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
            raise GlobusError("login_failed")
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
                raise GlobusError(failures[0])
            while pending:
                response = pending.popleft()
                if response.status != 200:
                    raise GlobusError("provider_unavailable")
                try:
                    data = response.json()
                except ValueError, BrowserError:
                    raise GlobusError("provider_response_invalid") from None
                code = data.get("errorCode") if isinstance(data, dict) else None
                if isinstance(code, bool) or not isinstance(code, int):
                    raise GlobusError("provider_response_invalid")
                if code != 0:
                    raise GlobusError(_login_error(code))
            cookies = [
                raw
                for raw in page.context.cookies(SITE)
                if raw.get("name") == COOKIE
                and raw.get("domain", "").lstrip(".") in {"globus.cz", "www.globus.cz"}
                and raw.get("path") == "/"
            ]
            if len(cookies) == 1:
                cookie = cookies[0].get("value", "")
                if not COOKIE_VALUE.fullmatch(cookie):
                    raise GlobusError("provider_response_invalid")
                return cookie, _expiry(cookies[0].get("expires"))
            if len(cookies) > 1:
                raise GlobusError("provider_response_invalid")
            page.wait_for_timeout(200)
        raise GlobusError("login_timeout")
    finally:
        page.remove_listener("requestfinished", observe)
        page.remove_listener("requestfailed", failed)


def login(
    username: str, password: str, *, timeout: float = 90, channel: str = "chrome"
) -> GlobusSession:
    if not username.strip() or not password:
        raise GlobusError("credentials_rejected")
    if not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise GlobusError("login_failed")
    if not _LOGIN_SLOTS.acquire(timeout=1):
        raise GlobusError("login_busy")
    try:
        return _browser_login(username.strip(), password, timeout=timeout, channel=channel)
    finally:
        _LOGIN_SLOTS.release()


def _browser_login(username: str, password: str, *, timeout: float, channel: str) -> GlobusSession:
    deadline = time.monotonic() + timeout

    def remaining_ms() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GlobusError("login_timeout")
        return remaining * 1000

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel=None if channel == "chromium" else channel,
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    timeout=min(30000, remaining_ms()),
                )
            except BrowserError:
                raise GlobusError("browser_unavailable") from None
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
                    timeout=min(30000, remaining_ms()),
                )
                try:
                    if response.status != 200:
                        raise GlobusError("provider_unavailable")
                    try:
                        data = response.json()
                    except ValueError:
                        raise GlobusError("provider_response_invalid") from None
                finally:
                    response.dispose()
                url = data.get("authorize_url") if isinstance(data, dict) else None
                session_id = data.get("session_id") if isinstance(data, dict) else None
                if not isinstance(url, str) or not trusted_navigation(url):
                    raise GlobusError("provider_response_invalid")
                if not isinstance(session_id, str) or not COOKIE_VALUE.fullmatch(session_id):
                    raise GlobusError("provider_response_invalid")
                page = context.new_page()
                page.set_default_timeout(remaining_ms())
                page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms())
                cookie, expires = submit_login(page, username, password, remaining_ms() / 1000)
            finally:
                browser.close()
    except BrowserTimeoutError:
        raise GlobusError("login_timeout") from None
    except BrowserError:
        raise GlobusError("login_failed") from None
    try:
        account = fetch_identity(cookie, user_agent=user_agent)
    except GlobusError as error:
        if error.code == "session_cookie_rejected":
            raise GlobusError("login_failed") from None
        raise
    return GlobusSession(
        cookie=cookie,
        session_id=session_id,
        subject=account.subject,
        user_agent=user_agent,
        cookie_expires=expires,
        email=account.email,
    )
