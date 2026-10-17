import asyncio
import time
from collections.abc import Iterator
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from http_login import app as probe
from http_login.client import HttpLogin, Status
from http_login.test_client import page


def test_idle_probe_expires_provider_state_without_requests() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=page({"CountryCode": "CZ"}))
        )
    ) as http:
        provider = HttpLogin(http=http)
        provider.begin()
        probe.flows["abandoned"] = probe.Flow(
            "owner", created=time.monotonic() - probe.TTL - 1, client=provider
        )

        async def check() -> None:
            async with probe.app.router.lifespan_context(probe.app):
                await asyncio.sleep(0.05)
                assert "abandoned" not in probe.flows
                assert provider._phase == "closed"

        try:
            with patch.object(probe, "CLEANUP_INTERVAL", 0.01, create=True):
                asyncio.run(check())
        finally:
            provider.close()
            probe.flows.pop("abandoned", None)


@pytest.fixture
def browser() -> Iterator[TestClient]:
    probe.flows.clear()
    with TestClient(probe.app, base_url="http://localhost:18081") as client:
        yield client
    for flow in probe.flows.values():
        if flow.client:
            flow.client.close()
    probe.flows.clear()


def test_probe_requires_existing_application_login(browser: TestClient) -> None:
    assert browser.get("/").status_code == 401


def test_initialization_check_never_submits_credentials_or_consumes_flow(
    browser: TestClient,
) -> None:
    provider = MagicMock()
    provider.begin.return_value = Status("password")
    provider.diagnostics = {"requests": 1, "captcha_token_acquired": True}

    def application(
        request: Request, method: str, path: str, payload: object = None
    ) -> dict[str, object]:
        assert method == "GET"
        return {"id": "owner"} if path == "auth/me" else {"items": []}

    with (
        patch.object(probe, "application", application),
        patch.object(probe, "HYBRID", True),
        patch.object(probe, "HybridLogin", return_value=provider),
    ):
        browser.get("/")
        csrf = browser.cookies.get("cartalytix_http_probe")
        headers = {"Origin": "http://localhost:18081"}
        invalid = browser.post(
            "/check",
            json={"csrf": csrf, "password": "private-password"},
            headers=headers,
        )
        assert invalid.status_code == 422 and "private-password" not in invalid.text
        provider.begin.assert_not_called()
        response = browser.post("/check", json={"csrf": csrf}, headers=headers)
        assert response.json()["step"] == "ready"
        provider.credentials.assert_not_called()
        provider.verify.assert_not_called()
        provider.close.assert_called_once()
        assert next(iter(probe.flows.values())).client is None


def test_probe_rejects_cross_site_password_submission_before_auth(
    browser: TestClient,
) -> None:
    response = browser.post(
        "/start",
        json={"password": "private"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert "private" not in response.text


def test_validation_never_echoes_invalid_credential_body(browser: TestClient) -> None:
    response = browser.post(
        "/start",
        json={"password": "private", "connection": "bad"},
        headers={"Origin": "http://localhost:18081"},
    )
    assert response.status_code == 422
    assert "private" not in response.text


def test_authenticated_probe_is_owner_bound_and_no_secret_is_logged(
    browser: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identifier = str(uuid4())
    owner = "owner"
    calls: list[str] = []

    def application(
        request: Request, method: str, path: str, payload: object = None
    ) -> dict[str, object]:
        if not request.cookies.get(probe.COOKIE):
            raise HTTPException(401)
        if path == "auth/me":
            return {"id": owner}
        return {"items": [{"id": identifier, "provider": "lidl", "name": "My Lidl"}]}

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            200,
            text=page(
                {"CountryCode": "CZ"},
                errors=["InvalidDeviceIdError", "private-password"],
            )
            if request.method == "POST"
            else page({"CountryCode": "CZ"}),
        )

    browser.cookies.set(probe.COOKIE, "application-session")
    with (
        httpx.Client(transport=httpx.MockTransport(upstream)) as http,
        patch.object(probe, "application", application),
        patch.object(probe, "HttpLogin", lambda: HttpLogin(http=http)),
    ):
        response = browser.get("/")
        assert response.headers.get("cache-control") == "no-store"
        csrf = browser.cookies.get("cartalytix_http_probe")
        body = {
            "csrf": csrf,
            "connection": identifier,
            "email": "owner@example.com",
            "password": "private-password",
        }
        headers = {"Origin": "http://localhost:18081"}
        owner = "another-owner"
        assert browser.post("/start", json=body, headers=headers).status_code == 409
        assert not calls
        owner = "owner"
        response = browser.post("/start", json=body, headers=headers)
        assert response.json().get("error") == "device_verification_required"
        assert "private-password" not in response.text
        assert "private-password" not in capsys.readouterr().out
        assert browser.post("/start", json=body, headers=headers).status_code == 409
    assert calls == ["GET", "POST"]


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"error": {"code": "auth_required", "message": "Request failed"}},
            "application_request_rejected:auth_required",
        ),
        ({"error": {"code": "private value"}}, "application_request_rejected"),
        ({"detail": "private"}, "application_request_rejected"),
    ],
)
def test_application_rejection_exposes_only_fixed_backend_code(
    body: dict[str, object], expected: str
) -> None:
    client = httpx.Client
    request = MagicMock()
    request.cookies = {probe.COOKIE: "application-session"}

    def upstream(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=body)

    with (
        patch.object(
            probe.httpx,
            "Client",
            lambda **options: client(
                transport=httpx.MockTransport(upstream), **options
            ),
        ),
        pytest.raises(HTTPException) as error,
    ):
        probe.application(request, "POST", "providers/lidl/connections/x/authorize")
    assert error.value.status_code == 422 and error.value.detail == expected


def test_rejected_handoff_returns_diagnostics_and_closes_client() -> None:
    client = MagicMock()
    client.take_refresh_token.return_value = "private-refresh"
    client.diagnostics = {"http_status": 302}
    flow = probe.Flow("owner", client=client, connection=uuid4())

    def application(*_args: object) -> dict[str, object]:
        raise HTTPException(422, "application_request_rejected:auth_required")

    with patch.object(probe, "application", application):
        output = probe.result(MagicMock(), flow, Status("completed"))
    assert output == {
        "step": "failed",
        "error": "application_request_rejected:auth_required",
        "diagnostics": {"http_status": 302},
    }
    client.close.assert_called_once()


def test_mfa_handoff_uses_fixed_authorize_endpoint_and_closes_client(
    browser: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    identifier = str(uuid4())
    state = ""
    handoffs: list[str] = []

    def application(
        request: Request, method: str, path: str, payload: object = None
    ) -> dict[str, object]:
        if path == "auth/me":
            return {"id": "owner"}
        if path == "connections":
            return {
                "items": [{"id": identifier, "provider": "lidl", "name": "My Lidl"}]
            }
        assert path == f"providers/lidl/connections/{identifier}/authorize"
        assert method == "POST" and payload == {"refresh_token": "private-refresh"}
        handoffs.append(path)
        return {"id": identifier}

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal state
        if request.method == "GET":
            state = request.url.params.get("state", "")
            return httpx.Response(200, text=page({"TwoFactorRequired": False}))
        if request.url.path == "/Account/Login":
            return httpx.Response(
                200, text=page({"TwoFactorRequired": True, "OtpMethod": "Email"})
            )
        if request.url.path == "/Account/Login/Otp":
            assert parse_qs(request.content.decode())["VerificationCode"] == ["123456"]
            return httpx.Response(
                302,
                headers={
                    "Location": f"com.lidlplus.app://callback?code=once&state={state}"
                },
            )
        assert request.url.path == "/connect/token"
        return httpx.Response(200, json={"refresh_token": "private-refresh"})

    with (
        httpx.Client(transport=httpx.MockTransport(upstream)) as http,
        patch.object(probe, "application", application),
        patch.object(probe, "HttpLogin", lambda: HttpLogin(http=http)),
    ):
        browser.get("/")
        csrf = browser.cookies.get("cartalytix_http_probe")
        headers = {"Origin": "http://localhost:18081"}
        response = browser.post(
            "/start",
            json={
                "csrf": csrf,
                "connection": identifier,
                "email": "owner@example.com",
                "password": "private-password",
            },
            headers=headers,
        )
        assert response.json()["step"] == "otp"
        response = browser.post(
            "/verify", json={"csrf": csrf, "code": "123456"}, headers=headers
        )
        output = response.json()
        assert output.get("step") == "completed" and output.get("linked") is True
        assert not any(
            key.startswith("refresh_") for key in output.get("diagnostics", {})
        )
        client = next(iter(probe.flows.values())).client
        assert client is not None and client._phase == "closed"
        assert "private-refresh" not in repr(client.__dict__)
        assert (
            browser.post(
                "/verify", json={"csrf": csrf, "code": "123456"}, headers=headers
            ).json()["error"]
            == "login_not_active"
        )
    assert len(handoffs) == 1
    output = capsys.readouterr().out
    assert "private-refresh" not in output and "private-password" not in output
