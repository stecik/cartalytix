from cartalytix.export import normalize


def test_multipack_product_title_is_not_a_quantity_continuation():
    html = '<span class="article" data-art-description="6 x 1,5 l voda" data-unit-price="9,00">6 x 1,5 l voda    9,00 A</span>'
    result = normalize({"htmlPrintedReceipt": html})
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "6 x 1,5 l voda"
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["unit_price"] == "9.00"


def test_structured_items_still_use_explicit_html_rounding():
    result = normalize(
        {
            "totalAmount": "20,00",
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "1",
                    "currentUnitPrice": "19,90",
                    "originalAmount": "19,90",
                }
            ],
            "htmlPrintedReceipt": "<span>zaokrouhleno 0,10</span>",
        }
    )
    assert result["rounding"] == "0.10"
    assert "totals_unreconciled" not in result["issues"]


def test_structured_deposit_and_payment_rounding_are_counted_once():
    result = normalize(
        {
            "id": "a",
            "date": "2021-01-01",
            "currency": "CZK",
            "totalAmount": "23,00",
            "payments": [{"roundingDifference": "0,10"}],
            "tenderChange": [{"roundingDifference": "0"}],
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "1",
                    "currentUnitPrice": "19,90",
                    "originalAmount": "19,90",
                    "deposit": {"quantity": 1, "amount": "3,00", "unitPrice": "3,00"},
                }
            ],
        }
    )
    assert result["rounding"] == "0.10"
    assert result["reconciliation"]["deposit_sum"] == "3.00"
    assert "totals_unreconciled" not in result["issues"]
