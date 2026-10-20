"""Read-only foodora.cz adapter: pasted access token, offset-paged order history.

The web login and every token refresh endpoint sit behind PerimeterX bot protection, so the
server cannot sign in or rotate tokens. The owner pastes the browser session's access token;
the order history API accepts it until it expires, then the connection needs re-linking.
"""

import base64
import binascii
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload

HISTORY = "https://cz.fd-api.com/api/v5/orders/order_history"
# Public web client key, sent by the foodora web app on every API request.
API_KEY = "volo"
PAGE_SIZE = 20


class FoodoraError(ProviderError):
    pass


@dataclass(frozen=True)
class FoodoraAccount:
    credential: str
    subject: str


def _claims(access_token: str) -> dict[str, Any]:
    parts = access_token.split(".")
    if len(parts) != 3:
        return {}
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError, UnicodeError, binascii.Error:
        return {}
    return claims if isinstance(claims, dict) else {}


def _expired(claims: dict[str, Any]) -> bool:
    expires = claims.get("expires")
    if isinstance(expires, bool) or not isinstance(expires, int | float):
        return False
    # The claim is a Unix timestamp; tolerate milliseconds.
    return (expires / 1000 if expires > 1e12 else expires) <= time.time()


def _get(http: httpx.Client, access_token: str, offset: int, limit: int) -> httpx.Response:
    return http.get(
        HISTORY,
        params={
            "include": "order_products,order_details",
            "offset": str(offset),
            "limit": str(limit),
            "language_id": "3",
        },
        headers={
            "Accept": "application/json",
            "X-FP-API-KEY": API_KEY,
            "Authorization": f"Bearer {access_token}",
        },
        follow_redirects=False,
    )


def link(access_token: str, *, http: httpx.Client | None = None) -> FoodoraAccount:
    """Verify the pasted token against the order history and bind it to its customer id."""
    claims = _claims(access_token)
    subject = claims.get("user_id")
    if isinstance(subject, bool) or not isinstance(subject, int | str) or not str(subject):
        raise FoodoraError("auth_required")
    if _expired(claims):
        raise FoodoraError("auth_required")
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            response = _get(transport, access_token, 0, 1)
        except httpx.HTTPError:
            raise FoodoraError("provider_unavailable") from None
    finally:
        if owned:
            transport.close()
    if response.status_code in {401, 403}:
        raise FoodoraError("auth_required")
    if response.status_code == 429:
        raise FoodoraError("rate_limited")
    if response.status_code != 200:
        raise FoodoraError("provider_unavailable")
    return FoodoraAccount(
        credential=json.dumps({"access_token": access_token}), subject=str(subject)
    )


class FoodoraClient:
    def __init__(
        self,
        credential: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        http: httpx.Client | None = None,
        delay: float = 0.5,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        # The token cannot be rotated; save_refresh is kept for the shared factory contract.
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        try:
            self._access = str(json.loads(credential).get("access_token", ""))
        except ValueError, AttributeError:
            raise FoodoraError("auth_required") from None
        if not self._access or _expired(_claims(self._access)):
            raise FoodoraError("auth_required")
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
        self._delay = delay
        self._heartbeat = heartbeat
        # The history list already carries every order field; details are served from it.
        self._orders: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise FoodoraError("provider_response_invalid")
        for attempt in range(4):
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = _get(self._http, self._access, (number - 1) * PAGE_SIZE, PAGE_SIZE)
            except httpx.HTTPError:
                if attempt == 3:
                    raise FoodoraError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code in {401, 403}:
                raise FoodoraError("auth_required")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise FoodoraError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise FoodoraError("provider_unavailable")
            try:
                data = (response.json() or {}).get("data") or {}
                items = data.get("items")
                if not isinstance(items, list):
                    raise ValueError
            except ValueError, AttributeError:
                raise FoodoraError("provider_response_invalid") from None
            for order in items:
                if isinstance(order, dict) and isinstance(order.get("order_code"), str):
                    self._orders[order.get("order_code")] = order
            # total_count is only a lookahead estimate; the sync engine stops on an empty page.
            return ProviderPayload({"orders": items}, response.content)
        raise FoodoraError("provider_unavailable")

    def detail(self, order_code: str) -> dict[str, Any]:
        order = self._orders.get(order_code)
        if order is None:
            raise FoodoraError("receipt_identity_mismatch")
        raw = json.dumps(order, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return ProviderPayload(order, raw.encode("utf-8"))
