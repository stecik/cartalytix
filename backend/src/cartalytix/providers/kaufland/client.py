"""Read-only Kaufland Card adapter: cidaas e-mail login, refresh token, app transaction history.

Endpoints and the public mobile client ids were verified live on a CZ account (see experiments).
The transaction list already contains whole receipts, so details are served from the listed pages.
"""

import base64
import hashlib
import json
import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload
from cartalytix.providers.lidl.login_protocol import LoginError, Status

AUTH = "https://account.kaufland.com"
API = "https://p.crm-dynamics.schwarz"
CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"
LOYALTY_CLIENT_ID = "88207bfc-780b-400d-92ee-893ae72dab40"
REDIRECT = "com.kaufland.kaufland://oauth/callback"
OAUTH_VERSION = "1.5.22"
APP_VERSION = "6.17.1"
COUNTRY = "CZ"
LOCALE = "cs-CZ"
PAGE_SIZE = 50
LOGIN_ROUTE = "routes/$pages/login"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class KauflandError(ProviderError):
    pass


@dataclass(frozen=True)
class KauflandAccount:
    refresh_token: str
    subject: str
    email: str | None


def _client(http: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (http, False) if http is not None else (httpx.Client(timeout=30), True)


def _auth_url(location: str, base: str) -> str:
    """Redirects are only ever followed on the Kaufland identity host."""
    url = urljoin(base, location)
    if url.startswith(REDIRECT):
        return url
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "account.kaufland.com":
        raise LoginError("login_flow_incomplete")
    return url


def _token(
    http: httpx.Client, form: dict[str, str], rejected: str
) -> tuple[str, str | None, httpx.Response]:
    """Single POST; a rotating refresh is never retried because a lost response may consume it."""
    try:
        response = http.post(
            f"{AUTH}/token-srv/token",
            data={**form, "client_id": CLIENT_ID, "v": OAUTH_VERSION},
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise KauflandError("provider_unavailable") from None
    if response.status_code == 429:
        raise KauflandError("login_rate_limited")
    if response.status_code in {400, 401, 403}:
        raise KauflandError(rejected)
    if response.status_code != 200:
        raise KauflandError("provider_unavailable")
    try:
        data = response.json()
        access, refresh = data.get("access_token"), data.get("refresh_token")
    except ValueError, AttributeError:
        raise KauflandError("provider_response_invalid") from None
    if not isinstance(access, str) or not access:
        raise KauflandError("provider_response_invalid")
    return access, refresh if isinstance(refresh, str) and refresh else None, response


def _userinfo(http: httpx.Client, access: str) -> tuple[str, str | None]:
    try:
        response = http.get(
            f"{AUTH}/users-srv/userinfo",
            headers={"Accept": "application/json", "Authorization": f"Bearer {access}"},
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise KauflandError("provider_unavailable") from None
    if response.status_code in {401, 403}:
        raise KauflandError("auth_required")
    if response.status_code != 200:
        raise KauflandError("provider_unavailable")
    try:
        data = response.json()
        subject, email = data.get("sub"), data.get("email")
    except ValueError, AttributeError:
        raise KauflandError("provider_response_invalid") from None
    if not isinstance(subject, str) or not subject or len(subject) > 256:
        raise KauflandError("account_identity_unavailable")
    return subject, email if isinstance(email, str) and email else None


def exchange_refresh(refresh_token: str, *, http: httpx.Client | None = None) -> KauflandAccount:
    """Validate a fresh login: rotate once and read the account identity."""
    client, owned = _client(http)
    try:
        access, rotated, _ = _token(
            client,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            "auth_required",
        )
        subject, email = _userinfo(client, access)
    finally:
        if owned:
            client.close()
    return KauflandAccount(rotated or refresh_token, subject, email)


class KauflandLogin:
    """cidaas hosted login driven over HTTP; the password is sent only to account.kaufland.com."""

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http, self._owns_http = (
            (http, False)
            if http is not None
            else (httpx.Client(timeout=30, headers={"User-Agent": BROWSER_UA}), True)
        )
        self._verifier = secrets.token_urlsafe(64)
        self._state = secrets.token_urlsafe(32)
        self._authorize = ""
        self._page = ""
        self._form: dict[str, str] = {}
        self._refresh: str | None = None

    def close(self) -> None:
        self._form.clear()
        if self._owns_http:
            self._http.close()

    def _get(self, url: str) -> httpx.Response:
        try:
            return self._http.get(url, follow_redirects=False)
        except httpx.HTTPError:
            raise LoginError("provider_unavailable") from None

    def begin(self) -> Status:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self._verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self._authorize = f"{AUTH}/authz-srv/authz?" + urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT,
                "ui_locales": LOCALE,
                "v": OAUTH_VERSION,
                "view_type": "login",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": self._state,
            }
        )
        response = self._get(self._authorize)
        location = response.headers.get("location", "")
        request_id = (parse_qs(urlsplit(location).query).get("requestId") or [""])[0]
        if response.status_code != 302 or not request_id:
            raise LoginError("provider_unavailable")
        self._page = f"{AUTH}/pages-app/login?" + urlencode(
            {
                "requestId": request_id,
                "ui_locales": LOCALE,
                "v": OAUTH_VERSION,
                "view_type": "login",
                "username_type": "email",
            }
        )
        # Loads the session cookies the login route expects.
        self._get(self._page)
        self._form = {
            "requestId": request_id,
            "usernameType": "email",
            "verificationType": "",
            "exchangeId": "",
            "resetRequestId": "",
            "action": "login",
            "code": "",
        }
        return Status("password")

    def credentials(self, email: str, password: str) -> Status:
        if not self._form:
            raise LoginError("login_flow_incomplete")
        self._form.update({"username": email, "password": password})
        return self._submit("credentials_rejected")

    def verify(self, code: str) -> Status:
        if not self._form.get("exchangeId") and not self._form.get("verificationType"):
            raise LoginError("login_flow_incomplete")
        self._form["code"] = code
        return self._submit("otp_rejected")

    def take_refresh_token(self) -> str:
        if not self._refresh:
            raise LoginError("login_flow_incomplete")
        token, self._refresh = self._refresh, None
        return token

    def _submit(self, rejected: str) -> Status:
        try:
            response = self._http.post(
                f"{self._page}&_data={quote(LOGIN_ROUTE, safe='')}",
                data=self._form,
                headers={"Accept": "*/*", "Origin": AUTH, "Referer": self._page},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            # The login POST may have been processed; never replay credentials.
            raise LoginError("login_request_uncertain") from None
        if response.status_code == 429:
            raise LoginError("login_rate_limited")
        redirect = response.headers.get("x-remix-redirect") or response.headers.get("location")
        if redirect:
            return self._complete(_auth_url(redirect, self._page))
        try:
            result = response.json()
        except ValueError:
            result = None
        if response.status_code not in {200, 204} or not isinstance(result, dict):
            raise LoginError(rejected)
        if result.get("success") is False or result.get("error"):
            raise LoginError(rejected)
        if result.get("otpFields") or result.get("exchangeId") or result.get("verificationType"):
            values = result.get("values") if isinstance(result.get("values"), dict) else {}
            self._form.update(
                {
                    "action": str(values.get("action") or "verify"),
                    "exchangeId": str(result.get("exchangeId") or values.get("exchangeId") or ""),
                    "verificationType": str(
                        result.get("verificationType") or values.get("verificationType") or ""
                    ),
                }
            )
            return Status("otp", self._form["verificationType"] or None)
        # Some cidaas versions only issue the code when the authorize request is replayed.
        return self._complete(self._authorize)

    def _complete(self, url: str) -> Status:
        for _ in range(10):
            if url.startswith(REDIRECT):
                break
            response = self._get(url)
            location = response.headers.get("location") or response.headers.get("x-remix-redirect")
            if not location:
                raise LoginError("login_flow_incomplete")
            url = _auth_url(location, url)
        else:
            raise LoginError("login_flow_incomplete")
        params = parse_qs(urlsplit(url).query)
        if (params.get("state") or [""])[0] != self._state:
            raise LoginError("login_flow_incomplete")
        code = (params.get("code") or [""])[0]
        if "error" in params or not code:
            raise LoginError("credentials_rejected")
        try:
            _, refresh, _ = _token(
                self._http,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT,
                    "code_verifier": self._verifier,
                },
                "credentials_rejected",
            )
        except KauflandError as error:
            raise LoginError(
                "token_exchange_uncertain" if error.code == "provider_unavailable" else error.code
            ) from None
        if not refresh:
            raise LoginError("login_flow_incomplete")
        self._form.clear()
        self._refresh = refresh
        return Status("completed")


class KauflandClient:
    def __init__(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        self._refresh = refresh_token
        self._save = save_refresh
        self._access: str | None = None
        self._expires_at = 0.0
        self._auth_failed = False
        self._subject: str | None = None
        self._last_page: int | None = None
        self._receipts: dict[str, dict[str, Any]] = {}
        self._http, self._owns_http = _client(http)
        self._delay = delay
        self._heartbeat = heartbeat

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def renew(self) -> None:
        if self._auth_failed or not self._refresh:
            raise KauflandError("auth_required")
        # Once the rotating POST may have happened, a failed result must never replay it.
        self._auth_failed = True
        access, refresh, response = _token(
            self._http,
            {"grant_type": "refresh_token", "refresh_token": self._refresh},
            "auth_required",
        )
        if refresh and refresh != self._refresh:
            try:
                self._save(refresh)
            except Exception:
                raise KauflandError("auth_required") from None
            self._refresh = refresh
        self._access = access
        try:
            lifetime = float(response.json().get("expires_in") or 0)
        except ValueError, AttributeError, TypeError:
            lifetime = 0
        # Renew well before expiry; live tokens lived 24 hours.
        self._expires_at = time.time() + max(60.0, min(lifetime, 3600.0) - 300)
        self._auth_failed = False
        if self._subject is None:
            self._subject = _userinfo(self._http, access)[0]

    def _get(self, params: dict[str, str]) -> ProviderPayload:
        for attempt in range(4):
            if self._access is None or self._expires_at <= time.time():
                self.renew()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    f"{API}/api/v2/customers/{quote(self._subject or '', safe='')}/transactions",
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {self._access}",
                        "client-id": LOYALTY_CLIENT_ID,
                        "app-platform": "Android",
                        "app-version": APP_VERSION,
                    },
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise KauflandError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code in {401, 403}:
                raise KauflandError("auth_required")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise KauflandError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 2) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise KauflandError("provider_unavailable")
            try:
                result = json.loads(response.content)
            except ValueError:
                raise KauflandError("provider_response_invalid") from None
            if not isinstance(result, list):
                raise KauflandError("provider_response_invalid")
            return ProviderPayload({"transactions": result}, response.content)
        raise KauflandError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise KauflandError("provider_response_invalid")
        if self._last_page is not None and number > self._last_page:
            # The previous page was short, so the history ends there.
            return ProviderPayload({"transactions": []}, b"[]")
        data = self._get(
            {
                "start": str((number - 1) * PAGE_SIZE),
                "limit": str(PAGE_SIZE),
                "country": COUNTRY,
                "version": "2",
            }
        )
        receipts = data["transactions"]
        for receipt in receipts:
            if isinstance(receipt, dict) and isinstance(receipt.get("id"), str):
                self._receipts[receipt["id"]] = receipt
        if len(receipts) < PAGE_SIZE:
            self._last_page = number
        return data

    def detail(self, receipt_id: str) -> dict[str, Any]:
        receipt = self._receipts.get(receipt_id)
        if receipt is None:
            raise KauflandError("receipt_identity_mismatch")
        return receipt
