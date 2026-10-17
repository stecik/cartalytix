"""Synthetic Lidl parser regressions; no personal receipt data."""

from typing import Any

import pytest

from cartalytix.providers.lidl.normalization import normalize, number


def test_html_item_attributes_and_discount_are_exported() -> None:
    detail = {
        "id": "a",
        "date": "2024-01-01",
        "currency": "CZK",
        "totalAmount": "17,00",
        "htmlPrintedReceipt": """
    <span id="purchase_list_line_1" class="article" data-art-id="123"
        data-art-description="Test" data-unit-price="10,00" data-art-quantity="2"
        data-art-amount="20,00">Test 20,00 A</span>
    <span id="purchase_list_line_2" class="article" data-art-id="123"
        data-art-description="Test" data-unit-price="10,00"
        data-art-quantity="2">2 * 10,00 20,00 A</span>
    <span id="purchase_list_line_3" class="discount">Lidl Plus kupon -3,00</span>
    """,
    }
    result = normalize(detail)
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "Test"
    assert result["items"][0]["quantity"] == "2"
    assert result["items"][0]["discounts"][0]["amount"] == "-3.00"
    assert result["items"][0]["original_amount"] == "20.00"
    assert "items_unavailable" not in result["issues"]


def test_html_identical_products_on_separate_lines_are_not_deduplicated() -> None:
    html = (
        '<span class="article" data-art-description="Test" data-unit-price="1,00"'
        ' data-art-quantity="1">Test 1,00 A</span>'
    )
    result = normalize({"htmlPrintedReceipt": html + html})
    assert len(result["items"]) == 2


def test_html_missing_quantity_does_not_default_to_one() -> None:
    result = normalize(
        {
            "htmlPrintedReceipt": '<span class="article" data-art-description="Test"'
            ' data-unit-price="1,00">Test</span>'
        }
    )
    assert result["items"][0]["quantity"] is None
    assert "missing_quantity" in result["issues"]


def test_czech_weighted_plain_lines_and_net_summary_do_not_create_phantom_items() -> None:
    html = """<span id="purchase_list_line_1">Sample weighted product       139,45 B</span>
    <span id="purchase_list_line_2">E   0,254 kg   x 549,00 Kč/kg</span>
    <span id="purchase_list_line_3" class="discount">Sleva 60%      -83,68</span>
    <span id="purchase_list_line_4">Cena po slevě       55,77</span>"""
    result = normalize({"htmlPrintedReceipt": html, "totalAmount": "55,77"})
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["quantity"] == "0.254"
    assert item["unit_price"] == "549.00"
    assert item["original_amount"] == "139.45"
    assert item["is_weight"] is True
    assert item["printed_net_amount"] == "55.77"
    assert "totals_unreconciled" not in result["issues"]


def test_single_unit_quantity_inferred_only_when_printed_amount_matches_unit_price() -> None:
    result = normalize(
        {
            "htmlPrintedReceipt": '<span class="article" data-art-description="Test"'
            ' data-unit-price="1,00">Test 1,00 A</span>'
        }
    )
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["quantity_source"] == "printed_single_unit"


def test_czech_scale_prefixes_thousands_and_tare_are_not_lost() -> None:
    html = """<span id="purchase_list_line_1">Sample item   246,51 B</span>
    <span id="purchase_list_line_2">M 0,099 kg x 2 490, Kč/kg</span>
    <span id="purchase_list_line_3">PT: 0,002 kg</span>"""
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["unit_price"] == "2490.00"
    assert result["items"][0]["quantity"] == "0.099"
    assert result["items"][0]["tare_quantity"] == "0.002"
    assert "html_unparsed_line" not in result["issues"]


def test_html_thousands_in_line_amount_do_not_become_part_of_product_name() -> None:
    html = (
        '<span class="article" data-art-description="Test"'
        ' data-unit-price="1569,90">Test 1 569,90 A</span>'
    )
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["original_amount"] == "1569.90"


def test_warranty_metadata_does_not_change_quantity() -> None:
    html = """<span class="article" data-art-description="Test"
        data-unit-price="10,00">Test 10,00 A</span>
    <span id="purchase_list_line_2">Záruka: 3 roky</span>"""
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["warranty"] == "3 roky"
    assert "html_unparsed_line" not in result["issues"]


def test_number_at_end_of_name_is_not_a_thousands_group_in_price() -> None:
    html = (
        '<span class="article" data-art-description="Sample 30"'
        ' data-unit-price="129,90">Sample 30         129,90 A</span>'
    )
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["original_amount"] == "129.90"
    assert result["items"][0]["quantity"] == "1"


def test_explicit_cash_rounding_is_needed_for_receipt_reconciliation() -> None:
    html = """<span class="article" data-art-description="Test"
        data-unit-price="19,90">Test 19,90 A</span>
    <span id="purchase_summary_line_2">zaokrouhleno       0,10</span>"""
    result = normalize({"htmlPrintedReceipt": html, "totalAmount": "20,00"})
    assert result["rounding"] == "0.10"
    assert "totals_unreconciled" not in result["issues"]
    assert result["reconciliation"]["matching_hypotheses"] == ["original_plus_rounding"]


def test_reconciliation_checks_decimal_candidates_without_assuming_discount_sign() -> None:
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
    assert result["reconciliation"]["matching_hypotheses"] == ["original_minus_absolute_discounts"]
    assert result["reconciliation"]["validated_net_semantics"] is False


def test_missing_item_price_never_looks_like_a_balanced_zero_receipt() -> None:
    result = normalize({"totalAmount": "0", "itemsLine": [{"name": "unknown", "quantity": "1"}]})
    assert result["reconciliation"]["matching_hypotheses"] == []


def test_optional_receipt_discount_is_not_a_quality_error() -> None:
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


def test_multipack_product_title_is_not_a_quantity_continuation() -> None:
    html = (
        '<span class="article" data-art-description="6 x 1,5 l voda"'
        ' data-unit-price="9,00">6 x 1,5 l voda    9,00 A</span>'
    )
    result = normalize({"htmlPrintedReceipt": html})
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "6 x 1,5 l voda"
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["unit_price"] == "9.00"


def test_structured_items_still_use_explicit_html_rounding() -> None:
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


def test_structured_deposit_and_payment_rounding_are_counted_once() -> None:
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


@pytest.mark.parametrize(
    "value,want",
    [("1\u00a0234,50", "1234.50"), ("-0,90", "-0.90"), ("0", "0"), (None, None)],
)
def test_decimal_values_preserve_precision_and_missingness(value: object, want: str | None) -> None:
    assert number(value) == want


def test_items_discounts_and_money_are_preserved() -> None:
    result = normalize(
        {
            "id": "a",
            "date": "2024-01-01T10:00:00",
            "totalAmount": "17,00",
            "currency": {"code": "CZK"},
            "itemsLine": [
                {
                    "name": "Test",
                    "quantity": "2",
                    "currentUnitPrice": "10,00",
                    "originalAmount": "20,00",
                    "discounts": [{"description": "Kupon", "amount": "3,00"}],
                }
            ],
        }
    )
    assert result["items"][0]["discounts"][0]["amount"] == "3.00"
    assert result["items"][0]["original_amount"] == "20.00"
    assert result["total"] == "17.00"
    assert result["currency"] == "CZK"


def test_html_only_and_bad_money_are_quality_issues() -> None:
    result = normalize(
        {
            "id": "a",
            "totalAmount": "unknown",
            "htmlPrintedReceipt": "<html>receipt</html>",
        }
    )
    assert "items_unavailable" in result["issues"]
    assert "invalid_total" in result["issues"]
    assert result["total"] is None


def test_validation_rejects_identity_mismatch() -> None:
    from cartalytix.providers.lidl.client import LidlError
    from cartalytix.providers.lidl.normalization import validate_receipt

    with pytest.raises(LidlError, match="receipt_identity_mismatch"):
        validate_receipt({"id": "other"}, "expected")


def test_validation_detects_wrong_quantity_even_with_balanced_total() -> None:
    from cartalytix.providers.lidl.normalization import validate_receipt

    result = validate_receipt(
        {
            "id": "a",
            "date": "2024-01-01",
            "currency": "CZK",
            "totalAmount": "2,00",
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "2",
                    "currentUnitPrice": "2,00",
                    "originalAmount": "2,00",
                }
            ],
        },
        "a",
    )
    assert result.get("issues") == ["quantity_unit_price_mismatch"]


def test_validation_detects_wrong_printed_net_and_preserves_source_fields() -> None:
    from cartalytix.providers.lidl.normalization import validate_receipt

    detail: dict[str, Any] = {
        "id": "a",
        "date": "2024-01-01",
        "currency": "CZK",
        "totalAmount": "17,00",
        "itemsLine": [
            {
                "name": "Sample",
                "quantity": "2",
                "currentUnitPrice": "10,00",
                "originalAmount": "20,00",
                "printedNetAmount": "18,00",
                "discounts": [{"amount": "-3,00", "promotionId": "sample"}],
            }
        ],
    }
    result = validate_receipt(detail, "a")
    assert result.get("issues") == ["printed_net_amount_mismatch"]
    assert result.get("items", [])[0].get("discounts")[0].get("promotionId") == "sample"
    assert detail.get("itemsLine", [])[0].get("originalAmount") == "20,00"


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "1.234,50", "1e3"])
def test_invalid_money_never_becomes_zero(value: object) -> None:
    with pytest.raises(ValueError):
        number(value)


@pytest.mark.parametrize(
    "source_unit,expected", [("kg", "kg"), ("g", "g"), ("l", "l"), ("m", "m"), ("ks", "piece")]
)
def test_printed_quantity_preserves_exact_source_unit(source_unit: str, expected: str) -> None:
    result = normalize(
        {
            "htmlPrintedReceipt": '<span id="purchase_list_line_1">Sample 25,00 B</span>'
            f'<span id="purchase_list_line_2">250 {source_unit} x 0,10</span>'
        }
    )
    assert result.get("items", [])[0].get("unit") == expected


@pytest.mark.parametrize("weighted,expected", [(True, "kg"), (False, "piece"), (None, None)])
def test_structured_weight_flag_determines_only_known_units(
    weighted: bool | None,
    expected: str | None,
) -> None:
    result = normalize({"itemsLine": [{"name": "Sample", "isWeight": weighted}]})
    assert result.get("items", [])[0].get("unit") == expected


@pytest.mark.parametrize(
    "field,value,issue",
    [
        ("quantity", "0.1234567", "quantity_precision_exceeded"),
        ("quantity", "1000000000000", "quantity_precision_exceeded"),
        ("currentUnitPrice", "0.00000001", "unit_price_precision_exceeded"),
        ("currentUnitPrice", "1000000000000", "unit_price_precision_exceeded"),
        ("originalAmount", "0.123", "original_amount_precision_exceeded"),
        ("originalAmount", "10000000000000000", "original_amount_precision_exceeded"),
        ("printedNetAmount", "0.123", "printed_net_amount_precision_exceeded"),
    ],
)
def test_unrepresentable_item_number_is_preserved_but_rejected(
    field: str,
    value: str,
    issue: str,
) -> None:
    line = {
        "name": "Sample",
        "quantity": "1",
        "currentUnitPrice": "1",
        "originalAmount": "1",
        field: value,
    }
    result = normalize({"itemsLine": [line]})
    assert issue in result.get("issues", [])


@pytest.mark.parametrize("field", ["totalAmount", "totalDiscount"])
def test_unrepresentable_receipt_money_is_rejected(field: str) -> None:
    result = normalize({field: "0.001"})
    issue = (
        "total_precision_exceeded"
        if field == "totalAmount"
        else "total_discount_precision_exceeded"
    )
    assert issue in result.get("issues", [])


def test_unrepresentable_discount_deposit_and_rounding_are_rejected() -> None:
    result = normalize(
        {
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "1",
                    "currentUnitPrice": "1",
                    "originalAmount": "1",
                    "discounts": [{"amount": "0.001"}],
                    "deposit": {"amount": "0.001"},
                }
            ],
            "payments": [{"roundingDifference": "0.001"}],
        }
    )
    issues = result.get("issues", [])
    assert "discount_amount_precision_exceeded" in issues
    assert "deposit_amount_precision_exceeded" in issues
    assert "payment_rounding_precision_exceeded" in issues


def test_excess_price_precision_cannot_pass_arithmetic_validation() -> None:
    from cartalytix.providers.lidl.normalization import validate_receipt

    result = validate_receipt(
        {
            "id": "a",
            "date": "2024-01-01",
            "currency": "CZK",
            "totalAmount": "0.10",
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": "10000000",
                    "currentUnitPrice": "0.00000001",
                    "originalAmount": "0.10",
                }
            ],
        },
        "a",
    )
    assert result.get("issues") == ["unit_price_precision_exceeded"]
    assert result.get("items", [])[0].get("unit_price") == "0.00000001"


@pytest.mark.parametrize(
    "quantity,price,amount",
    [
        ("999999999999.999999", "0", "0"),
        ("1", "999999999999.999999", "999999999999.99"),
        ("1.0000000", "1.0000000", "1.000"),
        ("-999999999999.999999", "0", "-9999999999999999.99"),
    ],
)
def test_exact_db_numeric_boundaries_and_trailing_zero_are_representable(
    quantity: str,
    price: str,
    amount: str,
) -> None:
    result = normalize(
        {
            "itemsLine": [
                {
                    "name": "Sample",
                    "quantity": quantity,
                    "currentUnitPrice": price,
                    "originalAmount": amount,
                }
            ]
        }
    )
    assert not any(issue.endswith("_precision_exceeded") for issue in result.get("issues", []))
