"""Synthetic fixtures only; never put personal orders in this directory."""

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from cartalytix.export import ExportError
from mixit.api import Client
from mixit.auth import capture_session, token_expiry
from mixit.export import collect, normalize, shape

TOKEN = "synthetic-token-0123456789"


class API:
    def __init__(
        self,
        pages: list[dict[str, Any]],
        details: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.pages = pages
        self.details = details or {}
        self.detail_calls: list[str] = []

    def page(self, number: int) -> dict[str, Any]:
        return self.pages[number - 1]

    def detail(self, order_hash: str) -> dict[str, Any]:
        self.detail_calls.append(order_hash)
        if order_hash not in self.details:
            raise ExportError("Mixit API HTTP 404")
        return self.details[order_hash]


def order(order_hash: str) -> dict[str, Any]:
    return {
        "hash": order_hash,
        "createdAt": "02. 01. 2026",
        "currency": "Kč",
        "totalPrice": "199.5",
    }


def detail(total: str = "199.5") -> dict[str, Any]:
    return {
        "order": {
            "totalPrice": total,
            "totalPriceWithoutVat": "176.55",
            "totalVat": "22.95",
            "orderProducts": [
                {
                    "productID": 1,
                    "name": "Synthetic oats",
                    "quantity": 2,
                    "price": "49.95",
                    "totalPrice": "99.9",
                    "unit": "g",
                }
            ],
            "orderMixes": [
                {
                    "mixID": 7,
                    "name": "Synthetic mix",
                    "quantity": 1,
                    "price": "99.6",
                    "totalPrice": "99.6",
                    "mixProducts": [
                        {
                            "productID": 2,
                            "name": "A",
                            "quantity": 1,
                            "price": "59.6",
                            "totalPrice": "59.6",
                        },
                        {
                            "productID": 3,
                            "name": "B",
                            "quantity": 1,
                            "price": "40",
                            "totalPrice": "40",
                        },
                    ],
                }
            ],
        }
    }


def test_capture_session_reads_token_header_only_from_mixit_api() -> None:
    headers = {
        "Token": TOKEN,
        "Accept": "application/json",
        "X-Purchase-Channel": "web",
        "Cookie": "x",
    }
    url = "https://api.mixit.cz/api/v2/customer/profile"
    session = capture_session(url, headers, TOKEN)
    assert session == {
        "token": TOKEN,
        "header": "Token",
        "prefix": "",
        "api": "https://api.mixit.cz/api/v2",
        "context_headers": {"x-purchase-channel": "web"},
    }
    assert (
        capture_session("https://evil.example/api/v2/customer", headers, TOKEN) is None
    )
    assert capture_session(url, {"Accept": "*/*"}, TOKEN) is None
    assert capture_session(url, headers, "other-token-0123456789") is None


def test_capture_session_accepts_bearer_authorization() -> None:
    session = capture_session(
        "https://api.mixit.cz/api/v3/cart", {"Authorization": "Bearer " + TOKEN}, TOKEN
    )
    assert session is not None
    assert session["header"] == "Authorization"
    assert session["prefix"] == "Bearer "
    assert session["token"] == TOKEN


def test_token_expiry_reads_unverified_jwt_exp() -> None:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": 1700000000}).encode())
        .rstrip(b"=")
        .decode()
    )
    assert token_expiry(f"h.{payload}.s") == 1700000000
    assert token_expiry("opaque") is None
    assert token_expiry("a.!!!.c") is None


def test_client_sends_captured_header_and_hides_response_body() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/orders/missing"):
            return httpx.Response(404, text="sensitive-body")
        return httpx.Response(200, json={"orders": [], "totalRecords": 0})

    session = {"token": TOKEN, "header": "Token", "api": "https://api.mixit.cz/api/v2"}
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(session, http=http, delay=0, sleep=lambda _: None)
        assert client.page(2)["totalRecords"] == 0
        with pytest.raises(ExportError) as err:
            client.detail("missing")
    assert str(seen[0].url) == "https://api.mixit.cz/api/v2/customer/orders?page=2"
    assert seen[0].headers["Token"] == TOKEN
    assert "sensitive" not in str(err.value)
    assert TOKEN not in str(err.value)


def test_client_retries_transient_errors() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, json={"ok": True})])
    with httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))) as http:
        client = Client({"token": TOKEN}, http=http, delay=0, sleep=lambda _: None)
        assert client.get("customer/profile") == {"ok": True}


def test_collect_pages_until_total_and_caches_details(tmp_path: Path) -> None:
    api = API(
        [
            {"orders": [order("a")], "totalRecords": 2},
            {"orders": [order("b")], "totalRecords": 2},
        ],
        {"a": detail(), "b": detail()},
    )
    report = collect(api, tmp_path)
    assert report["complete"] is True
    assert report["unique_orders"] == 2
    assert report["pages"] == 2
    assert report["oldest_order"] == "2026-01-02"
    assert report["quality_issues"] == {}
    assert report["schema"]["detail"]["$.order.orderProducts[].name:string"] == 2
    receipts = json.loads((tmp_path / "receipts.json").read_text(encoding="utf-8"))[
        "receipts"
    ]
    assert receipts[0]["total"] == "199.5"
    assert receipts[0]["currency"] == "CZK"
    assert receipts[0]["items"][1]["components"][0]["total"] == "59.6"
    again = collect(api, tmp_path)
    assert again["cached_details"] == 2
    assert api.detail_calls == ["a", "b"]


def test_empty_page_before_total_is_incomplete(tmp_path: Path) -> None:
    api = API(
        [{"orders": [order("a")], "totalRecords": 3}, {"orders": [], "totalRecords": 3}]
    )
    with pytest.raises(ExportError):
        collect(api, tmp_path)


def test_repeated_page_is_rejected(tmp_path: Path) -> None:
    page = {"orders": [order("a")], "totalRecords": 2}
    with pytest.raises(ExportError):
        collect(API([page, page]), tmp_path)


def test_detail_failure_writes_partial_export(tmp_path: Path) -> None:
    api = API(
        [{"orders": [order("a"), order("b")], "totalRecords": 2}],
        {"a": detail()},
    )
    report = collect(api, tmp_path)
    assert report["complete"] is False
    assert report["detail_failures"] == 1
    assert (tmp_path / "receipts.partial.json").exists()
    assert not (tmp_path / "receipts.json").exists()


def test_normalize_reports_invalid_total_without_guessing() -> None:
    result = normalize({"hash": "a", "totalPrice": {"amount": 1}}, {})
    assert result["total"] is None
    assert "invalid_total" in result["issues"]
    assert "items_unavailable" in result["issues"]


def test_normalize_reports_unexplained_shipping_or_discount_gap() -> None:
    result = normalize(order("a"), detail(total="278.5"))
    assert result["items_total"] == "199.5"
    assert result["unexplained_difference"] == "79.0"
    assert result["issues"] == ["totals_unreconciled"]


def test_normalize_flags_line_and_mix_mismatches() -> None:
    broken = detail()
    broken["order"]["orderProducts"][0]["totalPrice"] = "100"
    broken["order"]["orderMixes"][0]["mixProducts"][1]["totalPrice"] = "41"
    broken["order"]["orderMixes"][0]["mixProducts"][1]["price"] = "41"
    issues = normalize(order("a"), broken)["issues"]
    assert "line_total_mismatch" in issues
    assert "mix_components_mismatch" in issues


def test_shape_never_contains_values() -> None:
    found = shape({"email": "secret@example.com", "items": [{"price": 1}]})
    assert "secret" not in json.dumps(found)
    assert found["$.items[].price:number"] == 1
