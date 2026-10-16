import json

from test_export import API

from cartalytix.audit import audit
from cartalytix.export import collect


def test_offline_audit_recomputes_quality_from_raw_data(tmp_path):
    detail = {
        "id": "a",
        "date": "2024-01-01",
        "currency": "CZK",
        "totalAmount": "2,00",
        "itemsLine": [
            {
                "name": "Sample",
                "quantity": "1",
                "currentUnitPrice": "2,00",
                "originalAmount": "2,00",
            }
        ],
    }
    collect(API([{"tickets": [{"id": "a"}], "totalCount": 1}], {"a": detail}), tmp_path)
    result = audit(tmp_path)
    assert result["quality_passed"] is True
    assert result["items"] == 1
    assert result["reconciled_receipts"] == 1
    assert json.loads((tmp_path / "quality.json").read_text())["quality_passed"] is True


def test_offline_audit_never_calls_missing_details_complete(tmp_path):
    collect(API([{"tickets": [{"id": "a"}], "totalCount": 1}]), tmp_path)
    next((tmp_path / "raw/details").glob("*.json")).unlink()
    result = audit(tmp_path)
    assert result["quality_passed"] is False
    assert result["missing_or_invalid_details"] == 1


def test_balanced_receipt_cannot_hide_wrong_quantity_or_unit_price(tmp_path):
    detail = {
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
    }
    collect(API([{"tickets": [{"id": "a"}], "totalCount": 1}], {"a": detail}), tmp_path)
    result = audit(tmp_path)
    assert result["quality_passed"] is False
    assert result["issues"]["quantity_unit_price_mismatch"] == 1
