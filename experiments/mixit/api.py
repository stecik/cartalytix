"""Read-only Mixit API adapter; no cart, reorder or profile mutation methods."""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.export import ExportError

API = (
    "https://api.mixit.cz/api/v2"  # Observed from the public web bundle on 2026-10-17.
)
SITE = "https://www.mixit.cz"


class Client:
    def __init__(
        self,
        session: dict[str, Any],
        *,
        http: httpx.Client | None = None,
        delay: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.sleep = sleep

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def headers(self) -> dict[str, str]:
        header = self.session.get("header", "Token")
        value = self.session.get("prefix", "") + self.session.get("token", "")
        return {
            **self.session.get("context_headers", {}),
            "Accept": "application/json",
            "Accept-Language": "cs-CZ",
            "Origin": SITE,
            "Referer": SITE + "/",
            header: value,
        }

    def get(self, path: str) -> dict[str, Any]:
        base = self.session.get("api", API)
        for attempt in range(4):
            self.sleep(self.delay)
            try:
                response = self.http.get(f"{base}/{path}", headers=self.headers())
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                self.sleep(self.delay * (2**attempt))
                continue
            if response.status_code in {401, 403}:
                raise ExportError("Mixit session rejected; run: uv run mixit login")
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                retry = response.headers.get("Retry-After", "")
                if retry:
                    try:
                        wait = float(retry)
                    except ValueError:
                        raise ExportError(
                            "API requested delayed retry; stop and rerun later"
                        ) from None
                    if not 0 <= wait <= 60:
                        raise ExportError("API rate limit; rerun later")
                else:
                    wait = max(self.delay, 1) * (2**attempt)
                self.sleep(wait)
                continue
            if response.status_code != 200:
                # Mixit answers unauthenticated customer routes with 404, not 401.
                raise ExportError(
                    f"Mixit API HTTP {response.status_code}; session may be expired, no response body logged"
                )
            try:
                result = response.json()
            except ValueError:
                raise ExportError("Mixit API returned non-JSON data") from None
            if not isinstance(result, dict):
                raise ExportError("Mixit API returned unexpected schema")
            return result
        raise ExportError("API retry limit reached")

    def page(self, number: int) -> dict[str, Any]:
        return self.get(f"customer/orders?page={number}")

    def detail(self, order_hash: str) -> dict[str, Any]:
        return self.get("customer/orders/" + quote(order_hash, safe=""))
