"""Synthetic fixtures only; never put personal receipts in this directory."""

from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from cartalytix.export import ExportError
from kaufland.api import PAGE_SIZE, callback_code
from kaufland.export import Client, collect, extract


def receipt(number: int) -> dict[str, object]:
    return {
        "id": f"r{number}",
        "timestamp": f"2026-01-{number % 28 + 1:02d}T10:00:00Z",
        "currency": "CZK",
        "sum": 3000,
        "positions": [
            {"name": "Synthetic", "total": 1000},
            {"name": "Other", "total": 2000},
        ],
    }


def test_callback_code_validates_scheme_and_state() -> None:
    url = "com.kaufland.kaufland://oauth/callback?code=c1&state=s1"
    assert callback_code(url, "s1") == "c1"
    with pytest.raises(ValueError, match="state"):
        callback_code(url, "other")
    with pytest.raises(ValueError, match="redirect"):
        callback_code("https://evil.example/oauth/callback?code=c1&state=s1", "s1")


def test_extract_finds_nested_list() -> None:
    assert extract({"data": {"transactions": [{"id": "a"}]}}) == (
        [{"id": "a"}],
        "$.data.transactions",
    )
    with pytest.raises(ExportError):
        extract({"unexpected": 1})


def test_collect_pages_until_short_page_and_rotates_refresh(tmp_path: Path) -> None:
    saved: list[str] = []
    starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-srv/token":
            form = parse_qs(request.content.decode())
            assert form["refresh_token"] == ["refresh-1"]
            return httpx.Response(
                200, json={"access_token": "a1", "refresh_token": "refresh-2"}
            )
        assert request.headers["authorization"] == "Bearer a1"
        assert request.url.path == "/api/v2/customers/sub-1/transactions"
        start = int(request.url.params["start"])
        starts.append(str(start))
        count = PAGE_SIZE if start == 0 else 3
        return httpx.Response(
            200, json={"transactions": [receipt(start + i) for i in range(count)]}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = Client(
        "sub-1", "refresh-1", saved.append, http=http, delay=0, sleep=lambda _: None
    )
    report = collect(client, tmp_path)
    assert saved == ["refresh-2"]
    assert starts == ["0", str(PAGE_SIZE)]
    assert report["complete"] is True
    assert report["unique_receipts"] == PAGE_SIZE + 3
    assert report["reconciliation"] == {"sum_equals_items": PAGE_SIZE + 3}
    assert (tmp_path / "receipts.json").exists()


def test_collect_rejects_session_without_retry(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token-srv/token":
            return httpx.Response(200, json={"access_token": "a1"})
        return httpx.Response(401)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = Client(
        "sub-1", "refresh-1", lambda _: None, http=http, delay=0, sleep=lambda _: None
    )
    with pytest.raises(ExportError, match="login"):
        collect(client, tmp_path)
