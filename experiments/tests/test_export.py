"""Synthetic fixtures only; never put personal receipts in this directory."""

import json

import pytest

from cartalytix.export import ExportError, collect, normalize, number


class API:
    def __init__(self, pages, details=None):
        self.pages = pages
        self.details = details or {}

    def page(self, page):
        return self.pages[page - 1]

    def detail(self, receipt_id):
        return self.details.get(receipt_id, {"id": receipt_id, "itemsLine": []})


def test_short_page_does_not_truncate_history(tmp_path):
    api = API(
        [
            {"tickets": [{"id": "a"}], "totalCount": 2, "size": 25},
            {"tickets": [{"id": "b"}], "totalCount": 2, "size": 25},
        ]
    )
    result = collect(api, tmp_path)
    assert result["complete"] is True
    assert result["unique_receipts"] == 2
    assert len(result["pages"]) == 2
    assert (
        json.loads((tmp_path / "receipts.json").read_text())["receipts"][1]["id"] == "b"
    )


def test_repeated_page_is_not_reported_as_complete(tmp_path):
    page = {"tickets": [{"id": "a"}], "totalCount": 3, "size": 1}
    with pytest.raises(ExportError):
        collect(API([page, page]), tmp_path)
    assert json.loads((tmp_path / "report.json").read_text())["complete"] is False


def test_empty_page_before_expected_total_is_incomplete(tmp_path):
    with pytest.raises(ExportError):
        collect(API([{"tickets": [], "totalCount": 2, "size": 25}]), tmp_path)


def test_incremental_reuses_details_but_scans_history_for_late_receipts(tmp_path):
    collect(API([{"tickets": [{"id": "old"}], "totalCount": 1}]), tmp_path)
    result = collect(
        API(
            [
                {"tickets": [{"id": "old"}], "totalCount": 2},
                {"tickets": [{"id": "late"}], "totalCount": 2},
            ]
        ),
        tmp_path,
    )
    assert result["cached_details"] == 1
    assert result["downloaded_details"] == 1
    assert result["unique_receipts"] == 2


def test_failed_detail_is_retried_on_next_run(tmp_path):
    class Broken(API):
        def detail(self, receipt_id):
            raise ExportError("HTTP 503")

    pages = [{"tickets": [{"id": "a"}], "totalCount": 1}]
    with pytest.raises(ExportError):
        collect(Broken(pages), tmp_path)
    assert collect(API(pages), tmp_path)["downloaded_details"] == 1


@pytest.mark.parametrize(
    "value,want",
    [("1\u00a0234,50", "1234.50"), ("-0,90", "-0.90"), ("0", "0"), (None, None)],
)
def test_decimal_values_preserve_precision_and_missingness(value, want):
    assert number(value) == want


def test_items_discounts_and_money_are_preserved():
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


def test_html_only_and_bad_money_are_quality_issues():
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


def test_detail_identity_mismatch_is_never_cached(tmp_path):
    with pytest.raises(ExportError):
        collect(
            API([{"tickets": [{"id": "a"}], "totalCount": 1}], {"a": {"id": "b"}}),
            tmp_path,
        )


def test_max_page_limit_keeps_previous_complete_export(tmp_path):
    collect(API([{"tickets": [{"id": "a"}], "totalCount": 1}]), tmp_path)
    previous = (tmp_path / "receipts.json").read_bytes()
    with pytest.raises(ExportError):
        collect(
            API([{"tickets": [{"id": "b"}], "totalCount": 2}]), tmp_path, max_pages=1
        )
    assert (tmp_path / "receipts.json").read_bytes() == previous
    assert json.loads((tmp_path / "report.json").read_text())["complete"] is False


def test_missing_count_continues_until_empty_page(tmp_path):
    result = collect(API([{"tickets": [{"id": "a"}]}, {"tickets": []}]), tmp_path)
    assert result["complete"] is True
    assert len(result["pages"]) == 2


def test_corrupt_cache_is_refetched(tmp_path):
    pages = [{"tickets": [{"id": "a"}], "totalCount": 1}]
    collect(API(pages), tmp_path)
    next((tmp_path / "raw/details").glob("*.json")).write_text(
        "broken", encoding="utf-8"
    )
    assert collect(API(pages), tmp_path)["downloaded_details"] == 1


def test_refresh_existing_details_picks_up_corrections(tmp_path):
    pages = [{"tickets": [{"id": "a"}], "totalCount": 1}]
    collect(API(pages, {"a": {"id": "a", "totalAmount": "10"}}), tmp_path)
    collect(
        API(pages, {"a": {"id": "a", "totalAmount": "8"}}),
        tmp_path,
        refresh_details=True,
    )
    receipt = json.loads((tmp_path / "receipts.json").read_text())["receipts"][0]
    assert receipt["total"] == "8"


def test_systemic_detail_failures_stop_early_with_useful_sanitized_error(tmp_path):
    class Broken(API):
        def detail(self, receipt_id):
            raise ExportError("Receipt API HTTP 400; no response body logged")

    pages = [{"tickets": [{"id": str(i)} for i in range(10)], "totalCount": 10}]
    with pytest.raises(ExportError):
        collect(Broken(pages), tmp_path)
    report = json.loads((tmp_path / "report.json").read_text())
    assert sum("detail" in error for error in report["errors"]) == 3
    assert "HTTP 400" in report["errors"][0]["error"]


def test_detail_without_identity_is_rejected(tmp_path):
    with pytest.raises(ExportError):
        collect(
            API(
                [{"tickets": [{"id": "a"}], "totalCount": 1}], {"a": {"itemsLine": []}}
            ),
            tmp_path,
        )
    assert not list((tmp_path / "raw/details").glob("*.json"))
