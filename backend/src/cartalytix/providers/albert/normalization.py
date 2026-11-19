"""Normalize Albert CZ receipt amounts without binary floating-point arithmetic."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from cartalytix.providers.albert.client import AlbertError

PARSER_VERSION = "albert-cz-1"
CENT = Decimal("0.01")
WEIGHT_MEASUREMENTS = frozenset({"E", "P", "M"})


def _number(value: object) -> Decimal | None:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (str, int, float, Decimal))
    ):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _amount(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _name(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _discount(value: object, issues: list[str]) -> tuple[list[dict[str, str]], Decimal]:
    if value is None:
        return [], Decimal(0)
    if not isinstance(value, dict):
        issues.append("invalid_discount")
        return [], Decimal(0)
    amount = _number(value.get("totalDiscount"))
    name = _name(value.get("description"))
    if amount is None or name is None:
        issues.append("invalid_discount")
        return [], Decimal(0)
    if amount == 0:
        return [], Decimal(0)
    return [{"name": name, "amount": format(-amount, "f")}], -amount


def validate_receipt(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any]:
    if not expected_id or detail.get("receiptNumber") != expected_id:
        raise AlbertError("receipt_identity_mismatch")
    issues: list[str] = []
    total = _number(detail.get("receiptSum", summary.get("receiptSum")))
    if total is None:
        raise AlbertError("provider_response_invalid")
    status = detail.get("purchaseStatus", summary.get("purchaseStatus"))
    if status not in {"", "SUCCESS"}:
        issues.append("unknown_purchase_status")
    source_items = detail.get("purchasedItems")
    if not isinstance(source_items, list):
        source_items = []
        issues.append("items_unavailable")
    items: list[dict[str, Any]] = []
    paid = Decimal(0)
    for source in source_items:
        if not isinstance(source, dict):
            issues.append("invalid_item")
            continue
        quantity = _number(source.get("quantity", 1))
        price = _number(source.get("pricePerItem"))
        original = _number(source.get("totalPrice"))
        name = _name(source.get("desc"))
        measurement = source.get("measurement")
        is_weight = isinstance(measurement, str) and measurement in WEIGHT_MEASUREMENTS
        if name is None:
            issues.append("missing_item_name")
        if quantity is None or quantity == 0:
            issues.append("invalid_quantity")
        if price is None or original is None:
            issues.append("invalid_item_amount")
        elif quantity is not None:
            expected = quantity * price
            # Albert weighed lines may be rounded to 0.1 CZK; keep the printed total.
            if expected.quantize(CENT, ROUND_HALF_UP) != original and (
                not is_weight or expected.quantize(Decimal("0.1"), ROUND_HALF_UP) != original
            ):
                issues.append("quantity_unit_price_mismatch")
        discounts, saving = _discount(source.get("discount"), issues)
        paid += (original or Decimal(0)) - saving
        code = source.get("goldId")
        items.append(
            {
                "name": name,
                "quantity": _amount(quantity),
                "unit": "kg" if is_weight else None,
                "unit_price": _amount(price),
                "original_amount": _amount(original),
                "code": str(code)
                if isinstance(code, (str, int)) and not isinstance(code, bool)
                else None,
                "is_weight": is_weight,
                "discounts": discounts,
            }
        )
    if not items:
        issues.append("items_unavailable")
    discounts: list[dict[str, str]] = []
    source_discounts = detail.get("discounts")
    if source_discounts is not None and not isinstance(source_discounts, list):
        issues.append("invalid_discount")
    for source in source_discounts if isinstance(source_discounts, list) else []:
        rows, saving = _discount(source, issues)
        discounts.extend(rows)
        paid -= saving
    rounding = _number(detail.get("roundingAmount", detail.get("rounding", 0)))
    if rounding is None:
        issues.append("invalid_rounding")
        rounding = Decimal(0)
    if paid + rounding != total:
        issues.append("totals_unreconciled")
    try:
        date = datetime.fromisoformat(
            str(detail.get("receiptCreationTime", summary.get("receiptCreationTime")))
        ).isoformat()
    except ValueError, OverflowError:
        date = None
        issues.append("invalid_date")
    currency = detail.get("currency", "CZK")
    if currency != "CZK":
        issues.append("unknown_currency")
    return {
        "id": expected_id,
        "date": date,
        "currency": currency,
        "total": _amount(total),
        "rounding": _amount(rounding),
        "merchant": "Albert",
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "albert-receipt",
    }
