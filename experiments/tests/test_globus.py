"""Synthetic fixtures only; never put personal receipts in this directory."""

import json

from globus.capture import Recorder, api_url, cookie_names

TOKEN = "https://login.globus.cz/oauth/token"
JSON = {"content-type": "application/json"}
RECEIPTS = {"items": [{"receiptId": "r1", "store": "Synthetic Store"}]}


def recorded() -> Recorder:
    recorder = Recorder()
    recorder.observe(
        "POST",
        TOKEN,
        JSON,
        json.dumps({"grant_type": "authorization_code", "code": "secret-code"}),
        200,
        {"access_token": "access-1", "refresh_token": "refresh-1"},
    )
    recorder.observe(
        "POST",
        TOKEN,
        JSON,
        json.dumps({"grant_type": "refresh_token", "refresh_token": "refresh-1"}),
        200,
        {"access_token": "access-2", "refresh_token": "refresh-2"},
    )
    recorder.observe(
        "GET",
        "https://api.globus.cz/loyalty/receipts?page=1",
        {
            "authorization": "Bearer access-2",
            "cookie": "session=cookie-secret; lang=cs",
            "x-app-version": "7",
        },
        None,
        200,
        RECEIPTS,
    )
    return recorder


def test_recorder_captures_tokens_refresh_shape_and_receipt_templates() -> None:
    recorder = recorded()
    assert (recorder.access_token, recorder.refresh_token) == ("access-2", "refresh-2")
    assert recorder.refresh == {
        "url": TOKEN,
        "content_type": "application/json",
        "fields": {"grant_type": "refresh_token"},
        "token_field": "refresh_token",
    }
    receipt = recorder.receipts.get("GET api.globus.cz/loyalty/receipts", {})
    assert receipt.get("query") == {"page": "1"}
    assert receipt.get("headers") == {"x-app-version": "7"}
    schema = next(iter(recorder.samples.values()))
    assert schema.get("$.items[].store:string") == 1
    assert recorder.flow[-1].get("cookies") == ["lang", "session"]
    flow = json.dumps([recorder.flow, recorder.samples])
    for secret in (
        "refresh-1",
        "refresh-2",
        "access-2",
        "secret-code",
        "cookie-secret",
        "Synthetic Store",
    ):
        assert secret not in flow


def test_graphql_operation_name_marks_receipt_request() -> None:
    recorder = Recorder()
    recorder.observe(
        "POST",
        "https://www.globus.cz/graphql",
        JSON,
        json.dumps({"operationName": "LoyaltyReceipts", "variables": {"page": 1}}),
        200,
        {"data": {"receipts": [{"id": "r1"}]}},
    )
    receipt = recorder.receipts.get("POST www.globus.cz/graphql LoyaltyReceipts", {})
    assert receipt.get("body", {}).get("variables") == {"page": 1}


def test_filters_static_and_foreign_noise() -> None:
    recorder = Recorder()
    recorder.observe("GET", "https://cdn.example.com/x", {}, None, 200, None)
    recorder.observe("GET", "https://www.globus.cz/ucet", {}, None, 200, None)
    assert [entry.get("host") for entry in recorder.flow] == ["www.globus.cz"]
    assert not api_url("https://www.globus.cz/app.JS")
    assert not api_url("http://www.globus.cz/api")
    assert cookie_names({"Cookie": "a=1; b=2=3"}) == ["a", "b"]
    assert not recorder.receipts
