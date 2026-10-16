"""Lossless raw capture plus deliberately conservative financial normalization."""

import hashlib
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .receipt_html import parse_html


class ExportError(RuntimeError):
    pass


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def number(value):
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


def normalize(detail):
    issues = []

    def numeric(value, field, *, required=True):
        try:
            result = number(value)
            if result is None and required:
                issues.append("missing_" + field)
            return result
        except ValueError:
            issues.append("invalid_" + field)
            return None

    items = []
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
                "original_amount": numeric(
                    line.get("originalAmount"), "original_amount"
                ),
                "is_weight": line.get("isWeight"),
                "discounts": parsed_discounts,
                "deposit": line.get("deposit"),
                "deposit_amount": deposit_amount,
                "warranty": line.get("warrantyText"),
                "tare_quantity": numeric(
                    line.get("tareQuantity"), "tare_quantity", required=False
                ),
                "quantity_source": line.get("quantitySource", "itemsLine"),
                "code_kind": "internal_article_id"
                if line.get("parsedFromHtml")
                else "source_code",
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
        datetime.fromisoformat(date)
    except (TypeError, ValueError):
        issues.append("invalid_date")
    total_amount = numeric(detail.get("totalAmount"), "total")
    rounding_amount = numeric(html_rounding, "rounding", required=False)
    rounding_source = "htmlPrintedReceipt" if rounding_amount is not None else None
    payments = detail.get("payments")
    if isinstance(payments, list):
        payment_roundings = [
            numeric(payment["roundingDifference"], "payment_rounding")
            for payment in payments
            if isinstance(payment, dict)
            and payment.get("roundingDifference") is not None
        ]
        if payment_roundings and all(value is not None for value in payment_roundings):
            payment_rounding = sum(
                (Decimal(value) for value in payment_roundings), Decimal(0)
            )
            if rounding_amount is None:
                rounding_amount = str(payment_rounding)
                rounding_source = "payments.roundingDifference"
            elif Decimal(rounding_amount) != payment_rounding:
                issues.append("rounding_sources_disagree")
    deposit_sum = sum(
        (
            Decimal(item["deposit_amount"])
            for item in items
            if item["deposit_amount"] is not None
        ),
        Decimal(0),
    )
    reconciliation = {
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
                        "original_plus_deposits"
                        if deposit_sum
                        else "original_equals_total"
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
                    if (
                        discount_total
                        and base - discount_total + rounding_decimal == total_decimal
                    ):
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
        "total_discount": numeric(
            detail.get("totalDiscount"), "total_discount", required=False
        ),
        "items": items,
        "issues": sorted(set(issues)),
        "price_semantics": "source_fields_only; net_item_price_not_inferred",
        "reconciliation": reconciliation,
        "source_format": source_format,
    }


def collect(api, destination, *, max_pages=10000, refresh_details=False, progress=None):
    """Scan the entire list; reuse successful details by ID, never stop at a known ID."""
    destination = Path(destination)
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    report = {
        "run": run,
        "complete": False,
        "list_complete": False,
        "pages": [],
        "unique_receipts": 0,
        "downloaded_details": 0,
        "cached_details": 0,
        "errors": [],
        "quality_issues": {},
        "country": "CZ",
    }
    summaries = {}
    receipts = []
    total = None
    first_signature = None
    duplicates = 0
    try:
        for page_number in range(1, max_pages + 1):
            data = api.page(page_number)
            write_json(
                destination / "raw" / "pages" / run / f"{page_number:05}.json", data
            )
            if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
                raise ExportError("Unexpected receipt list schema")
            page_total = data.get("totalCount")
            if page_total is not None:
                if type(page_total) is not int or page_total < 0:
                    raise ExportError("Invalid totalCount")
                if total is not None and total != page_total:
                    raise ExportError("History changed during pagination; rerun")
                total = page_total
            tickets = data["tickets"]
            report["pages"].append(
                {
                    "page": page_number,
                    "count": len(tickets),
                    "size": data.get("size"),
                    "totalCount": page_total,
                }
            )
            ids = []
            before = len(summaries)
            for ticket in tickets:
                if (
                    not isinstance(ticket, dict)
                    or not isinstance(ticket.get("id"), str)
                    or not ticket["id"]
                ):
                    raise ExportError("Receipt summary has no valid ID")
                receipt_id = ticket["id"]
                ids.append(receipt_id)
                duplicates += receipt_id in summaries
                summaries[receipt_id] = ticket
            if first_signature is None:
                first_signature = (ids, page_total)
            report["unique_receipts"] = len(summaries)
            if progress:
                progress(
                    f"Page {page_number}: {len(tickets)} receipts; unique {len(summaries)}"
                )
            if total is not None and len(summaries) > total:
                raise ExportError("More unique receipts than totalCount")
            if not tickets or (total is not None and len(summaries) == total):
                if total is not None and len(summaries) != total:
                    raise ExportError("Empty page before totalCount was reached")
                break
            if len(summaries) == before:
                raise ExportError("Repeated page without new receipt IDs")
        else:
            raise ExportError("Page limit reached; history incomplete")
        if duplicates:
            raise ExportError("Overlapping pages; history may have changed, rerun")
        # Pagination is not a snapshot. Detect common concurrent-insert cases.
        check = api.page(1)
        if (
            [t["id"] for t in check["tickets"]],
            check.get("totalCount"),
        ) != first_signature:
            raise ExportError("First page changed during pagination; rerun")
        report["list_complete"] = True
        write_json(
            destination / "index.json",
            {"run": run, "tickets": list(summaries.values())},
        )
        consecutive_failures = 0
        for receipt_id, summary in summaries.items():
            filename = hashlib.sha256(receipt_id.encode()).hexdigest() + ".json"
            cache = destination / "raw" / "details" / filename
            try:
                detail = None
                if cache.exists() and not refresh_details:
                    try:
                        detail = json.loads(cache.read_text(encoding="utf-8"))
                    except (ValueError, OSError):
                        detail = None
                    if not isinstance(detail, dict) or detail.get("id") != receipt_id:
                        detail = None
                if detail is None:
                    detail = api.detail(receipt_id)
                    if not isinstance(detail, dict) or detail.get("id") != receipt_id:
                        raise ExportError("Detail identity/schema mismatch")
                    write_json(cache, detail)
                    report["downloaded_details"] += 1
                else:
                    report["cached_details"] += 1
                normalized = normalize({**summary, **detail, "id": receipt_id})
                receipts.append(normalized)
                consecutive_failures = 0
                if progress:
                    progress(f"Details: {len(receipts)}/{len(summaries)}")
            except ExportError as exc:
                # Receipt ID is private; report cache filename only.
                report["errors"].append({"detail": filename, "error": str(exc)})
                consecutive_failures += 1
                if progress:
                    progress("Detail failed: " + str(exc))
                if consecutive_failures >= 3:
                    raise ExportError(
                        "Three consecutive detail failures; stopped to avoid repeating systemic errors"
                    ) from None
        if report["errors"]:
            raise ExportError("Some receipt details failed; rerun to resume")
        report["complete"] = True
    except ExportError as exc:
        report["errors"].append({"error": str(exc)})
    finally:
        issue_counts = Counter(
            issue for receipt in receipts for issue in receipt["issues"]
        )
        report["quality_issues"] = dict(issue_counts)
        report["data_quality_passed"] = report["complete"] and not issue_counts
        dates = sorted(
            r["date"]
            for r in receipts
            if r.get("date") and "invalid_date" not in r["issues"]
        )
        report["oldest_receipt"] = dates[0] if dates else None
        report["newest_receipt"] = dates[-1] if dates else None
        report["duplicate_ids"] = duplicates
        report["history_scope"] = (
            "API-visible CZ history only; retention not guaranteed"
        )
        report["financial_reconciliation"] = (
            "exact_decimal_candidate_equalities_checked; not a guarantee of net item or fiscal semantics"
        )
        target = "receipts.json" if report["complete"] else "receipts.partial.json"
        write_json(
            destination / target,
            {"run": run, "complete": report["complete"], "receipts": receipts},
        )
        write_json(destination / "report.json", report)
    if not report["complete"]:
        raise ExportError("Export incomplete; inspect local report.json and rerun")
    return report
