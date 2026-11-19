"""Money, weighing and duplicate lines in Albert receipts; synthetic data only."""

from decimal import Decimal
from typing import Any

import pytest

from cartalytix.providers.albert.client import AlbertError
from cartalytix.providers.albert.normalization import validate_receipt


def receipt(**changes: object) -> dict[str, Any]:
    return {
        "receiptNumber": "receipt-1",
        "receiptCreationTime": "2026-01-02T10:00:00+01:00",
        "receiptSum": Decimal("26.34"),
        "purchaseStatus": "SUCCESS",
        "purchasedItems": [
            {
                "desc": "Milk",
                "quantity": 2,
                "pricePerItem": Decimal("12"),
                "totalPrice": Decimal("24"),
                "goldId": "123",
                "discount": {"description": "Card", "totalDiscount": Decimal("-4")},
            },
            {
                "desc": "Apples",
                "quantity": Decimal("0.1"),
                "measurement": "E",
                "pricePerItem": Decimal("63.4"),
                "totalPrice": Decimal("6.34"),
                "goldId": "456",
            },
        ],
        "discounts": [],
        **changes,
    }


def test_money_is_czk_and_line_discounts_are_not_subtracted_twice() -> None:
    normalized = validate_receipt({}, receipt(), "receipt-1")
    assert normalized is not None
    assert normalized.get("issues") == []
    assert normalized.get("currency") == "CZK"
    assert normalized.get("total") == "26.34"
    items = normalized.get("items", [])
    assert items[0].get("original_amount") == "24"
    assert items[0].get("discounts") == [{"name": "Card", "amount": "4"}]
    assert items[1].get("quantity") == "0.1"
    assert items[1].get("unit") == "kg" and items[1].get("is_weight") is True


def test_receipt_discount_is_applied_once() -> None:
    normalized = validate_receipt(
        {},
        receipt(
            receiptSum=Decimal("25.34"),
            discounts=[{"description": "Basket", "totalDiscount": Decimal("-1")}],
        ),
        "receipt-1",
    )
    assert normalized is not None
    assert normalized.get("issues") == []
    assert normalized.get("discounts") == [{"name": "Basket", "amount": "1"}]


def test_duplicate_products_remain_independent_receipt_lines() -> None:
    item = {
        "desc": "Milk",
        "quantity": 1,
        "pricePerItem": Decimal("10"),
        "totalPrice": Decimal("10"),
        "goldId": "123",
    }
    normalized = validate_receipt(
        {}, receipt(receiptSum=Decimal("20"), purchasedItems=[item, item]), "receipt-1"
    )
    assert normalized is not None
    assert len(normalized.get("items", [])) == 2
    assert normalized.get("issues") == []


def test_mismatched_identity_or_malformed_numbers_are_not_silently_imported() -> None:
    with pytest.raises(AlbertError, match="receipt_identity_mismatch"):
        validate_receipt({}, receipt(), "wrong")
    normalized = validate_receipt({}, receipt(receiptSum=Decimal("100")), "receipt-1")
    assert normalized is not None and "totals_unreconciled" in normalized.get("issues", [])
    malformed = receipt(
        purchasedItems=[
            {"desc": "Invalid", "quantity": True, "pricePerItem": "NaN", "totalPrice": "0"}
        ]
    )
    normalized = validate_receipt({}, malformed, "receipt-1")
    assert normalized is not None and normalized.get("issues")


def test_weighed_prices_keep_albert_ten_haler_rounding() -> None:
    item = {
        "desc": "Weighed",
        "quantity": Decimal("0.643"),
        "measurement": "E",
        "pricePerItem": Decimal("63"),
        "totalPrice": Decimal("40.5"),
    }
    normalized = validate_receipt(
        {}, receipt(receiptSum=Decimal("40.5"), purchasedItems=[item]), "receipt-1"
    )
    assert normalized.get("issues") == []
    assert normalized.get("items", [])[0].get("original_amount") == "40.5"


def test_archived_receipt_can_have_empty_legacy_status() -> None:
    normalized = validate_receipt({}, receipt(purchaseStatus=""), "receipt-1")
    assert normalized.get("issues") == []


@pytest.mark.parametrize("status", ["REVERSED", "SUCCESS_REVERSED"])
def test_unverified_reversal_is_not_silently_counted_as_spending(status: str) -> None:
    normalized = validate_receipt({}, receipt(purchaseStatus=status), "receipt-1")
    assert normalized.get("issues")
