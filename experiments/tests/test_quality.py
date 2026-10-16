from cartalytix.export import normalize


def test_reconciliation_checks_decimal_candidates_without_assuming_discount_sign():
    result = normalize(
        {
            "id": "a",
            "totalAmount": "17,00",
            "currency": "CZK",
            "date": "2024-01-01",
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "2",
                    "currentUnitPrice": "10,00",
                    "originalAmount": "20,00",
                    "discounts": [{"amount": "-3,00"}],
                }
            ],
        }
    )
    assert result["reconciliation"]["original_sum"] == "20.00"
    assert result["reconciliation"]["absolute_discount_sum"] == "3.00"
    assert result["reconciliation"]["matching_hypotheses"] == [
        "original_minus_absolute_discounts"
    ]
    assert result["reconciliation"]["validated_net_semantics"] is False


def test_missing_item_price_never_looks_like_a_balanced_zero_receipt():
    result = normalize(
        {"totalAmount": "0", "itemsLine": [{"name": "unknown", "quantity": "1"}]}
    )
    assert result["reconciliation"]["matching_hypotheses"] == []


def test_optional_receipt_discount_is_not_a_quality_error():
    result = normalize(
        {
            "totalAmount": "2,00",
            "currency": "CZK",
            "date": "2024-01-01",
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "1",
                    "currentUnitPrice": "2,00",
                    "originalAmount": "2,00",
                }
            ],
        }
    )
    assert result["issues"] == []
