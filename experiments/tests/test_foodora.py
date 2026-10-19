"""Synthetic foodora order_history payloads reproducing observed fields, no real orders."""

from pathlib import Path
from typing import Any

from foodora.export import PAGE_SIZE, collect, normalize


def order(code: str, *, voucher: int = 0, canceled: bool = False) -> dict[str, Any]:
    fees = [
        {"translation_key": "NEXTGEN_CART_SUBTOTAL", "value": 315},
        {"translation_key": "NEXTGEN_DELIVERY_FEE", "value": 0},
        {"translation_key": "NEXTGEN_CART_DISCOUNT", "value": -63},
        {"translation_key": "NEXTGEN_CART_CONTAINER_CHARGES", "value": 20},
        {"translation_key": "NEXTGEN_CART_SERVICE_FEE", "value": 13},
    ]
    if voucher:
        fees.append({"translation_key": "NEXTGEN_COUT_VOUCHER", "value": -voucher})
    total = 285 - voucher
    fees.append({"translation_key": "NEXTGEN_TOTAL_VAT", "value": total})
    return {
        "order_code": code,
        "ordered_at": {"date": "2024-01-12 11:31:27", "timezone": "Europe/Prague"},
        "vendor": {"name": "Synthetic bistro"},
        "subtotal": 315,
        "total_value": total,
        "rider_tip": 0,
        "dynamic_fees": fees,
        "status_flags": {"is_canceled": canceled, "is_delivered": not canceled},
        "order_products": [
            {
                "name": "Polévka",
                "quantity": 1,
                "total_price": 80,
                "variation_id": "1",
                "toppings": [],
            },
            {
                "name": "Burger",
                "quantity": 2,
                "total_price": 235,
                "variation_id": "2",
                "toppings": [{"id": "9", "quantity": 1}],
            },
        ],
    }


def test_normalize_reconciles_fees_and_discounts():
    result = normalize(order("a1", voucher=50))
    assert result["issues"] == []
    assert (result["date"], result["timezone"]) == (
        "2024-01-12T11:31:27",
        "Europe/Prague",
    )
    assert (result["merchant"], result["status"], result["total"]) == (
        "Synthetic bistro",
        "completed",
        "235",
    )
    assert result["items"][1]["unit_price"] == "117.5"
    assert result["items"][1]["toppings"] == 1
    assert result["fees"] == [
        {"name": "cart_container_charges", "amount": "20"},
        {"name": "cart_service_fee", "amount": "13"},
    ]
    assert result["discounts"] == [
        {"name": "cart_discount", "amount": "63"},
        {"name": "cout_voucher", "amount": "50"},
    ]


def test_normalize_flags_mismatches_and_tips():
    broken = order("a2")
    broken["total_value"] = 300
    assert "totals_unreconciled" in normalize(broken)["issues"]
    legacy = order("a5")
    legacy["total_value"] = 135
    result = normalize(legacy)
    assert result["issues"] == []
    assert result["discounts"][-1] == {"name": "discounts_and_credits", "amount": "150"}
    tipped = order("a3")
    tipped["rider_tip"] = 20
    tipped["total_value"] = 305
    assert normalize(tipped)["fees"][-1] == {"name": "rider_tip", "amount": "20"}
    assert normalize(tipped)["issues"] == []
    assert normalize(order("a4", canceled=True))["status"] == "canceled"


def test_collect_pages_until_short_page(tmp_path: Path):
    orders = [order(f"o{i}") for i in range(PAGE_SIZE + 3)]
    calls: list[int] = []

    def fetch_page(offset: int, limit: int) -> dict[str, Any]:
        calls.append(offset)
        return {
            "data": {
                "items": orders[offset : offset + limit],
                "total_count": str(len(orders)),
            }
        }

    report = collect(fetch_page, tmp_path)
    assert calls == [0, PAGE_SIZE]
    assert (report["complete"], report["unique_orders"], report["quality_issues"]) == (
        True,
        23,
        0,
    )
    assert (tmp_path / "orders.json").exists()


def test_collect_reports_overlapping_pages(tmp_path: Path):
    def fetch_page(offset: int, limit: int) -> dict[str, Any]:
        # total_count is a lookahead and must not end paging early or late.
        items = [order("same")] * (PAGE_SIZE if offset == 0 else 1)
        return {"data": {"items": items, "total_count": "999"}}

    report = collect(fetch_page, tmp_path)
    assert report["errors"][0] == "duplicate_or_invalid_order"
    assert (tmp_path / "orders.partial.json").exists()
