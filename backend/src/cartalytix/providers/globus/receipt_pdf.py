"""Parses the text layer of a Globus receipt PDF; unknown lines are kept, never guessed."""

import io
import logging
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

AMOUNT = r"-?\d{1,3}(?:[  ]\d{3})+,\d{2}|-?\d+,\d{2}"
HEADER = re.compile(
    r"\(datum \d{2}\.\d{2}\.\d{4}, provoz (?P<house>\d+), "
    r"pokladna (?P<cash_desk>\d+), účet (?P<receipt>\d+)\)"
)
STAMP = re.compile(r"^(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})\s+(?P<t>\d{2}:\d{2}:\d{2})$")
SINGLE = re.compile(rf"^(?P<name>\S.*?)\s+\((?P<vat>\d)\)\s+(?P<amount>{AMOUNT})$")
HEAD = re.compile(r"^(?P<name>\S.*?)\s+\((?P<vat>\d)\)$")
QUANTITY = re.compile(
    rf"^(?P<quantity>-?\d+,\d{{3}})\s+x\s+(?P<unit_price>{AMOUNT})\s+Kč/(?P<unit>\S+)"
    rf"\s+(?P<amount>{AMOUNT})$"
)
# EAN or in-store PLU printed below an item name.
CODE = re.compile(r"^\d{4,14}$")
TOTAL = re.compile(rf"^CELKEM\s+(?P<amount>{AMOUNT})\s+Kč$")
SAVINGS = re.compile(rf"^UŠETŘILI JSTE\s+(?P<amount>{AMOUNT})\s+Kč$")
PAYMENT = re.compile(rf"^(?P<method>[^\W\d][^:\d]*?)\s{{2,}}(?P<amount>{AMOUNT})$")
VAT = re.compile(rf"^(?P<code>\d)\s+(?P<rate>\d+)%\s+DPH\s+(?P<base>{AMOUNT})\s+(?P<vat>{AMOUNT})$")
CENT = Decimal("0.01")


def money(text: str) -> Decimal:
    return Decimal(re.sub(r"[  ]", "", text).replace(",", "."))


def pdf_text(data: bytes) -> str:
    from pdfminer.high_level import extract_text

    # pdfminer warns about the receipt font descriptors on every page.
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    return extract_text(io.BytesIO(data))


def parse_items(block: list[str], issues: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """A "(vat)" line either carries its amount or is followed by a quantity line.

    Newer receipts print one product code line right below the item name.
    """
    items: list[dict[str, Any]] = []
    unparsed: list[str] = []

    def add(
        head: re.Match[str],
        code: str | None,
        quantity: str,
        unit: str | None,
        price: str,
        amount: str,
    ) -> None:
        count, unit_price, value = money(quantity), money(price), money(amount)
        if (count * unit_price).quantize(CENT, ROUND_HALF_UP) != value:
            issues.add("quantity_price_mismatch")
        items.append(
            {
                "name": head.group("name"),
                "vat_code": int(head.group("vat")),
                "code": code,
                "quantity": str(count),
                "unit": unit,
                "unit_price": str(unit_price),
                "amount": str(value),
                "is_weight": unit == "kg",
            }
        )

    pending: re.Match[str] | None = None
    pending_code: str | None = None
    # Only the line right after a single-line item may carry that item's code.
    coded_last = True
    for line in block:
        if pending is not None:
            if pending_code is None and CODE.match(line):
                pending_code = line
                continue
            quantity = QUANTITY.match(line)
            if quantity:
                add(
                    pending,
                    pending_code,
                    quantity.group("quantity"),
                    quantity.group("unit"),
                    quantity.group("unit_price"),
                    quantity.group("amount"),
                )
                pending, pending_code, coded_last = None, None, True
                continue
            unparsed.extend(
                [pending.group(0)] + ([pending_code] if pending_code is not None else [])
            )
            pending, pending_code = None, None
        if single := SINGLE.match(line):
            amount = single.group("amount")
            add(single, None, "1", None, amount, amount)
            coded_last = False
        elif head := HEAD.match(line):
            pending = head
        elif not coded_last and CODE.match(line):
            items[-1]["code"] = line
            coded_last = True
        else:
            unparsed.append(line)
            coded_last = True
    if pending is not None:
        unparsed.extend([pending.group(0)] + ([pending_code] if pending_code is not None else []))
    return items, unparsed


def parse(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    issues: set[str] = set()
    header = next((m for line in lines if (m := HEADER.search(line))), None)
    stamp = next((m for line in lines if (m := STAMP.match(line))), None)
    start = next((i + 1 for i, line in enumerate(lines) if line == "Kč"), None)
    end = next(
        (i for i, line in enumerate(lines[start or 0 :], start or 0) if line.startswith("____")),
        len(lines),
    )
    items, unparsed = parse_items(lines[start:end] if start is not None else [], issues)
    if not items:
        issues.add("items_unavailable")
    if unparsed:
        issues.add("unparsed_line")
    total_at, total = next(
        ((i, money(m.group("amount"))) for i, line in enumerate(lines) if (m := TOTAL.match(line))),
        (None, None),
    )
    savings = next(
        (money(m.group("amount")) for line in lines if (m := SAVINGS.match(line))),
        None,
    )
    vat_at = next((i for i, line in enumerate(lines) if "Zákl.DPH" in line), len(lines))
    payments = [
        {"method": m.group("method"), "amount": str(money(m.group("amount")))}
        for line in lines[(total_at if total_at is not None else end) + 1 : vat_at]
        if (m := PAYMENT.match(line)) and m.group("method").isupper()
    ]
    vat = [
        {
            "code": int(m.group("code")),
            "rate": int(m.group("rate")),
            "base": str(money(m.group("base"))),
            "vat": str(money(m.group("vat"))),
        }
        for line in lines[vat_at:]
        if (m := VAT.match(line))
    ]
    sums = {
        "items": sum((Decimal(i["amount"]) for i in items), Decimal(0)),
        "vat": sum((Decimal(v["base"]) + Decimal(v["vat"]) for v in vat), Decimal(0)),
        "payments": sum((Decimal(p["amount"]) for p in payments), Decimal(0)),
    }
    for name, found in (
        ("header", header),
        ("date", stamp),
        ("vat", vat),
        ("payments", payments),
    ):
        if not found:
            issues.add("missing_" + name)
    if total is None:
        issues.add("missing_total")
    else:
        issues.update(f"{name}_unreconciled" for name, value in sums.items() if value != total)
    return {
        **{
            key: int(header.group(key)) if header else None
            for key in ("house", "cash_desk", "receipt")
        },
        "date": f"{stamp['y']}-{stamp['m']}-{stamp['d']}T{stamp['t']}" if stamp else None,
        "currency": "CZK",
        "total": str(total) if total is not None else None,
        "savings": str(savings) if savings is not None else None,
        "items": items,
        "vat": vat,
        "payments": payments,
        "unparsed_lines": unparsed,
        "issues": sorted(issues),
        "reconciliation": {f"{name}_sum": str(value) for name, value in sums.items()},
        "source_format": "pdf_text",
    }
