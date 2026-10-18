"""Synthetic fixtures only; never put personal orders in this directory."""

import json
from pathlib import Path

import httpx
import pytest

from cartalytix.export import ExportError
from wolt.api import renew
from wolt.capture import Recorder, api_url
from wolt.export import Client, collect

AUTH = "https://authentication.wolt.com/v1/wauth2/access_token"
FORM = {"content-type": "application/x-www-form-urlencoded"}
ORDERS = {"orders": [{"purchase_id": "p1", "venue_name": "Synthetic Venue"}]}


def recorded() -> Recorder:
    recorder = Recorder()
    recorder.observe(
        "POST",
        AUTH,
        FORM,
        "grant_type=authorization_code&code=secret-code",
        200,
        {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 1800},
    )
    recorder.observe(
        "POST",
        AUTH,
        FORM,
        "grant_type=refresh_token&refresh_token=refresh-1",
        200,
        {"access_token": "access-2", "refresh_token": "refresh-2"},
    )
    recorder.observe(
        "GET",
        "https://consumer-api.wolt.com/order-tracking/orders?limit=10",
        {"authorization": "Bearer access-2", "w-wolt-session-id": "s1"},
        None,
        200,
        ORDERS,
    )
    return recorder


def test_recorder_captures_tokens_refresh_shape_and_order_templates() -> None:
    recorder = recorded()
    assert recorder.complete()
    assert (recorder.access_token, recorder.refresh_token) == ("access-2", "refresh-2")
    template = recorder.refresh_template()
    assert template.get("observed") is True
    assert template.get("fields") == {"grant_type": "refresh_token"}
    assert template.get("token_field") == "refresh_token"
    order = next(iter(recorder.orders.values()))
    assert order.get("query") == {"limit": "10"}
    assert order.get("headers") == {"w-wolt-session-id": "s1"}
    schema = next(iter(recorder.samples.values()))
    assert schema.get("$.orders[].venue_name:string") == 1
    flow = json.dumps([recorder.flow, recorder.samples])
    for secret in (
        "refresh-1",
        "refresh-2",
        "access-2",
        "secret-code",
        "Synthetic Venue",
    ):
        assert secret not in flow


def test_unobserved_refresh_falls_back_to_oauth_grant() -> None:
    recorder = Recorder()
    assert recorder.refresh_template().get("fields") == {"grant_type": "refresh_token"}
    assert api_url("https://consumer-api.wolt.com/x")
    assert not api_url("https://wolt-com-static-assets.wolt.com/app.js")
    assert not api_url("https://example.com/order")


def test_renew_sends_refresh_only_to_auth_host_and_saves_rotation() -> None:
    saved: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == AUTH
        assert request.content == b"grant_type=refresh_token&refresh_token=refresh-1"
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "refresh-2"}
        )

    template = recorded().refresh_template()
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert renew(template, "refresh-1", saved.append, http=http) == "a"
    assert saved == ["refresh-2"]
    with pytest.raises(ExportError):
        renew(
            {**template, "url": "https://evil.example/token"}, "refresh-1", saved.append
        )


def test_export_renews_once_replays_headers_and_caches(tmp_path: Path) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.host == "authentication.wolt.com":
            return httpx.Response(
                200, json={"access_token": "access", "refresh_token": "r2"}
            )
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["client-version"] == "1.0"
        if request.url.path.endswith("/order_history/"):
            assert request.url.params["limit"] == "50"
            return httpx.Response(
                200, json={"orders": [{"purchase_id": "p1", "status": "delivered"}]}
            )
        assert request.url.params["tips_use_percentage"] == "true"
        return httpx.Response(200, json={"order_id": "p1", "total_price": 19900})

    session = {
        "refresh": recorded().refresh_template(),
        "orders": {
            "GET x/order_history/purchase/p0": {"headers": {"client-version": "1.0"}}
        },
    }
    saved: list[str] = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(
            session, "refresh-1", saved.append, http=http, delay=0, sleep=lambda _: None
        )
        report = collect(client, tmp_path)
        again = collect(client, tmp_path)
    assert (report["unique_orders"], report["downloaded_details"]) == (1, 1)
    assert again["cached_details"] == 1
    assert report["detail_schema"]["$.total_price:number"] == 1
    assert saved == ["r2"]
    assert calls.count("/v1/wauth2/access_token") == 1


def test_renew_errors_hide_response_body() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(400, text="private-detail")
    )
    with httpx.Client(transport=transport) as http, pytest.raises(ExportError) as error:
        renew(recorded().refresh_template(), "refresh-1", lambda _: None, http=http)
    assert "private" not in str(error.value)
