"""Globus adapter; synthetic receipt text, cookies and API responses only."""

import httpx
import pytest

from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.globus.client import (
    LIST_URL,
    PROFILE_URL,
    GlobusClient,
    GlobusError,
    fetch_identity,
    parse_session_cookie,
)
from cartalytix.providers.globus.normalization import validate_receipt

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
----------------------------------------
02/03/2026      10:15:30
PLATEBNÍ KARTA                     80,00
HOTOVOST                            6,90
                Zákl.DPH             DPH
2 12% DPH          63,39            7,61
1 21% DPH          13,14            2,76
"""
SUMMARY = {"id": "r1", "total": 87, "houseName": "Synthetic Hypermarket"}


def detail(text: str = RECEIPT, identifier: str = "r1") -> dict[str, str]:
    return {"id": identifier, "text": text}


def test_receipt_maps_items_total_and_local_date() -> None:
    receipt = validate_receipt(SUMMARY, detail(), "r1")
    assert receipt is not None
    assert receipt.get("issues") == []
    assert (receipt.get("date"), receipt.get("currency"), receipt.get("total")) == (
        "2026-03-02T10:15:30",
        "CZK",
        "86.90",
    )
    assert receipt.get("merchant") == "Globus"
    items = receipt.get("items", [])
    assert [i.get("name") for i in items] == [
        "SYNTETICKÝ CHLÉB 500G",
        "TESTOVACÍ VODA 1,5L",
        "ŠUNKA VÁŽENÁ",
    ]
    assert items[0] == {
        "name": "SYNTETICKÝ CHLÉB 500G",
        "quantity": "2.000",
        "unit": "ks",
        "unit_price": "10.50",
        "original_amount": "21.00",
        "code": None,
        "is_weight": False,
        "discounts": [],
    }
    assert items[2].get("is_weight") is True


def test_unknown_lines_and_list_total_mismatch_are_quality_issues() -> None:
    text = RECEIPT.replace(
        "TESTOVACÍ VODA 1,5L   (1)          15,90",
        "TESTOVACÍ VODA 1,5L   (1)          15,90\nSLEVA NEZNÁMÁ            -1,00",
    )
    receipt = validate_receipt({**SUMMARY, "total": 88}, detail(text), "r1")
    assert receipt is not None
    assert receipt.get("issues") == ["list_total_mismatch", "unparsed_line"]
    exact = validate_receipt({**SUMMARY, "total": 86.9}, detail(), "r1")
    assert exact is not None and exact.get("issues") == []


def test_detail_for_another_receipt_is_rejected() -> None:
    with pytest.raises(GlobusError) as error:
        validate_receipt(SUMMARY, detail(identifier="r2"), "r1")
    assert error.value.code == "receipt_identity_mismatch"


def test_session_cookie_accepts_value_or_name_value_pair() -> None:
    assert parse_session_cookie("  abc.DEF-123_x  ") == "abc.DEF-123_x"
    assert parse_session_cookie("oidc_session=abc%2Fdef") == "abc%2Fdef"
    assert parse_session_cookie("abc==") == "abc=="
    for invalid in ("", "a b", "a;b", 'a"b', "x" * 4097):
        with pytest.raises(GlobusError) as error:
            parse_session_cookie(invalid)
        assert error.value.code == "session_cookie_invalid"


def api(handle: httpx.MockTransport) -> GlobusClient:
    return GlobusClient(
        "cookie-secret",
        lambda value: None,
        http=httpx.Client(transport=handle),
        delay=0,
        extract=lambda data: RECEIPT if data.startswith(b"%PDF") else "",
    )


def test_client_pages_and_downloads_pdf_with_only_the_session_cookie() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pdf"):
            return httpx.Response(200, content=b"%PDF-1.4 synthetic")
        return httpx.Response(200, json={"receipts": [{"id": "r1"}], "totalCount": 1})

    client = api(httpx.MockTransport(handle))
    page = client.page(2)
    assert isinstance(page, ProviderPayload) and page.get("totalCount") == 1
    receipt = client.detail("r/1")
    assert isinstance(receipt, ProviderPayload)
    assert receipt == {"id": "r/1", "text": RECEIPT}
    assert receipt.raw == b"%PDF-1.4 synthetic"
    assert str(requests[0].url).split("?", 1)[0] == LIST_URL
    assert dict(requests[0].url.params) == {
        "pageNumber": "2",
        "pageSize": "20",
        "totalFrom": "0",
        "includeGlobusOnline": "true",
    }
    assert requests[1].url.raw_path.endswith(b"/r%2F1/pdf")
    assert all(r.headers.get("cookie") == "oidc_session=cookie-secret" for r in requests)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "auth_required"),
        (httpx.Response(302, headers={"location": "https://www.globus.cz/"}), "auth_required"),
        (httpx.Response(200, text="<html>"), "provider_response_invalid"),
    ],
)
def test_client_maps_expired_session_and_non_pdf(response: httpx.Response, code: str) -> None:
    client = api(httpx.MockTransport(lambda request: response))
    with pytest.raises(GlobusError) as error:
        client.detail("r1")
    assert error.value.code == code


def profile(**registration: object) -> dict[str, object]:
    return {
        "authenticated": True,
        "profile": {
            "registration": registration,
            "contacts": {"email": "real@example.com"},
        },
    }


def test_identity_reads_customer_id_and_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PROFILE_URL
        assert request.headers.get("cookie") == "oidc_session=c"
        return httpx.Response(200, json=profile(customerID=42))

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        account = fetch_identity("c", http=http)
    assert (account.subject, account.email) == ("42", "real@example.com")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401), "session_cookie_rejected"),
        (httpx.Response(200, json={"authenticated": False}), "session_cookie_rejected"),
        (httpx.Response(200, json=profile()), "account_identity_unavailable"),
        (httpx.Response(503), "provider_unavailable"),
    ],
)
def test_identity_errors(response: httpx.Response, code: str) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http,
        pytest.raises(GlobusError) as error,
    ):
        fetch_identity("c", http=http)
    assert error.value.code == code
