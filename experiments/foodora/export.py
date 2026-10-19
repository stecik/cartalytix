"""foodora.cz order history; fetched inside the signed-in browser because of bot protection."""

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from cartalytix.export import ExportError, write_json

SITE = "https://www.foodora.cz/new/orders"
HISTORY = "https://cz.fd-api.com/api/v5/orders/order_history"
PAGE_SIZE = 20
MAX_ORDERS = 100000
DELAY = 0.5
# Same request the web app makes; the token never leaves the browser.
FETCH_JS = """async ([url, offset, limit]) => {
  const cookie = (name) => decodeURIComponent(
    (document.cookie.split('; ').find((c) => c.startsWith(name + '=')) || '').slice(name.length + 1));
  const query = new URLSearchParams({include: 'order_products,order_details', offset: String(offset),
    limit: String(limit), language_id: '3', item_replacement: 'true'});
  const response = await fetch(url + '?' + query, {headers: {
    Accept: 'application/json', 'X-FP-API-KEY': 'volo',
    'perseus-client-id': cookie('dhhPerseusGuestId'), 'perseus-session-id': cookie('dhhPerseusSessionId'),
    Authorization: 'Bearer ' + cookie('token')}});
  return {status: response.status, text: await response.text()};
}"""
# Only the customer id claim is returned, to namespace exports per account.
ACCOUNT_JS = """() => {
  const token = decodeURIComponent((document.cookie.split('; ').find((c) => c.startsWith('token=')) || '').slice(6));
  if (!token) return null;
  const claims = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
  return String(claims.user_id || claims.sub || '');
}"""
SUBTOTAL = "NEXTGEN_CART_SUBTOTAL"
TOTAL = "NEXTGEN_TOTAL_VAT"


def amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        return None
    return result if result.is_finite() else None


def order_date(value: object) -> str | None:
    """Local wall time; the source timezone is kept alongside (no tz database needed)."""
    if not isinstance(value, dict):
        return None
    try:
        return datetime.strptime(
            str(value.get("date")), "%Y-%m-%d %H:%M:%S"
        ).isoformat()
    except ValueError:
        return None


def normalize(order: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    items: list[dict[str, Any]] = []
    line_sum = Decimal(0)
    for product in order.get("order_products") or []:
        quantity = amount(product.get("quantity"))
        line = amount(product.get("total_price"))
        if quantity is None or quantity <= 0 or line is None:
            issues.append("invalid_item_amount")
            continue
        items.append(
            {
                "name": product.get("name"),
                "code": product.get("variation_id"),
                "quantity": format(quantity, "f"),
                "unit_price": format(line / quantity, "f"),
                "amount": format(line, "f"),
                "toppings": len(product.get("toppings") or []),
            }
        )
        line_sum += line
    if not items:
        issues.append("items_unavailable")
    subtotal = amount(order.get("subtotal"))
    total = amount(order.get("total_value"))
    fees: list[dict[str, str]] = []
    discounts: list[dict[str, str]] = []
    adjustment = Decimal(0)
    for fee in order.get("dynamic_fees") or []:
        key = str(fee.get("translation_key") or "")
        value = amount(fee.get("value"))
        if key in {SUBTOTAL, TOTAL} or not value:
            continue
        name = key.removeprefix("NEXTGEN_").lower()
        adjustment += value
        if value > 0:
            fees.append({"name": name, "amount": format(value, "f")})
        else:
            # Discounts are stored as positive savings, like the other connectors.
            discounts.append({"name": name, "amount": format(-value, "f")})
    tip = amount(order.get("rider_tip")) or Decimal(0)
    if tip and not any("tip" in fee.get("name", "") for fee in fees):
        fees.append({"name": "rider_tip", "amount": format(tip, "f")})
        adjustment += tip
    if subtotal is None or line_sum != subtotal:
        issues.append("subtotal_mismatch")
    if total is None:
        issues.append("invalid_total")
    elif subtotal is not None:
        gap = total - subtotal - adjustment
        # Older orders (2021) omit promotion lines; the paid total matches the card charge.
        if gap < 0:
            discounts.append(
                {"name": "discounts_and_credits", "amount": format(-gap, "f")}
            )
        elif gap > 0:
            issues.append("totals_unreconciled")
    flags = order.get("status_flags") or {}
    status = (
        "canceled"
        if flags.get("is_canceled")
        else "completed"
        if flags.get("is_delivered") or flags.get("is_completed")
        else "active"
    )
    ordered_at = order.get("ordered_at") or {}
    date = order_date(ordered_at)
    if date is None:
        issues.append("invalid_date")
    vendor = order.get("vendor") or {}
    return {
        "id": order.get("order_code"),
        "date": date,
        "timezone": ordered_at.get("timezone"),
        "status": status,
        "merchant": vendor.get("name"),
        # foodora.cz bills in CZK; the payload has no currency field.
        "currency": "CZK",
        "subtotal": format(subtotal, "f") if subtotal is not None else None,
        "total": format(total, "f") if total is not None else None,
        "items": items,
        "fees": fees,
        "discounts": discounts,
        "issues": sorted(set(issues)),
    }


def collect(
    fetch_page: Callable[[int, int], dict[str, Any]],
    destination: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Walk offset pages until a short or empty page; history has no cursor, overlaps are fatal.

    total_count is only a lookahead (offset + page + next page size), never the real total.
    """
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    report: dict[str, Any] = {"run": run, "complete": False, "pages": 0, "errors": []}
    orders: dict[str, dict[str, Any]] = {}
    offset = 0
    while offset < MAX_ORDERS:
        data = fetch_page(offset, PAGE_SIZE)
        write_json(destination / "raw" / "pages" / run / f"{offset}.json", data)
        items = (data.get("data") or {}).get("items")
        if not isinstance(items, list):
            report["errors"].append("invalid_page")
            break
        for order in items:
            code = order.get("order_code") if isinstance(order, dict) else None
            if not isinstance(code, str) or not code or code in orders:
                report["errors"].append("duplicate_or_invalid_order")
                continue
            orders[code] = order
        if items:
            report["pages"] += 1
        offset += len(items)
        if progress:
            progress(f"Objednávky {len(orders)}")
        if len(items) < PAGE_SIZE:
            break
    else:
        report["errors"].append("order_limit_reached")
    normalized = [normalize(order) for order in orders.values()]
    dates = sorted(str(order.get("date")) for order in normalized if order.get("date"))
    report.update(
        {
            "unique_orders": len(orders),
            "statuses": {
                status: sum(order.get("status") == status for order in normalized)
                for status in ("completed", "active", "canceled")
            },
            "quality_issues": sum(
                len(order.get("issues") or []) for order in normalized
            ),
            "oldest_order": dates[0] if dates else None,
            "newest_order": dates[-1] if dates else None,
        }
    )
    report["complete"] = not report["errors"]
    write_json(
        destination / ("orders.json" if report["complete"] else "orders.partial.json"),
        normalized,
    )
    write_json(destination / "report.json", report)
    return report


def export(
    *,
    channel: str = "chrome",
    timeout: float = 900,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], Path]:
    """Manual login in a fresh headed browser; cookies and tokens stay in that browser."""
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False, channel=None if channel == "chromium" else channel
            )
        except BrowserError:
            raise ExportError(
                "Browser launch failed; use --browser chrome or install Chromium locally"
            ) from None
        try:
            page = browser.new_context(locale="cs-CZ").new_page()
            page.goto(SITE, wait_until="domcontentloaded", timeout=60000)
            print(
                "Přihlaste se ručně v okně foodora (údaje a kódy zadávejte jen tam). "
                "Pak otevřete Moje objednávky.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            account = None
            while not account:
                if page.is_closed():
                    raise ExportError("Login window closed")
                if time.monotonic() > deadline:
                    raise ExportError("Login timed out; rerun locally")
                page.wait_for_timeout(500)
                try:
                    account = page.evaluate(ACCOUNT_JS)
                except BrowserError:
                    account = None  # Page is navigating during login.

            def fetch_page(offset: int, limit: int) -> dict[str, Any]:
                time.sleep(DELAY)
                result = page.evaluate(FETCH_JS, [HISTORY, offset, limit])
                if result.get("status") != 200:
                    raise ExportError(
                        f"foodora history failed (HTTP {result.get('status')}); sign in again"
                    )
                return json.loads(result.get("text"))

            profile = hashlib.sha256(str(account).encode()).hexdigest()[:32]
            destination = Path("data") / "foodora" / "CZ" / profile
            report = collect(fetch_page, destination, progress=progress)
        except BrowserError:
            raise ExportError("Browser session interrupted; rerun locally") from None
        finally:
            browser.close()
    return report, destination
