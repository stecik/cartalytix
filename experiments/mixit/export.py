"""Lossless raw capture plus conservative normalization of the live-verified schema."""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from cartalytix.export import ExportError, number, write_json


class OrdersAPI(Protocol):
    def page(self, number: int) -> dict[str, Any]: ...

    def detail(self, order_hash: str) -> dict[str, Any]: ...


def shape(
    value: Any, prefix: str = "$", found: Counter[str] | None = None
) -> Counter[str]:
    """Key paths and JSON types only; never values, so reports stay free of personal data."""
    found = Counter() if found is None else found
    if isinstance(value, dict):
        found[prefix + ":object"] += 1
        for key, child in value.items():
            shape(child, f"{prefix}.{key}", found)
    elif isinstance(value, list):
        found[prefix + ":array"] += 1
        for child in value:
            shape(child, prefix + "[]", found)
    else:
        kind = {bool: "bool", int: "number", float: "number", str: "string"}.get(
            type(value), "null"
        )
        found[f"{prefix}:{kind}"] += 1
    return found


CURRENCIES = {"Kč": "CZK", "€": "EUR"}


def money(value: Any, field: str, issues: list[str]) -> Decimal | None:
    try:
        result = number(value)
    except ValueError:
        issues.append("invalid_" + field)
        return None
    if result is None:
        issues.append("missing_" + field)
        return None
    return Decimal(result)


def text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def sum_all(values: list[Decimal | None]) -> Decimal | None:
    """Sum only when every amount is known; a partial sum would hide missing data."""
    known = [value for value in values if value is not None]
    return sum(known, Decimal(0)) if len(known) == len(values) else None


def line(
    source: dict[str, Any], kind: str, issues: list[str]
) -> tuple[dict[str, Any], Decimal | None]:
    quantity = source.get("quantity")
    unit_price = money(source.get("price"), "unit_price", issues)
    total = money(source.get("totalPrice"), "line_total", issues)
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        issues.append("invalid_quantity")
    elif (
        unit_price is not None and total is not None and unit_price * quantity != total
    ):
        issues.append("line_total_mismatch")
    if not source.get("name"):
        issues.append("missing_item_name")
    return {
        "kind": kind,
        "product_id": source.get("productID", source.get("mixID")),
        "name": source.get("name"),
        "quantity": quantity,
        "unit_price": text(unit_price),
        "total": text(total),
        "unit": source.get("unit"),
        "weight": source.get("weight"),
        "is_weight": source.get("isWeight"),
    }, total


def parse_date(value: Any) -> str | None:
    try:
        return datetime.strptime(str(value), "%d. %m. %Y").date().isoformat()
    except ValueError:
        return None


def normalize(order: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    source = detail.get("order")
    if not isinstance(source, dict):
        source = {}
        issues.append("detail_order_unavailable")
    merged = {**order, **source}
    items: list[dict[str, Any]] = []
    totals: list[Decimal | None] = []
    products = source.get("orderProducts") or []
    mixes = source.get("orderMixes") or []
    if not isinstance(products, list) or not isinstance(mixes, list):
        issues.append("invalid_items")
        products, mixes = [], []
    for product in products:
        item, total = line(product, "product", issues)
        items.append(item)
        totals.append(total)
    for mix in mixes:
        item, total = line(mix, "mix", issues)
        components: list[dict[str, Any]] = []
        component_totals: list[Decimal | None] = []
        for component in mix.get("mixProducts") or []:
            part, part_total = line(component, "mix_component", issues)
            components.append(part)
            component_totals.append(part_total)
        mix_price = money(mix.get("price"), "unit_price", [])
        components_total = sum_all(component_totals)
        if (
            mix_price is not None
            and components_total is not None
            and components_total != mix_price
        ):
            issues.append("mix_components_mismatch")
        items.append({**item, "components": components})
        totals.append(total)
    if not items:
        issues.append("items_unavailable")
    total = money(merged.get("totalPrice"), "total", issues)
    items_total = sum_all(totals)
    difference = (
        total - items_total if total is not None and items_total is not None else None
    )
    # Detail has no shipping, discount or credit amounts; the gap is reported, not explained.
    if difference:
        issues.append("totals_unreconciled")
    created = parse_date(merged.get("createdAt"))
    if created is None:
        issues.append("invalid_created_at")
    currency = CURRENCIES.get(merged.get("currency", ""))
    if currency is None:
        issues.append("unknown_currency")
    return {
        "id": order.get("hash"),
        "order_id": merged.get("id"),
        "variable_symbol": merged.get("variableSymbol"),
        "invoice_number": source.get("invoiceNumber"),
        "created_at": created,
        "paid_at": source.get("paidAt"),
        "state": merged.get("state"),
        "currency": currency,
        "source_currency": merged.get("currency"),
        "total": text(total),
        "total_without_vat": text(
            money(merged.get("totalPriceWithoutVat"), "total_without_vat", issues)
        ),
        "total_vat": text(money(merged.get("totalVat"), "total_vat", issues)),
        "items_total": text(items_total),
        "unexplained_difference": text(difference),
        "transport": (source.get("transport") or {}).get("name"),
        "payment": (source.get("payment") or {}).get("name"),
        "items": items,
        "issues": issues,
    }


def list_orders(
    api: OrdersAPI, raw_pages: Path, max_pages: int, progress: Callable[[str], None]
) -> tuple[list[dict[str, Any]], int]:
    orders: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected: int | None = None
    for page_number in range(1, max_pages + 1):
        page = api.page(page_number)
        write_json(raw_pages / f"page-{page_number:05d}.json", page)
        batch = page.get("orders")
        total = page.get("totalRecords")
        if (
            not isinstance(batch, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
        ):
            raise ExportError("Unexpected order list schema; raw page kept")
        if expected is None:
            expected = total
        elif total != expected:
            raise ExportError("totalRecords changed during listing; rerun later")
        if not batch:
            if len(seen) < expected:
                raise ExportError(
                    f"Empty page before end: {len(seen)}/{expected} orders"
                )
            return orders, page_number
        fresh = 0
        for order in batch:
            order_hash = order.get("hash") if isinstance(order, dict) else None
            if not isinstance(order_hash, str) or not order_hash:
                raise ExportError("Order without hash; raw page kept")
            if order_hash not in seen:
                seen.add(order_hash)
                orders.append(order)
                fresh += 1
        if not fresh:
            raise ExportError("Repeated page; listing is not reliable")
        progress(f"Stránka {page_number}: {len(seen)}/{expected}")
        if len(seen) > expected:
            raise ExportError("More orders than totalRecords; rerun later")
        if len(seen) == expected:
            return orders, page_number
    raise ExportError("Page limit reached; history is not complete")


def collect(
    api: OrdersAPI,
    destination: Path,
    *,
    max_pages: int = 10000,
    refresh_details: bool = False,
    progress: Callable[[str], None] = lambda text: None,
) -> dict[str, Any]:
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    orders, pages = list_orders(
        api, destination / "raw" / "pages" / run, max_pages, progress
    )
    write_json(destination / "index.json", orders)
    receipts: list[dict[str, Any]] = []
    order_shape: Counter[str] = Counter()
    detail_shape: Counter[str] = Counter()
    downloaded = cached = failures = consecutive = 0
    for order in orders:
        shape(order, found=order_shape)
        path = (
            destination
            / "raw"
            / "details"
            / (hashlib.sha256(order["hash"].encode()).hexdigest() + ".json")
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
                detail = api.detail(order["hash"])
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
        receipts.append(normalize(order, detail))
    dates = sorted(
        r["created_at"] for r in receipts if isinstance(r["created_at"], str)
    )
    report = {
        "complete": failures == 0,
        "unique_orders": len(orders),
        "pages": pages,
        "oldest_order": dates[0] if dates else None,
        "newest_order": dates[-1] if dates else None,
        "downloaded_details": downloaded,
        "cached_details": cached,
        "detail_failures": failures,
        "quality_issues": dict(
            Counter(issue for r in receipts for issue in r["issues"])
        ),
        "schema": {
            "order": dict(sorted(order_shape.items())),
            "detail": dict(sorted(detail_shape.items())),
        },
    }
    write_json(
        destination / ("receipts.json" if failures == 0 else "receipts.partial.json"),
        {"receipts": receipts},
    )
    write_json(destination / "report.json", report)
    return report
