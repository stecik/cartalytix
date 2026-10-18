"""Wolt purchase normalization.

Verified on 21 live orders: amounts are integer minor units; item total =
count x (price + option values); the paid total equals items_price + delivery_price
minus discounts, credits and tokens. service_fee is reported but not part of that total.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cartalytix.providers.wolt.client import WoltError

PARSER_VERSION = "wolt-cz-1"
FINAL_STATES = frozenset({"delivered"})
DELIVERY_NAME = "Delivery"
CREDITS_NAME = "Wolt credits"
TOKENS_NAME = "Wolt tokens"


def minor(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return Decimal(value) / 100


def text(value: object) -> str:
    return format(value, "f") if isinstance(value, Decimal) else ""


def _item(item: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    count = item.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        issues.append("invalid_quantity")
        count = 0
    price = minor(item.get("price"))
    total = minor(item.get("end_amount"))
    options = Decimal(0)
    for option in item.get("options") or []:
        if not isinstance(option, dict):
            continue
        for value in option.get("values") or []:
            if isinstance(value, dict):
                options += (minor(value.get("price")) or Decimal(0)) * (value.get("count") or 1)
    if price is None or total is None:
        issues.append("invalid_item_amount")
    elif count * (price + options) != total:
        issues.append("quantity_unit_price_mismatch")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append("missing_item_name")
    return {
        "name": name.strip() if isinstance(name, str) else None,
        "quantity": str(count),
        "unit": None,
        "unit_price": text(price + options) if price is not None else None,
        "original_amount": text(total) or None,
        "code": str(item.get("id")) if item.get("id") is not None else None,
        "is_weight": False,
        "discounts": [],
    }


def _date(summary: dict[str, Any], detail: dict[str, Any]) -> str | None:
    # Detail times are formatted per app-language; the history epoch is language-independent.
    stamp = summary.get("payment_time_ts")
    if isinstance(stamp, int) and not isinstance(stamp, bool) and stamp > 0:
        return datetime.fromtimestamp(stamp / 1000, UTC).isoformat()
    try:
        zone = ZoneInfo(str(detail.get("venue_timezone") or "Europe/Prague"))
    except ZoneInfoNotFoundError, ValueError:
        zone = ZoneInfo("Europe/Prague")
    try:
        local = datetime.strptime(str(detail.get("creation_time")), "%d/%m/%Y, %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=zone).isoformat()


def validate_order(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any] | None:
    """Return None for purchases that were not delivered."""
    if not expected_id or detail.get("order_id") != expected_id:
        raise WoltError("receipt_identity_mismatch")
    if detail.get("status") not in FINAL_STATES:
        return None
    issues: list[str] = []
    items = [_item(item, issues) for item in detail.get("items") or [] if isinstance(item, dict)]
    if not items:
        issues.append("items_unavailable")
    items_price = minor(detail.get("items_price"))
    delivery = minor(detail.get("delivery_price"))
    total = minor(detail.get("total_price"))
    if items_price is None or delivery is None or total is None:
        raise WoltError("provider_response_invalid")
    if delivery:
        items.append(
            {
                "name": DELIVERY_NAME,
                "quantity": "1",
                "unit": None,
                "unit_price": text(delivery),
                "original_amount": text(delivery),
                "code": "delivery",
                "is_weight": False,
                "discounts": [],
            }
        )
    discounts: list[dict[str, str]] = []
    for discount in detail.get("discounts") or []:
        amount = minor(discount.get("amount")) if isinstance(discount, dict) else None
        if amount is None:
            issues.append("invalid_discount")
            continue
        title = discount.get("title")
        discounts.append(
            {
                "name": title if isinstance(title, str) and title else "Discount",
                "amount": text(amount),
            }
        )
    for key, name in (("credits", CREDITS_NAME), ("tokens", TOKENS_NAME)):
        amount = minor(detail.get(key))
        if amount:
            # Credits and tokens lower the paid amount, so they are stored like discounts.
            discounts.append({"name": name, "amount": text(amount)})
    line_total = sum(
        (
            Decimal(item["original_amount"])
            for item in items
            if item.get("code") != "delivery" and item.get("original_amount")
        ),
        Decimal(0),
    )
    savings = sum((Decimal(d["amount"]) for d in discounts), Decimal(0))
    if line_total != items_price or items_price + delivery - savings != total:
        issues.append("totals_unreconciled")
    date = _date(summary, detail)
    if date is None:
        issues.append("invalid_date")
    currency = str(detail.get("currency") or "").upper()
    if len(currency) != 3:
        issues.append("unknown_currency")
    venue = detail.get("venue_name", summary.get("venue_name"))
    return {
        "id": expected_id,
        "date": date,
        "currency": currency or None,
        "total": text(total),
        "rounding": "0",
        "merchant": venue.strip()[:256] if isinstance(venue, str) and venue.strip() else "Wolt",
        "items": items,
        "discounts": discounts,
        "issues": sorted(set(issues)),
        "source_format": "wolt-order",
    }
