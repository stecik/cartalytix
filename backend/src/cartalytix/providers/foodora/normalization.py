"""foodora order normalization; fees become items, discounts receipt-level savings."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from cartalytix.providers.foodora.client import FoodoraError

PARSER_VERSION = "foodora-cz-1"
SUBTOTAL = "NEXTGEN_CART_SUBTOTAL"
TOTAL = "NEXTGEN_TOTAL_VAT"
# Fee and discount lines are stored as slugs the UI translates; see cartalytix.search.
SLUGS = {
    "NEXTGEN_DELIVERY_FEE": "delivery_fee",
    "NEXTGEN_CART_SERVICE_FEE": "service_fee",
    "NEXTGEN_CART_CONTAINER_CHARGES": "container_charges",
    "NEXTGEN_DRIVER_TIP": "rider_tip",
    "NEXTGEN_CART_DISCOUNT": "discount",
    "NEXTGEN_COUT_VOUCHER": "voucher",
}
UNITEMIZED_DISCOUNT = "discounts_and_credits"


def amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        return None
    return result if result.is_finite() else None


def vendor_name(detail: dict[str, Any]) -> str | None:
    """Restaurant the order was placed with, like Bolt and Wolt venues."""
    vendor = detail.get("vendor")
    name = vendor.get("name") if isinstance(vendor, dict) else None
    return name.strip()[:256] if isinstance(name, str) and name.strip() else None


def _line(name: str, quantity: Decimal, total: Decimal, code: str | None) -> dict[str, Any]:
    return {
        "name": name,
        "quantity": format(quantity, "f"),
        "unit": None,
        "unit_price": format(total / quantity, "f"),
        "original_amount": format(total, "f"),
        "code": code,
        "is_weight": False,
        "discounts": [],
    }


def validate_order(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any] | None:
    """Return None for orders that are not delivered yet; they import once delivered."""
    if not expected_id or detail.get("order_code") != expected_id:
        raise FoodoraError("receipt_identity_mismatch")
    flags = detail.get("status_flags") or {}
    if flags.get("is_canceled") or not (flags.get("is_delivered") or flags.get("is_completed")):
        return None
    issues: list[str] = []
    items: list[dict[str, Any]] = []
    line_sum = Decimal(0)
    for product in detail.get("order_products") or []:
        quantity = amount(product.get("quantity"))
        total = amount(product.get("total_price"))
        name = product.get("name")
        if not isinstance(name, str) or not name.strip():
            issues.append("missing_item_name")
            continue
        if quantity is None or quantity <= 0 or total is None:
            issues.append("invalid_item_amount")
            continue
        code = product.get("variation_id")
        items.append(_line(name.strip(), quantity, total, str(code) if code else None))
        line_sum += total
    if not items:
        issues.append("items_unavailable")
    subtotal = amount(detail.get("subtotal"))
    paid = amount(detail.get("total_value"))
    if subtotal is None or line_sum != subtotal:
        issues.append("subtotal_mismatch")
    discounts: list[dict[str, str]] = []
    adjustment = Decimal(0)
    for fee in detail.get("dynamic_fees") or []:
        key = str(fee.get("translation_key") or "")
        value = amount(fee.get("value"))
        if key in {SUBTOTAL, TOTAL} or not value:
            continue
        name = SLUGS.get(key, key.removeprefix("NEXTGEN_").lower())
        adjustment += value
        if value > 0:
            items.append(_line(name, Decimal(1), value, None))
        else:
            # Discounts are stored as positive savings, like Lidl source discounts.
            discounts.append({"name": name, "amount": format(-value, "f")})
    tip = amount(detail.get("rider_tip")) or Decimal(0)
    if tip and not any(item.get("name") == "rider_tip" for item in items):
        items.append(_line("rider_tip", Decimal(1), tip, None))
        adjustment += tip
    if paid is None:
        issues.append("invalid_total")
    elif subtotal is not None:
        gap = paid - subtotal - adjustment
        # Older orders (2021) omit promotion lines; the paid total matches the card charge.
        if gap < 0:
            discounts.append({"name": UNITEMIZED_DISCOUNT, "amount": format(-gap, "f")})
        elif gap > 0:
            issues.append("totals_unreconciled")
    ordered = detail.get("ordered_at") or {}
    try:
        # Local wall time; the sync engine applies Europe/Prague to naive timestamps.
        date = datetime.strptime(str(ordered.get("date")), "%Y-%m-%d %H:%M:%S").isoformat()
    except ValueError:
        date = None
        issues.append("invalid_date")
    if ordered.get("timezone") not in (None, "Europe/Prague"):
        issues.append("unexpected_timezone")
    return {
        "id": expected_id,
        "date": date,
        "merchant": vendor_name(detail) or "Foodora",
        # foodora.cz bills in CZK; the payload has no currency field.
        "currency": "CZK",
        "total": format(paid, "f") if paid is not None else None,
        "rounding": "0",
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "foodora-order",
    }
