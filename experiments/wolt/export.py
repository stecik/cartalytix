"""Order history and purchase detail export with a value-free schema report."""

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.export import ExportError, write_json
from mixit.export import shape

from .api import renew

API = "https://consumer-api.wolt.com/order-tracking-api/v1/order_history"


class Client:
    def __init__(
        self,
        session: dict[str, Any],
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.session = session
        self.refresh_token = refresh_token
        self.save_refresh = save_refresh
        self.access: str | None = None
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.sleep = sleep
        detail = next(
            (t for k, t in session.get("orders", {}).items() if "/purchase/" in k), {}
        )
        # Replays the web client's own non-secret headers (client version, locale).
        self.headers = {**detail.get("headers", {}), "Accept": "application/json"}

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def _save(self, token: str) -> None:
        self.save_refresh(token)
        self.refresh_token = token

    def get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        if self.access is None:
            self.access = renew(
                self.session.get("refresh") or {},
                self.refresh_token,
                self._save,
                http=self.http,
            )
        for attempt in range(4):
            self.sleep(self.delay)
            try:
                response = self.http.get(
                    url,
                    params=params,
                    headers={**self.headers, "Authorization": f"Bearer {self.access}"},
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                continue
            if response.status_code in {401, 403}:
                raise ExportError("Wolt session rejected; run: uv run wolt login")
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                self.sleep(max(self.delay, 2) * (2**attempt))
                continue
            if response.status_code != 200:
                raise ExportError(
                    f"Wolt API HTTP {response.status_code}; no response body logged"
                )
            try:
                result = response.json()
            except ValueError:
                raise ExportError("Wolt API returned non-JSON data") from None
            if not isinstance(result, dict):
                raise ExportError("Wolt API returned unexpected schema")
            return result
        raise ExportError("API retry limit reached")

    def history(self) -> dict[str, Any]:
        return self.get(f"{API}/", {"limit": "50"})

    def detail(self, purchase_id: str) -> dict[str, Any]:
        return self.get(
            f"{API}/purchase/{quote(purchase_id, safe='')}",
            {"tips_use_percentage": "true"},
        )


def collect(
    client: Client,
    destination: Path,
    *,
    refresh_details: bool = False,
    progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    history = client.history()
    write_json(destination / "raw" / "history.json", history)
    orders = [
        o
        for o in history.get("orders") or []
        if isinstance(o, dict) and o.get("purchase_id")
    ]
    if not orders:
        raise ExportError("History returned no orders; raw response kept")
    detail_shape: Counter[str] = Counter()
    downloaded = cached = failures = 0
    for order in orders:
        purchase_id = str(order.get("purchase_id"))
        path = (
            destination
            / "raw"
            / "details"
            / (hashlib.sha256(purchase_id.encode()).hexdigest() + ".json")
        )
        detail: dict[str, Any] | None = None
        if path.exists() and not refresh_details:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                detail = loaded if isinstance(loaded, dict) else None
            except ValueError:
                detail = None
            cached += detail is not None
        if detail is None:
            try:
                detail = client.detail(purchase_id)
            except ExportError:
                failures += 1
                continue
            write_json(path, detail)
            downloaded += 1
            progress(f"Detail {downloaded + cached}/{len(orders)}")
        shape(detail, found=detail_shape)
    report = {
        "complete": failures == 0,
        "unique_orders": len(orders),
        # Pagination beyond 50 orders is unverified; flagged so a longer history is noticed.
        "history_may_be_truncated": len(orders) >= 50,
        "downloaded_details": downloaded,
        "cached_details": cached,
        "detail_failures": failures,
        "statuses": dict(Counter(str(o.get("status")) for o in orders)),
        "history_schema": dict(sorted(shape(history).items())),
        "detail_schema": dict(sorted(detail_shape.items())),
    }
    write_json(destination / "report.json", report)
    return report
