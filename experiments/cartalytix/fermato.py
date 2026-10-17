"""Fermato.cz (Shoptet) order export; credentials go directly to Fermato, never to Python."""

import hashlib
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from .export import ExportError, number, write_json

BASE = "https://www.fermato.cz"
LOGIN = BASE + "/login/?backTo=%2Fklient%2F"
ORDERS = BASE + "/klient/objednavky/"
SETTINGS = BASE + "/klient/nastaveni/"
DELAY = 0.5
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}
PARTS = {
    "p-name__itemLink",
    "p-name__itemName",
    "p-name__code",
    "p-name__shipping",
}
CODE = re.compile(r"\d{1,20}")
PAGE = re.compile(r"/klient/objednavky/strana-\d+/")


def clean(text: str) -> str:
    return " ".join(text.split())


class TableRows(HTMLParser):
    """Collect rows of the first table carrying `table_class`, cell text by class."""

    def __init__(self, table_class: str) -> None:
        super().__init__(convert_charrefs=True)
        self.table_class = table_class
        self.depth = 0
        self.done = False
        self.stack: list[tuple[str, set[str]]] = []
        self.rows: list[dict[str, Any]] = []
        self.links: list[str] = []
        self.cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and values.get("href"):
            self.links.append(values.get("href") or "")
        if self.done:
            return
        if tag == "table":
            if self.depth:
                self.depth += 1
            elif self.table_class in classes:
                self.depth = 1
            return
        if not self.depth:
            return
        if tag == "tr":
            self.rows.append({"class": classes, "cells": []})
        elif tag in ("td", "th") and self.rows:
            self.cell = {"text": [], "parts": {}, "href": None, "defs": []}
            self.rows[-1]["cells"].append(self.cell)
            self.stack = []
        elif self.cell is not None:
            if tag == "a" and self.cell["href"] is None:
                self.cell["href"] = values.get("href")
            if tag in ("dt", "dd"):
                self.cell["defs"].append([])
            if tag not in VOID:
                self.stack.append((tag, classes))

    def handle_endtag(self, tag: str) -> None:
        if not self.depth or self.done:
            return
        if tag == "table":
            self.depth -= 1
            self.done = not self.depth
        elif tag in ("td", "th"):
            self.cell = None
        elif self.cell is not None:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    def handle_data(self, data: str) -> None:
        if self.cell is None:
            return
        classes = (
            set().union(*(item[1] for item in self.stack)) if self.stack else set()
        )
        if "client-table-heading" in classes or "p-name__codeLabel" in classes:
            return
        self.cell["text"].append(data)
        if self.cell["defs"]:
            self.cell["defs"][-1].append(data)
        for name in classes & PARTS:
            self.cell["parts"].setdefault(name, []).append(data)


def table_rows(html: str, table_class: str) -> tuple[list[dict[str, Any]], list[str]]:
    parser = TableRows(table_class)
    parser.feed(html)
    parser.close()
    rows = [
        {
            "class": row["class"],
            "cells": [
                {
                    "text": clean("".join(cell["text"])),
                    "parts": {k: clean("".join(v)) for k, v in cell["parts"].items()},
                    "href": cell["href"],
                    "defs": [clean("".join(d)) for d in cell["defs"]],
                }
                for cell in row["cells"]
            ],
        }
        for row in parser.rows
    ]
    return rows, parser.links


class EmailInput(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.email: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if (
            tag == "input"
            and values.get("name") == "email"
            and values.get("value")
            and self.email is None
        ):
            self.email = values.get("value")


def account_profile(settings_html: str) -> str:
    parser = EmailInput()
    parser.feed(settings_html)
    if not parser.email:
        raise ExportError("Fermato account e-mail not found; sign in again")
    # Hashed e-mail keeps one export namespace per account without storing the address.
    return hashlib.sha256(parser.email.strip().lower().encode()).hexdigest()[:32]


def money(text: str) -> tuple[str | None, str | None]:
    """Return (amount, currency) from Shoptet price text such as '1 332,50 Kč'."""
    if not text:
        return None, None
    currency = "CZK" if "Kč" in text else None
    try:
        return number(text.replace("Kč", "")), currency
    except ValueError:
        return None, currency


def order_date(text: str) -> str | None:
    try:
        return datetime.strptime(clean(text), "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        return None


def parse_list(html: str) -> tuple[list[dict[str, Any]], list[str]]:
    rows, links = table_rows(html, "customer-orders")
    orders = []
    for row in rows:
        cells = row["cells"]
        if len(cells) < 4 or not cells[0]["href"]:
            continue
        code = parse_qs(urlsplit(cells[0]["href"]).query).get("code", [""])[0]
        if not CODE.fullmatch(code):
            continue
        orders.append(
            {
                "code": code,
                "date": order_date(cells[1]["text"]),
                "total_net": money(cells[2]["text"])[0],
                "total": money(cells[3]["text"])[0],
                "payment_state": cells[4]["text"] if len(cells) > 4 else None,
            }
        )
    pages = sorted({urljoin(BASE, link) for link in links if PAGE.search(link)})
    return orders, pages


def normalize(
    code: str, html: str, listed: dict[str, Any] | None = None
) -> dict[str, Any]:
    issues: list[str] = []
    currencies: set[str] = set()

    def amount(text: str, field: str) -> str | None:
        value, currency = money(text)
        if currency:
            currencies.add(currency)
        if value is None:
            issues.append("invalid_" + field)
        return value

    rows, _ = table_rows(html, "table-order-details")
    items: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []
    summary: dict[str, str] = {}
    for row in rows:
        cells = row["cells"]
        if "row-summary" in row["class"] and cells:
            defs = cells[0]["defs"]
            summary = {
                defs[i].rstrip(":"): defs[i + 1] for i in range(0, len(defs) - 1, 2)
            }
            continue
        if len(cells) != 7 or not cells[0]["parts"]:
            continue
        parts = cells[0]["parts"]
        discount = cells[4]["text"].replace("%", "").strip()
        prices = {
            "unit_price_net": amount(cells[2]["text"], "unit_price_net"),
            "unit_price": amount(cells[3]["text"], "unit_price"),
            "discount_percent": number(discount)
            if re.fullmatch(r"[\d,.\s]+", discount)
            else None,
            "amount_net": amount(cells[5]["text"], "amount_net"),
            "amount": amount(cells[6]["text"], "amount"),
        }
        if "p-name__code" in parts:
            quantity = re.fullmatch(r"([\d\s,.]+?)\s*([^\d\s].*)?", cells[1]["text"])
            if not quantity:
                issues.append("invalid_quantity")
            items.append(
                {
                    "code": parts.get("p-name__code"),
                    "name": parts.get("p-name__itemLink")
                    or parts.get("p-name__itemName"),
                    "url": urljoin(BASE, cells[0]["href"])
                    if cells[0]["href"]
                    else None,
                    "quantity": number(quantity.group(1)) if quantity else None,
                    "unit": quantity.group(2) if quantity else None,
                    **prices,
                }
            )
        else:
            adjustments.append(
                {
                    "name": parts.get("p-name__itemName")
                    or parts.get("p-name__itemLink"),
                    "detail": parts.get("p-name__shipping"),
                    **prices,
                }
            )
    total = amount(summary.get("Cena celkem včetně DPH", ""), "total")
    total_net = amount(summary.get("Cena bez DPH", ""), "total_net")
    vat = amount(summary.get("DPH", ""), "vat")
    if not items:
        issues.append("items_unavailable")
    if len(currencies) > 1:
        issues.append("mixed_currencies")
    if listed and listed.get("total") not in (None, total):
        issues.append("list_total_differs")
    lines = [line["amount"] for line in items + adjustments]
    nets = [line["amount_net"] for line in items + adjustments]
    rounding = None
    total_source = "summary"
    if total is not None and all(value is not None for value in lines):
        line_sum = sum((Decimal(value) for value in lines), Decimal(0))
        # Shoptet sometimes shows 0 Kč for orders paid in full; line prices are what was paid.
        if Decimal(total) == 0 and line_sum > 0:
            issues.append("source_total_zero")
            total_source = "lines_sum"
            total = str(line_sum)
            if all(value is not None for value in nets):
                total_net = str(sum((Decimal(value) for value in nets), Decimal(0)))
                vat = str(line_sum - Decimal(total_net))
        difference = Decimal(total) - line_sum
        # Shoptet rounds the gross total to whole CZK without listing a rounding row.
        if difference and abs(difference) < 1:
            rounding = str(difference)
        elif difference:
            issues.append("totals_unreconciled")
    return {
        "id": code,
        "date": (listed or {}).get("date"),
        "currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "total": total,
        "total_source": total_source,
        "total_net": total_net,
        "vat": vat,
        "rounding": rounding,
        "payment_state": (listed or {}).get("payment_state"),
        "items": items,
        "adjustments": adjustments,
        "issues": sorted(set(issues)),
    }


def collect(
    fetch: Callable[[str], str],
    destination: Path,
    *,
    max_pages: int = 10000,
    refresh_details: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Walk every list page, download missing order details, write normalized export."""
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    report: dict[str, Any] = {
        "run": run,
        "complete": False,
        "pages": [],
        "unique_orders": 0,
        "downloaded_details": 0,
        "cached_details": 0,
        "errors": [],
    }
    listed: dict[str, dict[str, Any]] = {}
    queue, seen = [ORDERS], set()
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        if len(seen) >= max_pages:
            report["errors"].append("page_limit_reached")
            break
        seen.add(url)
        html = fetch(url)
        (destination / "raw" / "pages" / run).mkdir(parents=True, exist_ok=True)
        (destination / "raw" / "pages" / run / f"{len(seen)}.html").write_text(
            html, encoding="utf-8"
        )
        orders, pages = parse_list(html)
        report["pages"].append({"url": url, "orders": len(orders)})
        for order in orders:
            listed.setdefault(order["code"], order)
        queue.extend(page for page in pages if page not in seen)
    write_json(destination / "index.json", list(listed.values()))
    report["unique_orders"] = len(listed)
    normalized = []
    for code, order in listed.items():
        path = destination / "raw" / "orders" / f"{code}.html"
        if path.exists() and not refresh_details:
            report["cached_details"] += 1
        else:
            if progress:
                progress(f"Objednávka {code}")
            html = fetch(f"{ORDERS}?code={code}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            report["downloaded_details"] += 1
        result = normalize(code, path.read_text(encoding="utf-8"), order)
        if "items_unavailable" in result["issues"]:
            report["errors"].append(f"detail_unparsed:{code}")
        normalized.append(result)
    report["quality_issues"] = sum(len(order["issues"]) for order in normalized)
    report["dates"] = sorted(order["date"] for order in normalized if order["date"])
    report["oldest_order"] = report["dates"][0] if report["dates"] else None
    report["newest_order"] = report["dates"][-1] if report["dates"] else None
    del report["dates"]
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
    max_pages: int = 10000,
    refresh_details: bool = False,
    timeout: int = 600,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], Path]:
    """Manual login in a fresh headed browser; session cookies stay in that browser only."""
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False,
                channel=None if channel == "chromium" else channel,
            )
        except BrowserError:
            raise ExportError(
                "Browser launch failed; use --browser chrome or install Chromium locally"
            ) from None
        try:
            context = browser.new_context(locale="cs-CZ")
            page = context.new_page()
            page.goto(LOGIN, wait_until="domcontentloaded", timeout=60000)
            print(
                "Přihlaste se ručně v okně Fermato. Heslo zadávejte pouze tam.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while urlsplit(page.url).path.rstrip("/") != "/klient":
                if page.is_closed():
                    raise ExportError("Login window closed")
                if time.monotonic() > deadline:
                    raise ExportError("Login timed out; rerun locally")
                page.wait_for_timeout(200)

            def fetch(url: str) -> str:
                time.sleep(DELAY)
                response = context.request.get(url, timeout=30000)
                if not response.ok or urlsplit(response.url).path.startswith("/login"):
                    raise ExportError(
                        f"Fermato request failed (HTTP {response.status}); sign in again"
                    )
                return response.text()

            destination = Path("data") / "fermato" / account_profile(fetch(SETTINGS))
            report = collect(
                fetch,
                destination,
                max_pages=max_pages,
                refresh_details=refresh_details,
                progress=progress,
            )
        except BrowserError:
            raise ExportError("Browser session interrupted; rerun locally") from None
        finally:
            browser.close()
    return report, destination
