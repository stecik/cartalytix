import httpx
import pytest

from cartalytix.api import Client
from cartalytix.export import ExportError


def test_rotated_refresh_token_is_saved_before_receipts_are_requested():
    saved = []

    def handle(req):
        if req.url.path == "/connect/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "rotated",
                    "expires_in": 3600,
                },
            )
        assert saved[0]["refresh_token"] == "rotated"
        assert req.url.params["onlyFavorite"] == "false"
        assert req.url.params["pageNumber"] == "2"
        assert req.headers["Accept-Language"] == "cs-CZ"
        return httpx.Response(200, json={"tickets": [], "totalCount": 0})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(
            {"refresh_token": "old", "profile": "test"},
            saved.append,
            http=http,
            delay=0,
        )
        assert client.page(2)["tickets"] == []


def test_api_errors_do_not_reveal_response_body_or_tokens():
    def handle(req):
        return httpx.Response(403, text="sensitive-token-from-server")

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client({"refresh_token": "secret"}, lambda x: None, http=http, delay=0)
        with pytest.raises(ExportError) as err:
            client.page(1)
        assert "sensitive" not in str(err.value)
        assert "secret" not in str(err.value)


def test_transient_get_is_retried_but_token_exchange_is_not_replayed():
    responses = iter(
        [
            httpx.Response(503),
            httpx.Response(200, json={"tickets": [], "totalCount": 0}),
        ]
    )
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: next(responses))
    ) as http:
        client = Client(
            {"access_token": "a", "expires_at": 9999999999},
            lambda x: None,
            http=http,
            delay=0,
        )
        assert client.page(1)["totalCount"] == 0


def test_czech_detail_requires_bare_language_code():
    def handle(req):
        assert req.headers["Accept-Language"] == "cs"
        assert req.url.path == "/api/v3/CZ/tickets/test"
        return httpx.Response(200, json={"id": "test"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(
            {"access_token": "a", "expires_at": 9999999999},
            lambda x: None,
            http=http,
            delay=0,
        )
        assert client.detail("test")["id"] == "test"
