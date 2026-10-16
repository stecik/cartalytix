"""Synthetic fixture reproducing observed attributes, no real purchases."""

from cartalytix.export import normalize


def test_html_item_attributes_and_discount_are_exported():
    detail = {
        "id": "a",
        "date": "2024-01-01",
        "currency": "CZK",
        "totalAmount": "17,00",
        "htmlPrintedReceipt": """
    <span id="purchase_list_line_1" class="article" data-art-id="123" data-art-description="Test" data-unit-price="10,00" data-art-quantity="2" data-art-amount="20,00">Test 20,00 A</span>
    <span id="purchase_list_line_2" class="article" data-art-id="123" data-art-description="Test" data-unit-price="10,00" data-art-quantity="2">2 * 10,00 20,00 A</span>
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


def test_html_identical_products_on_separate_lines_are_not_deduplicated():
    html = '<span class="article" data-art-description="Test" data-unit-price="1,00" data-art-quantity="1">Test 1,00 A</span>'
    result = normalize({"htmlPrintedReceipt": html + html})
    assert len(result["items"]) == 2


def test_html_missing_quantity_does_not_default_to_one():
    result = normalize(
        {
            "htmlPrintedReceipt": '<span class="article" data-art-description="Test" data-unit-price="1,00">Test</span>'
        }
    )
    assert result["items"][0]["quantity"] is None
    assert "missing_quantity" in result["issues"]


def test_czech_weighted_plain_lines_and_net_summary_do_not_create_phantom_items():
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


def test_single_unit_quantity_inferred_only_when_printed_amount_matches_unit_price():
    result = normalize(
        {
            "htmlPrintedReceipt": '<span class="article" data-art-description="Test" data-unit-price="1,00">Test 1,00 A</span>'
        }
    )
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["quantity_source"] == "printed_single_unit"


def test_czech_scale_prefixes_thousands_and_tare_are_not_lost():
    html = """<span id="purchase_list_line_1">Sample item   246,51 B</span>
    <span id="purchase_list_line_2">M 0,099 kg x 2 490, Kč/kg</span>
    <span id="purchase_list_line_3">PT: 0,002 kg</span>"""
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["unit_price"] == "2490.00"
    assert result["items"][0]["quantity"] == "0.099"
    assert result["items"][0]["tare_quantity"] == "0.002"
    assert "html_unparsed_line" not in result["issues"]


def test_html_thousands_in_line_amount_do_not_become_part_of_product_name():
    html = '<span class="article" data-art-description="Test" data-unit-price="1569,90">Test 1 569,90 A</span>'
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["original_amount"] == "1569.90"


def test_warranty_metadata_does_not_change_quantity():
    html = """<span class="article" data-art-description="Test" data-unit-price="10,00">Test 10,00 A</span>
    <span id="purchase_list_line_2">Záruka: 3 roky</span>"""
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["quantity"] == "1"
    assert result["items"][0]["warranty"] == "3 roky"
    assert "html_unparsed_line" not in result["issues"]


def test_number_at_end_of_name_is_not_a_thousands_group_in_price():
    html = '<span class="article" data-art-description="Sample 30" data-unit-price="129,90">Sample 30         129,90 A</span>'
    result = normalize({"htmlPrintedReceipt": html})
    assert result["items"][0]["original_amount"] == "129.90"
    assert result["items"][0]["quantity"] == "1"


def test_explicit_cash_rounding_is_needed_for_receipt_reconciliation():
    html = """<span class="article" data-art-description="Test" data-unit-price="19,90">Test 19,90 A</span>
    <span id="purchase_summary_line_2">zaokrouhleno       0,10</span>"""
    result = normalize({"htmlPrintedReceipt": html, "totalAmount": "20,00"})
    assert result["rounding"] == "0.10"
    assert "totals_unreconciled" not in result["issues"]
    assert result["reconciliation"]["matching_hypotheses"] == ["original_plus_rounding"]
