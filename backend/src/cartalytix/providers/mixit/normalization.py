"""Mixit order normalization; unitemized shipping/discounts become explicit adjustments."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from cartalytix.providers.mixit.client import MixitError

PARSER_VERSION = "mixit-cz-1"
FINAL_STATES = frozenset({"shipped", "completed"})
CURRENCIES = {"Kč": "CZK", "CZK": "CZK", "€": "EUR", "EUR": "EUR"}
# Slugs, translated by the UI; see cartalytix.search.SYSTEM_NAMES.
SHIPPING_NAME = "shipping_and_fees"
DISCOUNT_NAME = "discounts_and_credits"


def amount(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    return Decimal(text)


def _line(source: dict[str, Any], code: str | None, issues: list[str]) -> dict[str, Any]:
    quantity = source.get("quantity")
    unit_price = amount(source.get("price"))
    total = amount(source.get("totalPrice"))
    name = source.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append("missing_item_name")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        issues.append("invalid_quantity")
    elif unit_price is None or total is None:
        issues.append("invalid_item_amount")
    elif unit_price * quantity != total:
        issues.append("quantity_unit_price_mismatch")
    return {
        "name": name.strip() if isinstance(name, str) else None,
        "quantity": str(quantity) if isinstance(quantity, int) else None,
        "unit": None,
        "unit_price": format(unit_price, "f") if unit_price is not None else None,
        "original_amount": format(total, "f") if total is not None else None,
        "code": code,
        "is_weight": False,
        "discounts": [],
    }


def validate_order(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any] | None:
    """Return None for orders that are not final yet; they import once shipped."""
    order = detail.get("order")
    if not expected_id or not isinstance(order, dict) or order.get("hash") != expected_id:
        raise MixitError("receipt_identity_mismatch")
    if order.get("state") not in FINAL_STATES:
        return None
    issues: list[str] = []
    items: list[dict[str, Any]] = []
    products = order.get("orderProducts")
    mixes = order.get("orderMixes")
    if not isinstance(products, list) or not isinstance(mixes, list):
        raise MixitError("provider_response_invalid")
    for product in products:
        if not isinstance(product, dict):
            issues.append("invalid_item")
            continue
        code = product.get("productID")
        items.append(_line(product, str(code) if code is not None else None, issues))
    for mix in mixes:
        if not isinstance(mix, dict):
            issues.append("invalid_item")
            continue
        code = mix.get("mixID")
        items.append(_line(mix, f"mix:{code}" if code is not None else None, issues))
    if not items:
        issues.append("items_unavailable")
    total = amount(order.get("totalPrice", summary.get("totalPrice")))
    if total is None:
        issues.append("invalid_total")
    discounts: list[dict[str, str]] = []
    line_totals = [item.get("original_amount") for item in items]
    if total is not None and items and None not in line_totals:
        # Mixit exposes no shipping, voucher or credit amounts; keep the paid total exact.
        gap = total - sum((Decimal(value) for value in line_totals if value), Decimal(0))
        if gap > 0:
            items.append(
                {
                    "name": SHIPPING_NAME,
                    "quantity": "1",
                    "unit": None,
                    "unit_price": format(gap, "f"),
                    "original_amount": format(gap, "f"),
                    "code": None,
                    "is_weight": False,
                    "discounts": [],
                }
            )
        elif gap < 0:
            # Discounts are stored as positive savings, like Lidl source discounts.
            discounts.append({"name": DISCOUNT_NAME, "amount": format(-gap, "f")})
    try:
        date = datetime.strptime(str(order.get("createdAt")), "%d. %m. %Y").date().isoformat()
    except ValueError:
        date = None
        issues.append("invalid_date")
    currency = CURRENCIES.get(str(order.get("currency", "")))
    if currency is None:
        issues.append("unknown_currency")
    return {
        "id": expected_id,
        "date": date,
        "currency": currency,
        "total": format(total, "f") if total is not None else None,
        "rounding": "0",
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "mixit-order",
    }
