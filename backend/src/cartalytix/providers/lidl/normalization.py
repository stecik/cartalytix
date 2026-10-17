"""Conservative CZ normalization ported from verified Lidl experiment."""

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .client import LidlError
from .receipt_html import parse_html

PARSER_VERSION = "lidl-cz-1"


def number(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not money")  # noqa: TRY004 - Uniform numeric validation contract.
    text = re.sub(r"[\s\u00a0\u202f]", "", str(value))
    if "," in text:
        if "." in text:
            raise ValueError("Ambiguous decimal separators")
        text = text.replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise ValueError("Invalid numeric value")
    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError("Invalid numeric value") from None
    if not result.is_finite():
        raise ValueError("Non-finite numeric value")
    return format(result, "f")


def normalize(detail: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []

    def numeric(value: object, field: str, *, required: bool = True) -> str | None:
        try:
            result = number(value)
            if result is None and required:
                issues.append("missing_" + field)
            if result is not None:
                scale = 6 if field in {"quantity", "unit_price", "tare_quantity"} else 2
                decimal = Decimal(result)
                if abs(decimal) >= Decimal(10) ** (18 - scale) or decimal != decimal.quantize(
                    Decimal(10) ** -scale
                ):
                    issues.append(field + "_precision_exceeded")
            return result
        except ValueError:
            issues.append("invalid_" + field)
            return None

    items: list[dict[str, Any]] = []
    lines = detail.get("itemsLine")
    html_currency = None
    html_rounding = None
    source_format = "itemsLine"
    if isinstance(detail.get("htmlPrintedReceipt"), str):
        html_lines, html_issues, html_currency, html_rounding = parse_html(
            detail["htmlPrintedReceipt"]
        )
        if not lines:
            lines = html_lines
            issues.extend(html_issues)
            source_format = "htmlPrintedReceipt"
    if not isinstance(lines, list) or not lines:
        issues.append("items_unavailable")
        lines = []
    for line in lines:
        if not isinstance(line, dict):
            issues.append("invalid_item")
            continue
        discounts = line.get("discounts")
        if discounts is None:
            discounts = []
        if not isinstance(discounts, list):
            issues.append("invalid_discounts")
            discounts = []
        parsed_discounts = []
        for discount in discounts:
            if not isinstance(discount, dict):
                issues.append("invalid_discount")
                continue
            parsed_discounts.append(
                {
                    **discount,
                    "amount": numeric(discount.get("amount"), "discount_amount"),
                }
            )
        if not line.get("name"):
            issues.append("missing_item_name")
        deposit = line.get("deposit")
        deposit_amount = None
        if deposit is not None:
            if isinstance(deposit, dict):
                deposit_amount = numeric(deposit.get("amount"), "deposit_amount")
            else:
                issues.append("invalid_deposit")
        items.append(
            {
                "name": line.get("name"),
                "code": line.get("codeInput"),
                "quantity": numeric(line.get("quantity"), "quantity"),
                "unit_price": numeric(line.get("currentUnitPrice"), "unit_price"),
                "original_amount": numeric(line.get("originalAmount"), "original_amount"),
                "is_weight": line.get("isWeight"),
                "unit": line.get("unit")
                if line.get("parsedFromHtml")
                else "kg"
                if line.get("isWeight") is True
                else "piece"
                if line.get("isWeight") is False
                else None,
                "discounts": parsed_discounts,
                "deposit": line.get("deposit"),
                "deposit_amount": deposit_amount,
                "warranty": line.get("warrantyText"),
                "tare_quantity": numeric(line.get("tareQuantity"), "tare_quantity", required=False),
                "quantity_source": line.get("quantitySource", "itemsLine"),
                "code_kind": "internal_article_id" if line.get("parsedFromHtml") else "source_code",
                "printed_net_amount": numeric(
                    line.get("printedNetAmount"), "printed_net_amount", required=False
                ),
            }
        )
    currency = detail.get("currency")
    if isinstance(currency, dict):
        currency = currency.get("code")
    currency = currency or html_currency
    if not currency:
        issues.append("missing_currency")
    date = detail.get("date")
    try:
        if not isinstance(date, str):
            raise TypeError
        datetime.fromisoformat(date)
    except TypeError, ValueError:
        issues.append("invalid_date")
    total_amount = numeric(detail.get("totalAmount"), "total")
    rounding_amount = numeric(html_rounding, "rounding", required=False)
    rounding_source = "htmlPrintedReceipt" if rounding_amount is not None else None
    payments = detail.get("payments")
    if isinstance(payments, list):
        payment_roundings = [
            numeric(payment["roundingDifference"], "payment_rounding")
            for payment in payments
            if isinstance(payment, dict) and payment.get("roundingDifference") is not None
        ]
        if payment_roundings and all(value is not None for value in payment_roundings):
            payment_rounding = sum(
                (Decimal(value) for value in payment_roundings if value is not None), Decimal(0)
            )
            if rounding_amount is None:
                rounding_amount = str(payment_rounding)
                rounding_source = "payments.roundingDifference"
            elif Decimal(rounding_amount) != payment_rounding:
                issues.append("rounding_sources_disagree")
    deposit_sum = sum(
        (Decimal(item["deposit_amount"]) for item in items if item["deposit_amount"] is not None),
        Decimal(0),
    )
    reconciliation: dict[str, Any] = {
        "original_sum": None,
        "absolute_discount_sum": None,
        "matching_hypotheses": [],
        "validated_net_semantics": False,
        "deposit_sum": str(deposit_sum),
    }
    if items and all(item["original_amount"] is not None for item in items):
        original = sum((Decimal(item["original_amount"]) for item in items), Decimal(0))
        reconciliation["original_sum"] = str(original)
        all_discounts = [discount for item in items for discount in item["discounts"]]
        if (
            all(discount["amount"] is not None for discount in all_discounts)
            and "invalid_discounts" not in issues
            and "invalid_discount" not in issues
        ):
            discount_total = sum(
                (abs(Decimal(discount["amount"])) for discount in all_discounts),
                Decimal(0),
            )
            reconciliation["absolute_discount_sum"] = str(discount_total)
            if total_amount is not None:
                total_decimal = Decimal(total_amount)
                base = original + deposit_sum
                if base == total_decimal:
                    reconciliation["matching_hypotheses"].append(
                        "original_plus_deposits" if deposit_sum else "original_equals_total"
                    )
                if discount_total and base - discount_total == total_decimal:
                    reconciliation["matching_hypotheses"].append(
                        "original_plus_deposits_minus_absolute_discounts"
                        if deposit_sum
                        else "original_minus_absolute_discounts"
                    )
                if rounding_amount is not None and Decimal(rounding_amount):
                    rounding_decimal = Decimal(rounding_amount)
                    if base + rounding_decimal == total_decimal:
                        reconciliation["matching_hypotheses"].append(
                            "original_plus_deposits_plus_rounding"
                            if deposit_sum
                            else "original_plus_rounding"
                        )
                    if discount_total and base - discount_total + rounding_decimal == total_decimal:
                        reconciliation["matching_hypotheses"].append(
                            "original_plus_deposits_minus_absolute_discounts_plus_rounding"
                            if deposit_sum
                            else "original_minus_absolute_discounts_plus_rounding"
                        )
                if not reconciliation["matching_hypotheses"]:
                    issues.append("totals_unreconciled")
    return {
        "id": detail.get("id"),
        "date": date,
        "currency": currency,
        "total": total_amount,
        "rounding": rounding_amount,
        "rounding_source": rounding_source,
        "total_discount": numeric(detail.get("totalDiscount"), "total_discount", required=False),
        "items": items,
        "issues": sorted(set(issues)),
        "price_semantics": "source_fields_only; net_item_price_not_inferred",
        "reconciliation": reconciliation,
        "source_format": source_format,
    }


def validate_receipt(detail: dict[str, Any], expected_id: str) -> dict[str, Any]:
    """Validate identity and arithmetic without inferring missing net prices."""
    if not expected_id or detail.get("id") != expected_id:
        raise LidlError("receipt_identity_mismatch")
    result = normalize(detail)
    issues = result.get("issues", [])
    for item in result.get("items", []):
        quantity = item.get("quantity")
        unit_price = item.get("unit_price")
        original = item.get("original_amount")
        if quantity is not None and unit_price is not None and original is not None:
            extended = (Decimal(quantity) * Decimal(unit_price)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if extended != Decimal(original):
                issues.append("quantity_unit_price_mismatch")
        printed_net = item.get("printed_net_amount")
        discounts = item.get("discounts", [])
        if (
            printed_net is not None
            and original is not None
            and all(discount.get("amount") is not None for discount in discounts)
        ):
            discount_sum = sum(
                (abs(Decimal(discount.get("amount"))) for discount in discounts), Decimal(0)
            )
            if Decimal(original) - discount_sum != Decimal(printed_net):
                issues.append("printed_net_amount_mismatch")
    result["issues"] = sorted(set(issues))
    return result
