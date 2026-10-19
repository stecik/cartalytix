"""Kaufland adapter; synthetic accounts, tokens and receipts only."""

import json
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from cartalytix.providers.kaufland.client import (
    PAGE_SIZE,
    REDIRECT,
    KauflandClient,
    KauflandError,
    KauflandLogin,
)
from cartalytix.providers.kaufland.normalization import validate_receipt
from cartalytix.providers.lidl.login_protocol import LoginError

AUTH = "https://account.kaufland.com"


def receipt(identifier: str = "r1", **changes: object) -> dict[str, Any]:
    return {
        "id": identifier,
        "timestamp": "2026-10-19T18:27:51+02:00",
        "currency": "CZK",
        "sum": 2634,
        "saving": 2846,
        "positions": [
            {
                "name": "Synthetic coffee",
                "quantity": "1,00",
                "quantityUnit": "ST",
                "unitPrice": 4390,
                "total": 2634,
                "gtin": "0000000000001",
            },
            {
                "name": "Free gift",
                "quantity": 1,
                "quantityUnit": "ST",
                "unitPrice": 1090,
                "total": 0,
            },
        ],
        "refundPositions": [],
        **changes,
    }


class Cidaas:
    """Fake identity host following the live cidaas Remix login shape."""

    def __init__(self, *, otp: bool = False, reject: bool = False) -> None:
        self.otp, self.reject = otp, reject
        self.state = ""
        self.forms: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/authz-srv/authz":
            self.state = request.url.params["state"]
            return httpx.Response(302, headers={"location": f"{AUTH}/pages-app/login?requestId=q1"})
        if path == "/pages-app/login" and request.method == "GET":
            return httpx.Response(200, text="<html></html>")
        if path == "/pages-app/login":
            form = parse_qs(request.content.decode())
            self.forms.append(form)
            if self.reject:
                return httpx.Response(200, json={"success": False, "error": "invalid"})
            if self.otp and not form.get("code"):
                return httpx.Response(
                    200, json={"success": True, "exchangeId": "x1", "verificationType": "EMAIL"}
                )
            return httpx.Response(204, headers={"x-remix-redirect": "/authz-srv/continue"})
        if path == "/authz-srv/continue":
            return httpx.Response(
                302, headers={"location": f"{REDIRECT}?code=c1&state={self.state}"}
            )
        if path == "/token-srv/token":
            form = parse_qs(request.content.decode())
            assert form["code"] == ["c1"] and form["code_verifier"]
            return httpx.Response(200, json={"access_token": "a1", "refresh_token": "refresh-1"})
        raise AssertionError(path)


def test_login_without_code_returns_refresh_and_sends_password_only_to_kaufland() -> None:
    cidaas = Cidaas()
    login = KauflandLogin(http=httpx.Client(transport=httpx.MockTransport(cidaas)))
    assert login.begin().step == "password"
    assert login.credentials("owner@example.com", "private-password").step == "completed"
    assert login.take_refresh_token() == "refresh-1"
    assert cidaas.forms[0]["password"] == ["private-password"]


def test_login_with_code_and_rejection() -> None:
    cidaas = Cidaas(otp=True)
    login = KauflandLogin(http=httpx.Client(transport=httpx.MockTransport(cidaas)))
    login.begin()
    status = login.credentials("owner@example.com", "private-password")
    assert (status.step, status.method) == ("otp", "EMAIL")
    assert login.verify("123456").step == "completed"
    assert cidaas.forms[1]["exchangeId"] == ["x1"] and cidaas.forms[1]["code"] == ["123456"]
    rejected = KauflandLogin(http=httpx.Client(transport=httpx.MockTransport(Cidaas(reject=True))))
    rejected.begin()
    with pytest.raises(LoginError, match="credentials_rejected"):
        rejected.credentials("owner@example.com", "wrong")


def test_login_never_follows_redirects_off_the_identity_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authz-srv/authz":
            return httpx.Response(302, headers={"location": f"{AUTH}/pages-app/login?requestId=q"})
        if request.method == "POST":
            return httpx.Response(204, headers={"x-remix-redirect": "https://evil.example/x"})
        return httpx.Response(200)

    login = KauflandLogin(http=httpx.Client(transport=httpx.MockTransport(handler)))
    login.begin()
    with pytest.raises(LoginError, match="login_flow_incomplete"):
        login.credentials("owner@example.com", "private-password")


def test_client_pages_by_offset_rotates_refresh_and_serves_details() -> None:
    saved: list[str] = []
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-srv/token":
            return httpx.Response(
                200, json={"access_token": "a1", "refresh_token": "refresh-2", "expires_in": 86400}
            )
        if request.url.path == "/users-srv/userinfo":
            return httpx.Response(200, json={"sub": "sub/1"})
        assert request.url.raw_path.startswith(b"/api/v2/customers/sub%2F1/transactions?")
        assert request.headers["authorization"] == "Bearer a1"
        starts.append(request.url.params["start"])
        count = PAGE_SIZE if request.url.params["start"] == "0" else 2
        offset = int(request.url.params["start"])
        return httpx.Response(200, json=[receipt(f"r{offset + i}") for i in range(count)])

    client = KauflandClient(
        "refresh-1",
        saved.append,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=0,
    )
    assert len(client.page(1)["transactions"]) == PAGE_SIZE
    assert len(client.page(2)["transactions"]) == 2
    assert client.page(3)["transactions"] == []
    assert starts == ["0", str(PAGE_SIZE)] and saved == ["refresh-2"]
    assert client.detail("r51")["id"] == "r51"
    with pytest.raises(KauflandError):
        client.detail("missing")


def test_client_session_rejection_requires_login() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-srv/token":
            return httpx.Response(400)
        raise AssertionError(request.url)

    client = KauflandClient(
        "refresh-1",
        lambda _: None,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=0,
    )
    with pytest.raises(KauflandError, match="auth_required"):
        client.page(1)


def test_normalization_derives_card_discounts_from_regular_prices() -> None:
    normalized = validate_receipt({}, receipt(), "r1")
    assert normalized["issues"] == []
    assert normalized["total"] == "26.34"
    first, second = normalized["items"]
    assert (first["quantity"], first["unit"], first["original_amount"]) == ("1.00", "st", "43.90")
    assert first["discounts"] == [{"name": "Kaufland Card", "amount": "17.56"}]
    assert second["discounts"] == [{"name": "Kaufland Card", "amount": "10.90"}]
    saving = sum(Decimal(d["amount"]) for item in normalized["items"] for d in item["discounts"])
    assert saving == Decimal("28.46")


def test_normalization_reports_unreconciled_totals_and_refunds() -> None:
    broken = validate_receipt({}, receipt(sum=9999), "r1")
    assert "totals_unreconciled" in broken["issues"]
    refund = {
        "name": "Returned",
        "quantity": 1,
        "quantityUnit": "ST",
        "unitPrice": 500,
        "total": 500,
    }
    refunded = validate_receipt({}, receipt(sum=2134, refundPositions=[refund]), "r1")
    assert refunded["issues"] == []
    assert refunded["items"][-1]["original_amount"] == "-5.00"
    with pytest.raises(KauflandError):
        validate_receipt({}, receipt(), "other")


def test_redirect_parsing_matches_custom_scheme() -> None:
    parsed = urlsplit(f"{REDIRECT}?code=c&state=s")
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "com.kaufland.kaufland",
        "oauth",
        "/callback",
    )
    assert json.loads(json.dumps(receipt()))["id"] == "r1"
