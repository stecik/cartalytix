"""Synthetic receipt text and API responses only; never put personal receipts here."""

import json
from pathlib import Path

import httpx
import pytest

from cartalytix.export import ExportError
from globus.export import LIST_URL, Client, collect, merge
from globus.receipt_pdf import parse

RECEIPT = """Duplikát pokladní účtenky
(datum 02.03.2026, provoz 4001, pokladna 7, účet 1234)
----------------------------------------
                                       Kč
SYNTETICKÝ CHLÉB 500G (2)
  2,000      x    10,50 Kč/ks      21,00
TESTOVACÍ VODA 1,5L   (1)          15,90
ŠUNKA VÁŽENÁ          (2)
  0,250      x   200,00 Kč/kg      50,00
________________________________________
CELKEM 86,90 Kč
                 UŠETŘILI JSTE 4,00 Kč
________________________________________
Karta Můj Globus:          0000000000000
Bonusy celkem:                      0,00
----------------------------------------
02/03/2026      10:15:30
PLATEBNÍ KARTA                     80,00
HOTOVOST                            6,90
                Zákl.DPH             DPH
2 12% DPH          63,39            7,61
1 21% DPH          13,14            2,76
"""


def test_parse_reads_items_vat_payments_and_reconciles() -> None:
    receipt = parse(RECEIPT)
    assert receipt.get("issues") == []
    assert receipt.get("date") == "2026-03-02T10:15:30"
    assert (receipt.get("house"), receipt.get("cash_desk"), receipt.get("receipt")) == (
        4001,
        7,
        1234,
    )
    assert (receipt.get("total"), receipt.get("savings")) == ("86.90", "4.00")
    assert receipt.get("items") == [
        {
            "name": "SYNTETICKÝ CHLÉB 500G",
            "vat_code": 2,
            "quantity": "2.000",
            "unit": "ks",
            "unit_price": "10.50",
            "amount": "21.00",
            "is_weight": False,
        },
        {
            "name": "TESTOVACÍ VODA 1,5L",
            "vat_code": 1,
            "quantity": "1",
            "unit": None,
            "unit_price": "15.90",
            "amount": "15.90",
            "is_weight": False,
        },
        {
            "name": "ŠUNKA VÁŽENÁ",
            "vat_code": 2,
            "quantity": "0.250",
            "unit": "kg",
            "unit_price": "200.00",
            "amount": "50.00",
            "is_weight": True,
        },
    ]
    assert receipt.get("vat") == [
        {"code": 2, "rate": 12, "base": "63.39", "vat": "7.61"},
        {"code": 1, "rate": 21, "base": "13.14", "vat": "2.76"},
    ]
    assert receipt.get("payments") == [
        {"method": "PLATEBNÍ KARTA", "amount": "80.00"},
        {"method": "HOTOVOST", "amount": "6.90"},
    ]
    assert receipt.get("reconciliation") == {
        "items_sum": "86.90",
        "vat_sum": "86.90",
        "payments_sum": "86.90",
    }


def test_parse_keeps_unknown_item_lines_and_flags_mismatches() -> None:
    text = RECEIPT.replace(
        "TESTOVACÍ VODA 1,5L   (1)          15,90",
        "TESTOVACÍ VODA 1,5L   (1)          15,90\nSLEVA NEZNÁMÁ            -1,00",
    ).replace("2,000      x    10,50", "2,000      x    11,50")
    receipt = parse(text)
    assert receipt.get("unparsed_lines") == ["SLEVA NEZNÁMÁ            -1,00"]
    assert receipt.get("issues") == ["quantity_price_mismatch", "unparsed_line"]
    assert len(receipt.get("items", [])) == 3


def test_parse_flags_missing_total_and_unreconciled_sums() -> None:
    receipt = parse(RECEIPT.replace("CELKEM 86,90 Kč", "CELKEM 90,00 Kč"))
    assert receipt.get("issues") == [
        "items_unreconciled",
        "payments_unreconciled",
        "vat_unreconciled",
    ]
    assert "missing_total" in parse(RECEIPT.replace("CELKEM", "SUMA")).get("issues", [])


def listed(number: int) -> dict[str, object]:
    return {
        "id": f"r{number}",
        "dateTime": "2026-03-02T10:15:30",
        "house": 4001,
        "houseName": "Synthetic Hypermarket",
        "total": 86.9,
    }


def api(requests: list[httpx.Request], *, status: int = 200) -> httpx.MockTransport:
    pages = {"1": [listed(1), listed(2)], "2": [listed(3)], "3": []}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, text="private-detail")
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 synthetic")
        page = request.url.params.get("pageNumber", "")
        return httpx.Response(
            200, json={"receipts": pages.get(page, []), "totalCount": 3}
        )

    return httpx.MockTransport(handle)


def test_collect_pages_downloads_pdfs_once_and_merges_list(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    with httpx.Client(transport=api(requests)) as http:
        client = Client("cookie-secret", http=http, delay=0, sleep=lambda _: None)
        report = collect(client, tmp_path, extract=lambda _: RECEIPT)
        again = collect(client, tmp_path, extract=lambda _: RECEIPT)
    assert (report.get("unique_receipts"), report.get("downloaded_details")) == (3, 3)
    assert (again.get("cached_details"), again.get("downloaded_details")) == (3, 0)
    assert report.get("complete") is True
    assert all(
        r.headers.get("cookie") == "oidc_session=cookie-secret" for r in requests
    )
    assert {str(r.url).split("?", 1)[0] for r in requests} >= {LIST_URL}
    first = requests[0].url.params
    assert (first.get("pageSize"), first.get("includeGlobusOnline")) == ("20", "true")
    receipts = json.loads((tmp_path / "receipts.json").read_text(encoding="utf-8"))
    assert [r.get("id") for r in receipts] == ["r1", "r2", "r3"]
    assert receipts[0].get("store") == "Synthetic Hypermarket"
    assert receipts[0].get("items", [])[0].get("amount") == "21.00"
    stored = json.dumps(report) + (tmp_path / "report.json").read_text(encoding="utf-8")
    assert "cookie-secret" not in stored


def test_rejected_session_asks_for_login_without_body(tmp_path: Path) -> None:
    with (
        httpx.Client(transport=api([], status=401)) as http,
        pytest.raises(ExportError) as error,
    ):
        collect(Client("c", http=http, delay=0, sleep=lambda _: None), tmp_path)
    assert "globus login" in str(error.value)
    assert "private" not in str(error.value)


def test_non_pdf_detail_counts_as_failure(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, text="<html>")
        page = request.url.params.get("pageNumber")
        rows = [listed(1)] if page == "1" else []
        return httpx.Response(200, json={"receipts": rows, "totalCount": 1})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client("c", http=http, delay=0, sleep=lambda _: None)
        report = collect(client, tmp_path, extract=lambda _: RECEIPT)
    assert (report.get("complete"), report.get("detail_failures")) == (False, 1)
    assert (tmp_path / "receipts.partial.json").exists()


def test_list_total_rounded_to_crowns_is_not_a_mismatch() -> None:
    parsed = parse(RECEIPT)
    assert merge({"id": "r", "total": 87}, parsed).get("issues") == []
    assert merge({"id": "r", "total": 86.9}, parsed).get("issues") == []
    assert merge({"id": "r", "total": 88}, parsed).get("issues") == [
        "list_total_mismatch"
    ]
