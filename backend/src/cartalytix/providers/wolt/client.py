"""Read-only Wolt adapter: pasted magic-link sign-in, rotating refresh token, order history."""

import base64
import binascii
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload, auth_diagnostic

AUTH = "https://authentication.wolt.com"
API = "https://consumer-api.wolt.com/order-tracking-api/v1/order_history"
PAGE_SIZE = 50
# English formatting matches the live-verified responses; dates are read from timestamps.
HEADERS = {"Accept": "application/json", "platform": "Web", "app-language": "en"}


class WoltError(ProviderError):
    pass


@dataclass(frozen=True)
class WoltAccount:
    refresh_token: str
    subject: str
    email: str | None


def parse_magic_link(link: str) -> str:
    """Return the token from the sign-in link the owner requested in their own browser.

    Wolt silently drops sign-in e-mails requested from automated clients, so the app never
    requests the link itself; it only exchanges a link pasted back by the owner. The link's
    e-mail parameter is masked by Wolt, so identity comes from the token response instead.
    """
    parsed = urlsplit(link.strip())
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "wolt.com" or host.endswith(".wolt.com")):
        raise WoltError("magic_link_invalid")
    token = (parse_qs(parsed.query).get("token") or [""])[0]
    if not token or len(token) > 512 or any(c.isspace() for c in token):
        raise WoltError("magic_link_invalid")
    return token


def _user_claims(access_token: str) -> dict[str, Any]:
    # Only ever called on the fixed authentication.wolt.com response, never on user input.
    parts = access_token.split(".")
    if len(parts) != 3:
        return {}
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError, UnicodeError, binascii.Error:
        return {}
    user = claims.get("user") if isinstance(claims, dict) else None
    return user if isinstance(user, dict) else {}


def _client(http: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (http, False) if http is not None else (httpx.Client(timeout=30), True)


def _tokens(response: httpx.Response, rejected: str, phase: str = "login") -> tuple[str, str]:
    if response.status_code == 429:
        raise WoltError("login_rate_limited", diagnostic=auth_diagnostic(phase, response=response))
    if response.status_code in {400, 401, 403}:
        raise WoltError(rejected, diagnostic=auth_diagnostic(phase, response=response))
    if response.status_code != 200:
        raise WoltError(
            "provider_unavailable", diagnostic=auth_diagnostic(phase, response=response)
        )
    try:
        data = response.json()
        access, refresh = data.get("access_token"), data.get("refresh_token")
    except ValueError, AttributeError:
        raise WoltError(
            "provider_response_invalid", diagnostic=auth_diagnostic(phase, response=response)
        ) from None
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        # Includes Wolt asking for an extra access confirmation the app cannot complete.
        raise WoltError(
            "access_confirmation_required", diagnostic=auth_diagnostic(phase, response=response)
        )
    return access, refresh


def exchange_magic_link(token: str, *, http: httpx.Client | None = None) -> WoltAccount:
    """Exchange a magic-link token; one-time tokens are never retried."""
    client, owned = _client(http)
    try:
        try:
            response = client.post(
                f"{AUTH}/v1/wauth2/access_token",
                data={
                    "grant_type": "email_login",
                    "token": token,
                    "audience": "wolt-com",
                    "capabilities": "access_confirmation",
                },
                headers=HEADERS,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise WoltError(
                "provider_unavailable", diagnostic=auth_diagnostic("login", error=error)
            ) from None
    finally:
        if owned:
            client.close()
    access, refresh = _tokens(response, "magic_link_rejected")
    user = _user_claims(access)
    subject, email = user.get("id"), user.get("email")
    if not isinstance(subject, str) or not subject:
        raise WoltError(
            "account_identity_unavailable", diagnostic=auth_diagnostic("login", response=response)
        )
    return WoltAccount(
        refresh_token=refresh,
        subject=subject,
        email=email if isinstance(email, str) and email else None,
    )


class WoltClient:
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
        self._http, self._owns_http = _client(http)
        self._delay = delay
        self._heartbeat = heartbeat

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def renew(self) -> None:
        if self._auth_failed or not self._refresh:
            raise WoltError("auth_required")
        # Once the rotating POST may have happened, a failed result must never replay it.
        self._auth_failed = True
        try:
            response = self._http.post(
                f"{AUTH}/v1/wauth2/access_token",
                data={"grant_type": "refresh_token", "refresh_token": self._refresh},
                headers=HEADERS,
                follow_redirects=False,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            # No token request was sent; the next scheduled sync can safely try again.
            raise WoltError(
                "provider_unavailable", diagnostic=auth_diagnostic("refresh", error=error)
            ) from None
        except httpx.HTTPError as error:
            raise WoltError(
                "auth_required", diagnostic=auth_diagnostic("refresh", error=error)
            ) from None
        access, refresh = _tokens(response, "auth_required", "refresh")
        if refresh != self._refresh:
            try:
                self._save(refresh)
            except Exception:
                raise WoltError(
                    "auth_required",
                    diagnostic={"phase": "save_refresh", "reason": "persistence_failed"},
                ) from None
            self._refresh = refresh
        self._access = access
        # Wolt access tokens live about 30 minutes; renew well before expiry.
        self._expires_at = time.time() + 20 * 60
        self._auth_failed = False

    def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        phase = "history" if url == f"{API}/" else "detail"
        for attempt in range(4):
            if self._access is None or self._expires_at <= time.time():
                self.renew()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    url,
                    params=params,
                    headers={**HEADERS, "Authorization": f"Bearer {self._access}"},
                    follow_redirects=False,
                )
            except httpx.HTTPError as error:
                if attempt == 3:
                    raise WoltError(
                        "provider_unavailable", diagnostic=auth_diagnostic(phase, error=error)
                    ) from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code in {401, 403}:
                raise WoltError(
                    "auth_required", diagnostic=auth_diagnostic(phase, response=response)
                )
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise WoltError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable",
                        diagnostic=auth_diagnostic(phase, response=response),
                    )
                time.sleep(min(60, max(self._delay, 2) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise WoltError(
                    "provider_unavailable", diagnostic=auth_diagnostic(phase, response=response)
                )
            try:
                result = json.loads(response.content)
            except ValueError:
                raise WoltError(
                    "provider_response_invalid",
                    diagnostic=auth_diagnostic(phase, response=response),
                ) from None
            if not isinstance(result, dict):
                raise WoltError(
                    "provider_response_invalid",
                    diagnostic=auth_diagnostic(phase, response=response),
                )
            return ProviderPayload(result, response.content)
        raise WoltError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise WoltError("provider_response_invalid")
        if number > 1:
            # One page holds the whole history unless it is full (see below).
            return ProviderPayload({"orders": []}, b'{"orders":[]}')
        data = self._get(f"{API}/", {"limit": str(PAGE_SIZE)})
        orders = data.get("orders")
        if isinstance(orders, list) and len(orders) >= PAGE_SIZE:
            # Paging past 50 orders is unverified; never claim a full page is the whole history.
            raise WoltError("history_incomplete")
        return data

    def detail(self, purchase_id: str) -> dict[str, Any]:
        if not purchase_id:
            raise WoltError("receipt_identity_mismatch")
        return self._get(
            f"{API}/purchase/{quote(purchase_id, safe='')}", {"tips_use_percentage": "true"}
        )
