"""Bolt Food order normalization from user_baskets and total_price_components.

Verified on 129 live orders: item total = (unit + options) x amount; discounts listed before
the subtotal row are item-level and already inside item prices; the paid total equals the
subtotal plus every fee and discount listed after it.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cartalytix.providers.bolt.client import BoltError

PARSER_VERSION = "bolt-cz-1"
FINAL_STATES = frozenset({"delivered"})


def money(value: object) -> Decimal | None:
    raw = value.get("value") if isinstance(value, dict) else value
    if raw is None or isinstance(raw, bool):
        return None
    try:
        result = Decimal(str(raw))
    except ArithmeticError:
        return None
    return result if result.is_finite() else None


def text(value: object) -> str | None:
    raw = value.get("value") if isinstance(value, dict) else value
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _item_discount(price: object, amount: int) -> Decimal:
    original = money(price.get("original_price")) if isinstance(price, dict) else None
    current = money(price)
    if original is None or current is None:
        return Decimal(0)
    return (original - current) * amount


def _item(item: dict[str, Any], discount_name: str, issues: list[str]) -> dict[str, Any]:
    amount = item.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        issues.append("invalid_quantity")
        amount = 0
    options = [option for option in item.get("options") or [] if isinstance(option, dict)]
    option_total = sum(
        (money(option.get("total_item_price")) or Decimal(0) for option in options), Decimal(0)
    )
    unit = money(item.get("unit_item_price"))
    total = money(item.get("total_item_price"))
    if unit is None or total is None:
        issues.append("invalid_item_amount")
    elif (unit + option_total) * amount != total:
        issues.append("quantity_unit_price_mismatch")
    discount = _item_discount(item.get("unit_item_price"), amount) + sum(
        (
            _item_discount(option.get("unit_item_price"), (option.get("amount") or 0) * amount)
            for option in options
            if isinstance(option.get("amount"), int)
        ),
        Decimal(0),
    )
    name = text(item.get("name"))
    if name is None:
        issues.append("missing_item_name")
    gross = total + discount if total is not None else None
    return {
        "name": name,
        "quantity": str(amount),
        "unit": None,
        "unit_price": format(unit + option_total, "f") if unit is not None else None,
        "original_amount": format(gross, "f") if gross is not None else None,
        "code": str(item.get("menu_item_id")) if item.get("menu_item_id") is not None else None,
        "is_weight": False,
        # Stored as positive savings, like Lidl source discounts.
        "discounts": [{"name": discount_name, "amount": format(discount, "f")}]
        if discount > 0
        else [],
    }


def validate_order(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any] | None:
    """Return None for orders that were not delivered (rejected, cancelled, in progress)."""
    if not expected_id or str(detail.get("order_id")) != expected_id:
        raise BoltError("receipt_identity_mismatch")
    if detail.get("order_state") not in FINAL_STATES:
        return None
    issues: list[str] = []
    components = [c for c in detail.get("total_price_components") or [] if isinstance(c, dict)]
    split = next((i for i, c in enumerate(components) if c.get("type") == "total"), None)
    if split is None:
        raise BoltError("provider_response_invalid")
    before, subtotal_row, after = components[:split], components[split], components[split + 1 :]
    item_discount_name = next(
        (
            text(c.get("data", {}).get("name")) or "Discount"
            for c in before
            if c.get("type") == "discount"
        ),
        "Discount",
    )
    items: list[dict[str, Any]] = []
    baskets = [basket for basket in detail.get("user_baskets") or [] if isinstance(basket, dict)]
    for basket in baskets:
        for item in basket.get("items") or []:
            if isinstance(item, dict):
                items.append(_item(item, item_discount_name, issues))
    if not items:
        issues.append("items_unavailable")
    subtotal = money(subtotal_row.get("data", {}).get("amount"))
    discounts: list[dict[str, str]] = []
    adjustments = Decimal(0)
    for component in after:
        data = component.get("data") or {}
        value = money(data.get("amount"))
        name = text(data.get("name"))
        if value is None or name is None:
            issues.append("invalid_price_component")
            continue
        adjustments += value
        if component.get("type") == "fee":
            items.append(
                {
                    "name": name,
                    "quantity": "1",
                    "unit": None,
                    "unit_price": format(value, "f"),
                    "original_amount": format(value, "f"),
                    "code": "fee",
                    "is_weight": False,
                    "discounts": [],
                }
            )
        elif component.get("type") == "discount":
            discounts.append({"name": name, "amount": format(-value, "f")})
        else:
            issues.append("unknown_price_component")
    total = money(detail.get("total_price"))
    net_items = [
        Decimal(item["original_amount"])
        - sum((Decimal(d["amount"]) for d in item["discounts"]), Decimal(0))
        for item in items
        if item.get("code") != "fee" and item.get("original_amount") is not None
    ]
    if total is None or subtotal is None:
        issues.append("invalid_total")
    elif sum(net_items, Decimal(0)) != subtotal or subtotal + adjustments != total:
        issues.append("totals_unreconciled")
    timestamp = detail.get("order_created_timestamp", summary.get("order_created_timestamp"))
    date = (
        datetime.fromtimestamp(timestamp, UTC).isoformat()
        if isinstance(timestamp, int) and not isinstance(timestamp, bool)
        else None
    )
    if date is None:
        issues.append("invalid_date")
    currency = str((detail.get("total_price") or {}).get("currency") or "").upper()
    if len(currency) != 3:
        issues.append("unknown_currency")
    return {
        "id": expected_id,
        "date": date,
        "currency": currency or None,
        "total": format(total, "f") if total is not None else None,
        "rounding": "0",
        "merchant": text(detail.get("provider_name")) or "Bolt Food",
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "bolt-order",
    }
