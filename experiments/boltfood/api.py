"""Read-only replay of the web app's own history requests; no ordering or profile calls."""

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from cartalytix.export import ExportError

from .capture import find_key


def _expiry(token: str, fallback: float) -> float:
    parts = token.split(".")
    if len(parts) == 3:
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
            )
            exp = payload.get("exp") if isinstance(payload, dict) else None
            if isinstance(exp, int | float) and not isinstance(exp, bool):
                return float(exp)
        except ValueError:
            pass
    return time.time() + fallback


class Client:
    def __init__(
        self,
        session: dict[str, Any],
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.refresh_token = str(session.get("refresh_token", ""))
        self.save_refresh = save_refresh
        access = session.get("access_token")
        self.access: str | None = access if isinstance(access, str) and access else None
        self.expires_at = _expiry(self.access, 300) if self.access else 0.0
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.sleep = sleep

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def _send(
        self,
        template: dict[str, Any],
        headers: dict[str, str],
        body: Any = None,
        extra_query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = template.get("base", "") + template.get("path", "")
        for attempt in range(4):
            self.sleep(self.delay)
            try:
                response = self.http.request(
                    template.get("method", "GET"),
                    url,
                    params={**template.get("query", {}), **(extra_query or {})},
                    headers={
                        **template.get("headers", {}),
                        "Accept": "application/json",
                        **headers,
                    },
                    json=body if template.get("method") == "POST" else None,
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                self.sleep(self.delay * (2**attempt))
                continue
            if response.status_code in {401, 403}:
                raise ExportError("Bolt session rejected; run: uv run boltfood login")
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                # The web app itself hit 429 (code 1005) while paging; back off generously.
                self.sleep(max(self.delay, 2) * (2**attempt))
                continue
            if response.status_code != 200:
                raise ExportError(
                    f"Bolt API HTTP {response.status_code}; no response body logged"
                )
            try:
                result = response.json()
            except ValueError:
                raise ExportError("Bolt API returned non-JSON data") from None
            if not isinstance(result, dict):
                raise ExportError("Bolt API returned unexpected schema")
            code = result.get("code", 0)
            if code != 0:
                # Bolt reports failures as HTTP 200 with a numeric code; only the code is shown.
                raise ExportError(f"Bolt API code {code}; no response body logged")
            return result
        raise ExportError("API retry limit reached")

    def renew(self) -> None:
        template = self.session.get("refresh") or {}
        header = template.get("token_header", "authorization")
        result = self._send(
            template,
            {header: template.get("token_prefix", "") + self.refresh_token},
            {},
        )
        access = find_key(result, "access_token")
        if not isinstance(access, str) or not access:
            raise ExportError("Token response without access token; run login again")
        rotated = find_key(result, "refresh_token")
        if isinstance(rotated, str) and rotated and rotated != self.refresh_token:
            self.save_refresh(
                rotated
            )  # Persist rotation before any further network call.
            self.refresh_token = rotated
        self.access = access
        self.expires_at = _expiry(access, 300)

    def _authorized(
        self,
        template: dict[str, Any],
        body: Any = None,
        extra_query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.access is None or self.expires_at <= time.time() + 60:
            self.renew()
        return self._send(
            template, {"Authorization": f"Bearer {self.access}"}, body, extra_query
        )

    def history(self, page_token: str | None = None) -> dict[str, Any]:
        extra = {"page_token": page_token} if page_token else None
        return self._authorized(self.session.get("history") or {}, extra_query=extra)

    def detail(self, order: dict[str, Any]) -> dict[str, Any]:
        """Each body field (order_id, city_id, ...) comes from the order's own history row."""
        template = self.session.get("detail") or {}
        body = {
            key: order.get(key, value)
            for key, value in template.get("body", {}).items()
        }
        return self._authorized(template, body)
