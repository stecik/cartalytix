"""Reproducible offline audit of a captured index and its original details."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .export import normalize, write_json


def audit(destination):
    destination = Path(destination)
    index = json.loads((destination / "index.json").read_text(encoding="utf-8"))
    rows = index["tickets"]
    issues = Counter()
    formats = Counter()
    dates = []
    counts = {
        "listed_receipts": len(rows),
        "checked_receipts": 0,
        "missing_or_invalid_details": 0,
        "items": 0,
        "discount_lines": 0,
        "weighted_items": 0,
        "reconciled_receipts": 0,
        "price_extensions_checked": 0,
        "price_extensions_matching": 0,
        "printed_net_amounts_checked": 0,
        "printed_net_amounts_matching": 0,
    }
    seen = set()
    for summary in rows:
        receipt_id = summary["id"]
        if receipt_id in seen:
            issues["duplicate_index_id"] += 1
            continue
        seen.add(receipt_id)
        filename = hashlib.sha256(receipt_id.encode()).hexdigest() + ".json"
        try:
            detail = json.loads(
                (destination / "raw/details" / filename).read_text(encoding="utf-8")
            )
        except (ValueError, OSError):
            counts["missing_or_invalid_details"] += 1
            continue
        if not isinstance(detail, dict) or detail.get("id") != receipt_id:
            counts["missing_or_invalid_details"] += 1
            continue
        result = normalize({**summary, **detail, "id": receipt_id})
        counts["checked_receipts"] += 1
        counts["items"] += len(result["items"])
        counts["discount_lines"] += sum(
            len(item["discounts"]) for item in result["items"]
        )
        counts["weighted_items"] += sum(
            item["is_weight"] is True for item in result["items"]
        )
        counts["reconciled_receipts"] += bool(
            result["reconciliation"]["matching_hypotheses"]
        )
        issues.update(result["issues"])
        for item in result["items"]:
            if all(
                item[k] is not None
                for k in ("quantity", "unit_price", "original_amount")
            ):
                counts["price_extensions_checked"] += 1
                extended = (
                    Decimal(item["quantity"]) * Decimal(item["unit_price"])
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if extended == Decimal(item["original_amount"]):
                    counts["price_extensions_matching"] += 1
                else:
                    issues["quantity_unit_price_mismatch"] += 1
            if (
                item["printed_net_amount"] is not None
                and item["original_amount"] is not None
                and all(
                    discount["amount"] is not None for discount in item["discounts"]
                )
            ):
                counts["printed_net_amounts_checked"] += 1
                discount_sum = sum(
                    (
                        abs(Decimal(discount["amount"]))
                        for discount in item["discounts"]
                    ),
                    Decimal(0),
                )
                if Decimal(item["original_amount"]) - discount_sum == Decimal(
                    item["printed_net_amount"]
                ):
                    counts["printed_net_amounts_matching"] += 1
                else:
                    issues["printed_net_amount_mismatch"] += 1
        formats[result["source_format"]] += 1
        if result.get("date") and "invalid_date" not in result["issues"]:
            dates.append(result["date"])
    result = {
        **counts,
        "checked_at": datetime.now(UTC).isoformat(),
        "index_run": index.get("run"),
        "formats": dict(formats),
        "issues": dict(issues),
        "oldest_receipt": min(dates) if dates else None,
        "newest_receipt": max(dates) if dates else None,
        "quality_passed": not issues
        and counts["missing_or_invalid_details"] == 0
        and counts["checked_receipts"] == len(rows)
        and counts["reconciled_receipts"] == len(rows),
        "scope": "Arithmetic/schema consistency of API-visible data; not proof of all purchases or fiscal correctness",
    }
    write_json(destination / "quality.json", result)
    return result
