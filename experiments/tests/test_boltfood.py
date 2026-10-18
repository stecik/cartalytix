"""Synthetic fixtures only; never put personal orders in this directory."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from boltfood.api import Client
from boltfood.auth import Recorder
from boltfood.capture import find_path, token_header, values_at
from boltfood.export import collect
from cartalytix.export import ExportError

BASE = "https://deliveryuser.live.boltsvc.net"
REFRESH = "synthetic-refresh-token"
PATH = ["data", "orders", "[]", "order_id"]
PAGE_1 = {
    "code": 0,
    "data": {
        "orders": [{"order_id": 11, "city_id": 7}, {"order_id": 12, "city_id": 8}],
        "next_page_token": "p2",
    },
}
PAGE_2 = {
    "code": 0,
    "data": {"orders": [{"order_id": 13, "city_id": 7}], "next_page_token": ""},
}


def recorded() -> Recorder:
    recorder = Recorder()
    recorder.observe(
        "POST",
        f"{BASE}/profile/verification/start?version=web",
        {},
        {"phone_number": "+420000000000", "type": "phone"},
        200,
        {"code": 0},
    )
    recorder.observe(
        "POST",
        f"{BASE}/profile/verification/confirm?version=web",
        {},
        {"code": "1234", "phone_number": "+420000000000"},
        200,
        {
            "code": 0,
            "data": {
                "user_jwt_authentication": {
                    "refresh_token": REFRESH,
                    "access_token": "initial-access",
                }
            },
        },
    )
    recorder.observe(
        "GET",
        f"{BASE}/deliveryClient/getOrderHistory?version=web&page_size=5&page_token=p2",
        {"x-client": "web"},
        None,
        200,
        PAGE_1,
    )
    recorder.observe(
        "POST",
        f"{BASE}/deliveryClient/v2/getOrderDetails?version=web",
        {},
        {"order_id": 12, "city_id": 8},
        200,
        {"code": 0},
    )
    return recorder


def test_recorder_takes_tokens_from_confirm_and_logs_only_names() -> None:
    recorder = recorded()
    assert recorder.complete()
    assert recorder.challenge_seen is False
    assert recorder.access_token == "initial-access"
    session = recorder.session()
    refresh = session.get("refresh", {})
    assert refresh.get("path") == "/profile/eater/auth/getAccessTokenWithRotation"
    assert refresh.get("observed") is False
    assert refresh.get("query") == {"version": "web"}
    assert session.get("history", {}).get("query") == {
        "version": "web",
        "page_size": "5",
    }
    assert session.get("detail", {}).get("id_path") == PATH
    flow = json.dumps(recorder.flow)
    assert REFRESH not in flow and "+420" not in flow and "1234" not in flow
    assert recorder.flow[1].get("jwt_keys") == ["access_token", "refresh_token"]


def test_shared_city_is_never_chosen_as_order_id() -> None:
    recorder = Recorder()
    recorder.history = {"query": {}}
    recorder.history_sample = {
        "data": {
            "orders": [{"city_id": 8, "order_id": 1}, {"city_id": 8, "order_id": 2}]
        }
    }
    recorder.detail = {"body": {"city_id": 8, "order_id": 2}}
    session = recorder.session()
    assert session.get("detail", {}).get("id_path") == PATH


def test_challenge_is_detected_from_verification_response() -> None:
    recorder = Recorder()
    recorder.observe(
        "POST",
        f"{BASE}/profile/verification/start",
        {},
        {"type": "phone"},
        200,
        {"code": 1, "data": {"challenge": {"type": "recaptcha"}}},
    )
    assert recorder.challenge_seen is True


def test_capture_helpers() -> None:
    assert token_header({"Authorization": "Bearer abc"}, "abc") == (
        "authorization",
        "Bearer ",
    )
    path = find_path(PAGE_1, 12)
    assert path == ("data", "orders", "[]", "order_id")
    assert values_at(PAGE_1, path) == [11, 12]


def session(access: str | None = None) -> dict[str, Any]:
    return {**recorded().session(), "refresh_token": REFRESH, "access_token": access}


def test_client_renews_saves_rotation_and_uses_per_order_body() -> None:
    saved: list[str] = []
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("getAccessTokenWithRotation"):
            assert request.headers["Authorization"] == "Bearer " + REFRESH
            data = {"access_token": "access", "refresh_token": "next"}
            return httpx.Response(200, json={"code": 0, "data": data})
        assert saved == ["next"]
        assert request.headers["Authorization"] == "Bearer access"
        if request.url.path.endswith("getOrderDetails"):
            assert json.loads(request.content) == {"order_id": 11, "city_id": 7}
            return httpx.Response(200, json={"code": 0, "data": {}})
        assert request.url.params["page_token"] == "p2"
        return httpx.Response(200, json=PAGE_2)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(
            session(), saved.append, http=http, delay=0, sleep=lambda _: None
        )
        assert client.history("p2") == PAGE_2
        assert client.detail({"order_id": 11, "city_id": 7}) == {"code": 0, "data": {}}
    assert seen[1].url.params["version"] == "web"
    assert len([r for r in seen if "getAccessToken" in r.url.path]) == 1


def test_initial_access_token_avoids_refresh() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert "getAccessToken" not in request.url.path
        assert request.headers["Authorization"] == "Bearer initial-access"
        return httpx.Response(200, json=PAGE_2)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(
            session("initial-access"),
            lambda _: None,
            http=http,
            delay=0,
            sleep=lambda _: None,
        )
        assert client.history() == PAGE_2


def test_bolt_error_codes_never_leak_body() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"code": 503, "message": "private-detail"})
    )
    with httpx.Client(transport=transport) as http:
        client = Client(
            session("initial-access"),
            lambda _: None,
            http=http,
            delay=0,
            sleep=lambda _: None,
        )
        with pytest.raises(ExportError) as error:
            client.history()
    assert "503" in str(error.value)
    assert "private" not in str(error.value)


class API:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.tokens: list[str | None] = []

    def history(self, page_token: str | None = None) -> dict[str, Any]:
        self.tokens.append(page_token)
        return PAGE_2 if page_token == "p2" else PAGE_1

    def detail(self, order: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(order.get("order_id"))
        if order.get("order_id") == 12:
            raise ExportError("Bolt API code 1")
        return {
            "code": 0,
            "data": {"order_id": order.get("order_id"), "total": "199.00"},
        }

    def renew(self) -> None:
        raise ExportError("Bolt API code 401")


def test_collect_paginates_caches_details_and_checks_refresh(tmp_path: Path) -> None:
    api = API()
    report = collect(api, tmp_path, PATH)
    assert api.tokens == [None, "p2"]
    assert (report["unique_orders"], report["pages"], report["downloaded_details"]) == (
        3,
        2,
        2,
    )
    assert report["complete"] is False
    assert report["refresh_check"] == "Bolt API code 401"
    assert report["detail_schema"]["$.data.total:string"] == 2
    again = collect(api, tmp_path, PATH)
    assert again["cached_details"] == 2
    assert api.calls == [11, 12, 13, 12]
