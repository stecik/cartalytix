"""Paged transaction export with a value-free schema report."""

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from cartalytix.export import ExportError, write_json
from mixit.export import shape

from .api import COUNTRY, PAGE_SIZE, app_headers, renew, transactions_url

LIST_KEYS = ("transactions", "items", "receipts", "results", "data")


class Client:
    def __init__(
        self,
        customer: str,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.customer = customer
        self.refresh_token = refresh_token
        self.save_refresh = save_refresh
        self.access: str | None = None
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.sleep = sleep

    def close(self) -> None:
        if self.owns_http:
            self.http.close()

    def _save(self, token: str) -> None:
        self.save_refresh(token)
        self.refresh_token = token

    def page(self, start: int) -> Any:
        if self.access is None:
            self.access = renew(self.http, self.refresh_token, self._save)
        params = {
            "start": str(start),
            "limit": str(PAGE_SIZE),
            "country": COUNTRY,
            "version": "2",
        }
        for attempt in range(4):
            self.sleep(self.delay)
            try:
                response = self.http.get(
                    transactions_url(self.customer),
                    params=params,
                    headers=app_headers(self.access),
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                continue
            if response.status_code in {401, 403}:
                raise ExportError(
                    f"Kaufland API HTTP {response.status_code}; run: uv run kaufland login"
                )
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                self.sleep(max(self.delay, 2) * (2**attempt))
                continue
            if response.status_code != 200:
                raise ExportError(
                    f"Kaufland API HTTP {response.status_code}; no response body logged"
                )
            try:
                return response.json()
            except ValueError:
                raise ExportError("Kaufland API returned non-JSON data") from None
        raise ExportError("API retry limit reached")


def extract(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """Transactions list and the path it was found under; the schema is not documented."""
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)], "$"
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [t for t in value if isinstance(t, dict)], f"$.{key}"
            if isinstance(value, dict):
                found, path = extract(value)
                return found, f"$.{key}{path[1:]}"
    raise ExportError("Unexpected transactions schema; raw page kept")


def minor_total(positions: Any) -> int | None:
    values = [p.get("total") for p in positions or [] if isinstance(p, dict)]
    if not values or not all(
        isinstance(v, int) and not isinstance(v, bool) for v in values
    ):
        return None
    return sum(values)


def collect(
    client: Client,
    destination: Path,
    *,
    max_pages: int = 1000,
    progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    run = time.strftime("%Y%m%dT%H%M%S")
    receipts: dict[str, dict[str, Any]] = {}
    page_schema: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    start = pages = repeated = 0
    complete = False
    while pages < max_pages:
        payload = client.page(start)
        pages += 1
        write_json(destination / "raw" / "pages" / run / f"{pages:04d}.json", payload)
        shape(payload, found=page_schema)
        batch, path = extract(payload)
        paths[path] += 1
        for receipt in batch:
            key = str(receipt.get("id") or "")
            if not key or key in receipts:
                repeated += 1
            receipts[key or f"missing-id-{len(receipts)}"] = receipt
        progress(f"Stránka {pages}: {len(batch)} účtenek, celkem {len(receipts)}")
        if len(batch) < PAGE_SIZE:
            complete = True
            break
        start += len(batch)
    items = list(receipts.values())
    write_json(destination / "receipts.json", items)
    reconciled = Counter(
        "no_item_totals"
        if (total := minor_total(r.get("positions"))) is None
        else "sum_equals_items"
        if total == r.get("sum")
        else "sum_differs"
        for r in items
    )
    stamps = sorted(str(r.get("timestamp")) for r in items if r.get("timestamp"))
    report = {
        "complete": complete and repeated == 0,
        "pages": pages,
        "page_size": PAGE_SIZE,
        "unique_receipts": len(items),
        "repeated_or_missing_ids": repeated,
        "positions": sum(len(r.get("positions") or []) for r in items),
        "refund_positions": sum(len(r.get("refundPositions") or []) for r in items),
        "oldest": stamps[0] if stamps else None,
        "newest": stamps[-1] if stamps else None,
        "currencies": dict(Counter(str(r.get("currency")) for r in items)),
        "reconciliation": dict(reconciled),
        "list_paths": dict(paths),
        "page_schema": dict(sorted(page_schema.items())),
    }
    write_json(destination / "report.json", report)
    return report
