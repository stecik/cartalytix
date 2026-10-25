"""Globus receipt normalization from the receipt PDF text.

Verified on one live receipt: item amounts, the VAT breakdown and payments all sum to
CELKEM. The list total is rounded to whole crowns, so the printed PDF total is used.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from cartalytix.providers.globus.client import GlobusError
from cartalytix.providers.globus.receipt_pdf import CENT, parse

PARSER_VERSION = "globus-cz-pdf-3"
MERCHANT = "Globus"


def _list_total_matches(listed: object, printed: Decimal) -> bool:
    if isinstance(listed, bool) or not isinstance(listed, int | float):
        return True
    accepted = {printed, printed.quantize(Decimal(1), ROUND_HALF_UP)}
    return Decimal(str(listed)).quantize(CENT) in accepted


def validate_receipt(
    summary: dict[str, Any], detail: dict[str, Any], expected_id: str
) -> dict[str, Any] | None:
    if not expected_id or detail.get("id") != expected_id:
        raise GlobusError("receipt_identity_mismatch")
    text = detail.get("text")
    if not isinstance(text, str):
        raise GlobusError("provider_response_invalid")
    parsed = parse(text)
    issues = set(parsed.get("issues", []))
    total = parsed.get("total")
    if total is not None and not _list_total_matches(summary.get("total"), Decimal(total)):
        issues.add("list_total_mismatch")
    items = [
        {
            "name": item.get("name"),
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "unit_price": item.get("unit_price"),
            "original_amount": item.get("amount"),
            "code": item.get("code"),
            "is_weight": item.get("is_weight"),
            "discounts": [],
        }
        for item in parsed.get("items", [])
    ]
    return {
        "id": expected_id,
        "date": parsed.get("date"),
        "currency": parsed.get("currency"),
        "total": total,
        "rounding": "0",
        "merchant": MERCHANT,
        "items": items,
        "discounts": [],
        "issues": sorted(issues),
        "source_format": parsed.get("source_format"),
    }
