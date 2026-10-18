"""Shoptet HTML order parsing; fees become items, coupons receipt discounts."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

PARSER_VERSION = "shoptet-cz-1"
VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
PARTS = frozenset({"p-name__itemLink", "p-name__itemName", "p-name__code"})
SKIPPED = frozenset({"client-table-heading", "p-name__codeLabel", "p-name__shipping"})
CODE = re.compile(r"\d{1,20}")
PAGE = re.compile(r"/klient/objednavky/strana-(\d{1,5})/")
ORDER_HEADING = re.compile(r"<h2>\s*Objednávka č\.\s*(\d{1,20})\s*</h2>")
# Shops with CSRF enabled reject forms without it; Shoptet JS normally injects it on submit.
CSRF_TOKEN = re.compile(r"shoptet\.csrf\.token\s*=\s*'([\w-]{1,200})'")
QUANTITY = re.compile(r"([\d\s,.]+?)\s*([^\d\s].*)?")


def _clean(text: str) -> str:
    return " ".join(text.split())


@dataclass
class _Cell:
    chunks: list[str] = field(default_factory=list)
    part_chunks: dict[str, list[str]] = field(default_factory=dict)
    definition_chunks: list[list[str]] = field(default_factory=list)
    href: str | None = None

    @property
    def text(self) -> str:
        return _clean("".join(self.chunks))

    def part(self, name: str) -> str | None:
        return _clean("".join(self.part_chunks[name])) if name in self.part_chunks else None

    @property
    def definitions(self) -> list[str]:
        return [_clean("".join(chunks)) for chunks in self.definition_chunks]


@dataclass
class _Row:
    classes: frozenset[str]
    cells: list[_Cell] = field(default_factory=list)


class _TableRows(HTMLParser):
    """Collect rows of the first table carrying `table_class`, cell text grouped by class."""

    def __init__(self, table_class: str) -> None:
        super().__init__(convert_charrefs=True)
        self.table_class = table_class
        self.depth = 0
        self.done = False
        self.stack: list[tuple[str, frozenset[str]]] = []
        self.rows: list[_Row] = []
        self.links: list[str] = []
        self.cell: _Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = frozenset((values.get("class") or "").split())
        href = values.get("href")
        if tag == "a" and href:
            self.links.append(href)
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
            self.rows.append(_Row(classes))
        elif tag in {"td", "th"} and self.rows:
            self.cell = _Cell()
            self.rows[-1].cells.append(self.cell)
            self.stack = []
        elif self.cell is not None:
            if tag == "a" and self.cell.href is None:
                self.cell.href = href
            if tag in {"dt", "dd"}:
                self.cell.definition_chunks.append([])
            if tag not in VOID:
                self.stack.append((tag, classes))

    def handle_endtag(self, tag: str) -> None:
        if not self.depth or self.done:
            return
        if tag == "table":
            self.depth -= 1
            self.done = not self.depth
        elif tag in {"td", "th"}:
            self.cell = None
        elif self.cell is not None:
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    def handle_data(self, data: str) -> None:
        if self.cell is None:
            return
        classes: frozenset[str] = frozenset().union(*(item[1] for item in self.stack))
        if classes & SKIPPED:
            return
        self.cell.chunks.append(data)
        if self.cell.definition_chunks:
            self.cell.definition_chunks[-1].append(data)
        for name in classes & PARTS:
            self.cell.part_chunks.setdefault(name, []).append(data)


def _table(html: str, table_class: str) -> tuple[list[_Row], list[str]]:
    parser = _TableRows(table_class)
    parser.feed(html)
    parser.close()
    return parser.rows, parser.links


class _EmailInput(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.email: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if self.email is None and tag == "input" and values.get("name") == "email":
            self.email = values.get("value") or None


def account_email(settings_html: str) -> str | None:
    parser = _EmailInput()
    parser.feed(settings_html)
    return parser.email


def csrf_token(login_html: str) -> str | None:
    match = CSRF_TOKEN.search(login_html)
    return match.group(1) if match else None


def order_code(detail_html: str) -> str | None:
    match = ORDER_HEADING.search(detail_html)
    return match.group(1) if match else None


def money(text: str) -> tuple[Decimal | None, str | None]:
    """Parse Shoptet price text such as '1 332,50 Kč' into (amount, currency)."""
    currency = "CZK" if "Kč" in text else None
    value = re.sub(r"\s", "", text.replace("Kč", "")).replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        return None, currency
    return Decimal(value), currency


def _date(text: str) -> str | None:
    try:
        return datetime.strptime(_clean(text), "%d.%m.%Y %H:%M").isoformat()
    except ValueError:
        return None


def parse_list(html: str, site: str) -> tuple[list[dict[str, Any]], dict[int, str]]:
    rows, links = _table(html, "customer-orders")
    orders: list[dict[str, Any]] = []
    for row in rows:
        cells = row.cells
        if len(cells) < 4 or not cells[0].href:
            continue
        code = parse_qs(urlsplit(cells[0].href).query).get("code", [""])[0]
        if not CODE.fullmatch(code):
            continue
        total = money(cells[3].text)[0]
        orders.append(
            {
                "code": code,
                "date": _date(cells[1].text),
                "total": format(total, "f") if total is not None else None,
            }
        )
    pages = {
        int(match.group(1)): urljoin(site, match.group(0))
        for link in links
        if (match := PAGE.search(link))
    }
    return orders, pages


def _line(
    name: str | None,
    quantity: Decimal,
    unit: str | None,
    unit_price: Decimal,
    original: Decimal,
    code: str | None,
    discounts: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "name": name,
        "quantity": format(quantity, "f"),
        "unit": unit,
        "unit_price": format(unit_price, "f"),
        "original_amount": format(original, "f"),
        "code": code,
        "is_weight": False,
        "discounts": discounts,
    }


def validate_order(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any]:
    issues: list[str] = []
    currencies: set[str] = set()

    def amount(text: str) -> Decimal | None:
        value, currency = money(text)
        if currency:
            currencies.add(currency)
        if value is None:
            issues.append("invalid_amount")
        return value

    rows, _ = _table(str(detail.get("html", "")), "table-order-details")
    items: list[dict[str, Any]] = []
    discounts: list[dict[str, str]] = []
    totals: dict[str, str] = {}
    paid = Decimal(0)
    products = 0
    for row in rows:
        cells = row.cells
        if "row-summary" in row.classes and cells:
            values = cells[0].definitions
            totals = {values[i].rstrip(":"): values[i + 1] for i in range(0, len(values) - 1, 2)}
            continue
        if len(cells) != 7 or not cells[0].part_chunks:
            continue
        first = cells[0]
        line = amount(cells[6].text)
        name = first.part("p-name__itemLink") or first.part("p-name__itemName")
        if not name:
            issues.append("missing_item_name")
        code = first.part("p-name__code")
        if code is None:
            # Shipping, payment and coupon rows; free ones carry no spending information.
            if line is not None and line > 0:
                items.append(_line(name, Decimal(1), None, line, line, None, []))
                paid += line
            elif line is not None and line < 0:
                # Discounts are stored as positive savings, like Lidl source discounts.
                discounts.append({"name": name or "", "amount": format(-line, "f")})
                paid += line
            continue
        quantity_match = QUANTITY.fullmatch(cells[1].text)
        unit_price = amount(cells[3].text)
        if quantity_match is None or unit_price is None or line is None:
            issues.append("invalid_item_amount")
            continue
        quantity = Decimal(re.sub(r"\s", "", quantity_match.group(1)).replace(",", "."))
        original = (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if quantity <= 0 or original < line:
            issues.append("invalid_item_amount")
            continue
        percent = cells[4].text
        item_discounts = (
            [{"name": f"Sleva {percent}".strip(), "amount": format(original - line, "f")}]
            if original > line
            else []
        )
        items.append(
            _line(
                name, quantity, quantity_match.group(2), unit_price, original, code, item_discounts
            )
        )
        paid += line
        products += 1
    if not products:
        issues.append("items_unavailable")
    total = amount(totals.get("Cena celkem včetně DPH", ""))
    rounding = Decimal(0)
    if total is not None:
        # Shoptet sometimes shows 0 Kč for orders paid in full; the lines are what was paid.
        if total == 0 and paid > 0:
            total = paid
        rounding = total - paid
        # Shoptet rounds the gross total to whole CZK without listing a rounding row.
        if abs(rounding) >= 1:
            issues.append("totals_unreconciled")
    date = summary.get("date")
    if not date:
        issues.append("invalid_date")
    if currencies != {"CZK"}:
        issues.append("unknown_currency")
    if not expected_id or detail.get("id") != expected_id:
        issues.append("receipt_identity_mismatch")
    return {
        "id": expected_id,
        "date": date,
        "currency": "CZK" if currencies == {"CZK"} else None,
        "total": format(total, "f") if total is not None else None,
        "rounding": format(rounding, "f"),
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "shoptet-order",
    }
