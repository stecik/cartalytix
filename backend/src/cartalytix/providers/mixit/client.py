"""Read-only Mixit.cz adapter; no cart, reorder or profile mutation methods."""

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload

# Customer API observed live; api.mixit.cz/api/v2 only serves public search and notices.
API = "https://admin.mixit.cz/api/v1"
SITE = "https://www.mixit.cz"


class MixitError(ProviderError):
    pass


@dataclass(frozen=True)
class MixitAccount:
    token: str
    subject: str


def _headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Language": "cs-CZ",
        "Origin": SITE,
        "Referer": SITE + "/",
    }


def sign_in(email: str, password: str, *, http: httpx.Client | None = None) -> MixitAccount:
    """Exchange credentials for the web session token; the POST is never retried."""
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            response = transport.post(
                f"{API}/sign_in",
                json={"email": email, "password": password},
                headers=_headers(),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise MixitError("provider_unavailable") from None
        if response.status_code in {400, 401, 403, 422}:
            raise MixitError("credentials_rejected")
        if response.status_code == 429:
            raise MixitError("login_rate_limited")
        if response.status_code != 200:
            raise MixitError("provider_unavailable")
        try:
            payload = response.json()
            token = payload.get("token")
            customer = payload.get("customer")
            subject = customer.get("id") if isinstance(customer, dict) else None
            if not isinstance(token, str) or not token or len(token) > 8192:
                raise ValueError
            if isinstance(subject, bool) or not isinstance(subject, int | str) or subject == "":
                raise ValueError
        except ValueError, AttributeError:
            raise MixitError("provider_response_invalid") from None
        return MixitAccount(token=token, subject=str(subject))
    finally:
        if owned:
            transport.close()


class MixitClient:
    def __init__(
        self,
        token: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 0.5,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        # The web token does not rotate; save_refresh is kept for the shared factory contract.
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        self._token = token
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
        self._delay = delay
        self._heartbeat = heartbeat

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _get(self, path: str) -> dict[str, Any]:
        if not self._token:
            raise MixitError("auth_required")
        for attempt in range(4):
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    f"{API}/{path}",
                    headers={**_headers(), "Token": self._token},
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise MixitError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code in {401, 403}:
                raise MixitError("auth_required")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise MixitError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                wait = min(60, max(self._delay, 1) * (2**attempt))
                retry = response.headers.get("Retry-After", "")
                if retry:
                    try:
                        wait = float(retry)
                    except ValueError:
                        raise MixitError("rate_limited") from None
                    if not math.isfinite(wait) or not 0 <= wait <= 60:
                        raise MixitError("rate_limited")
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise MixitError("provider_unavailable")
            try:
                result = json.loads(response.content, parse_float=Decimal)
            except ValueError:
                raise MixitError("provider_response_invalid") from None
            if not isinstance(result, dict):
                raise MixitError("provider_response_invalid")
            return ProviderPayload(result, response.content)
        raise MixitError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise MixitError("provider_response_invalid")
        return self._get(f"customer/orders?page={number}")

    def detail(self, order_hash: str) -> dict[str, Any]:
        if not order_hash:
            raise MixitError("receipt_identity_mismatch")
        return self._get("customer/orders/" + quote(order_hash, safe=""))
