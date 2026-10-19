"""Kaufland Card receipt normalization.

Verified on a live CZ receipt: amounts are integer minor units, `unitPrice` is the regular
price, `total` the paid line amount after Kaufland Card discounts, `sum` equals the paid
line totals and `saving` equals the summed line discounts. Quantities may arrive as "1,00".
Refund positions are not verified live; they are imported as negative lines.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cartalytix.providers.kaufland.client import KauflandError

PARSER_VERSION = "kaufland-cz-1"
DISCOUNT_NAME = "Kaufland Card"
WEIGHT_UNITS = frozenset({"KG", "G"})
CENT = Decimal("0.01")


def minor(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return Decimal(value) / 100


def quantity(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        result = Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        return None
    return result if result.is_finite() and result > 0 else None


def text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _item(position: dict[str, Any], sign: int, issues: list[str]) -> tuple[dict[str, Any], Decimal]:
    count = quantity(position.get("quantity"))
    price = minor(position.get("unitPrice"))
    paid = minor(position.get("total"))
    if count is None:
        issues.append("invalid_quantity")
    if price is None or paid is None:
        issues.append("invalid_item_amount")
    original = (
        (count * price).quantize(CENT, ROUND_HALF_UP) if count and price is not None else None
    )
    discounts: list[dict[str, str]] = []
    if original is not None and paid is not None:
        if paid > original:
            issues.append("quantity_unit_price_mismatch")
        elif paid < original:
            discounts.append(
                {"name": DISCOUNT_NAME, "amount": text(sign * (original - paid)) or "0"}
            )
    name = position.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append("missing_item_name")
    unit = position.get("quantityUnit")
    code = position.get("gtin") or position.get("itemno")
    return {
        "name": name.strip() if isinstance(name, str) else None,
        "quantity": text(sign * count) if count is not None else None,
        "unit": unit.lower() if isinstance(unit, str) and unit else None,
        "unit_price": text(price),
        "original_amount": text(sign * original) if original is not None else None,
        "code": str(code) if code else None,
        "is_weight": isinstance(unit, str) and unit.upper() in WEIGHT_UNITS,
        "discounts": discounts,
    }, sign * (paid or Decimal(0))


def validate_receipt(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any]:
    if not expected_id or detail.get("id") != expected_id:
        raise KauflandError("receipt_identity_mismatch")
    issues: list[str] = []
    items: list[dict[str, Any]] = []
    paid = Decimal(0)
    for key, sign in (("positions", 1), ("refundPositions", -1)):
        for position in detail.get(key) or []:
            if not isinstance(position, dict):
                issues.append("invalid_item")
                continue
            item, amount = _item(position, sign, issues)
            items.append(item)
            paid += amount
    if not items:
        issues.append("items_unavailable")
    total = minor(detail.get("sum"))
    if total is None:
        raise KauflandError("provider_response_invalid")
    if paid != total:
        issues.append("totals_unreconciled")
    try:
        date = datetime.fromisoformat(str(detail.get("timestamp"))).isoformat()
    except ValueError:
        date = None
        issues.append("invalid_date")
    currency = str(detail.get("currency") or "").upper()
    if len(currency) != 3:
        issues.append("unknown_currency")
    return {
        "id": expected_id,
        "date": date,
        "currency": currency or None,
        "total": text(total),
        "rounding": "0",
        "merchant": "Kaufland",
        "items": items,
        "discounts": [],
        "issues": sorted(set(issues)),
        "source_format": "kaufland-transaction",
    }
