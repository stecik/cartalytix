"""Raw history and detail capture plus a value-free schema report for designing normalization."""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cartalytix.export import ExportError, write_json
from mixit.export import shape

from .capture import find_key, values_at


class OrdersAPI(Protocol):
    def history(self, page_token: str | None = None) -> dict[str, Any]: ...

    def detail(self, order: dict[str, Any]) -> dict[str, Any]: ...

    def renew(self) -> None: ...


def list_orders(
    api: OrdersAPI,
    raw_pages: Path,
    id_path: list[str],
    max_pages: int,
    progress: Callable[[str], None],
) -> tuple[list[dict[str, Any]], Counter[str], int]:
    item_path, id_key = tuple(id_path[:-1]), id_path[-1] if id_path else "order_id"
    orders: dict[str, dict[str, Any]] = {}
    history_shape: Counter[str] = Counter()
    token: str | None = None
    seen_tokens: set[str] = set()
    for number in range(1, max_pages + 1):
        page = api.history(token)
        write_json(raw_pages / f"page-{number:05d}.json", page)
        shape(page, found=history_shape)
        for item in values_at(page, item_path):
            if isinstance(item, dict) and item.get(id_key) is not None:
                orders.setdefault(str(item.get(id_key)), item)
        progress(f"Stránka {number}: {len(orders)} objednávek")
        next_token = find_key(page, "next_page_token")
        if not next_token:
            return list(orders.values()), history_shape, number
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise ExportError("Repeated or invalid page token; history is not reliable")
        seen_tokens.add(next_token)
        token = next_token
    raise ExportError("Page limit reached; history is not complete")


def collect(
    api: OrdersAPI,
    destination: Path,
    id_path: list[str],
    *,
    max_pages: int = 1000,
    refresh_details: bool = False,
    progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    orders, history_shape, pages = list_orders(
        api, destination / "raw" / "pages" / run, id_path, max_pages, progress
    )
    if not orders:
        raise ExportError("History returned no orders; raw pages kept")
    id_key = id_path[-1] if id_path else "order_id"
    detail_shape: Counter[str] = Counter()
    downloaded = cached = failures = consecutive = 0
    for order in orders:
        name = hashlib.sha256(str(order.get(id_key)).encode()).hexdigest() + ".json"
        path = destination / "raw" / "details" / name
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
                detail = api.detail(order)
            except ExportError:
                failures += 1
                consecutive += 1
                if consecutive >= 3:
                    raise ExportError(
                        "Three consecutive detail failures; stopped to avoid repeating systemic errors"
                    ) from None
                continue
            consecutive = 0
            write_json(path, detail)
            downloaded += 1
            progress(f"Detail {downloaded + cached}/{len(orders)}")
        shape(detail, found=detail_shape)
    try:
        # Proves whether unattended sync can renew access without a new SMS login.
        api.renew()
        refresh_check = "ok"
    except ExportError as error:
        refresh_check = str(error)
    report = {
        "complete": failures == 0,
        "unique_orders": len(orders),
        "pages": pages,
        "downloaded_details": downloaded,
        "cached_details": cached,
        "detail_failures": failures,
        "refresh_check": refresh_check,
        "history_schema": dict(sorted(history_shape.items())),
        "detail_schema": dict(sorted(detail_shape.items())),
    }
    write_json(destination / "report.json", report)
    return report
