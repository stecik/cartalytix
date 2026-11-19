import json
from collections.abc import Callable
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_lidl_login_protocol import page
from test_lidl_login_sessions import FakeLogin
from test_lidl_routes import HEADERS, PREFIX, create
from test_lidl_routes import authorized as authorized

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection, SyncRun
from cartalytix.providers.lidl.client import LidlClient, LidlError, TokenData
from cartalytix.providers.lidl.login_protocol import HttpLogin
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.sync import enqueue_sync, run_sync


def test_browser_login_is_authenticated_and_checks_origin(client: TestClient) -> None:
    url = f"{PREFIX}/{uuid4()}/login"
    payload = {"email": "owner@example.com", "password": "private-password"}
    assert client.post(url, json=payload, headers=HEADERS).status_code == 401


def test_login_handoff_encrypts_token_and_returns_no_secrets(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(authorized)
    login = FakeLogin(otp=False)
    app.state.lidl_logins = LoginSessions(factory=lambda: login)
    identifier = create(authorized)
    monkeypatch.setattr(
        "cartalytix.lidl_routes.exchange_refresh",
        lambda _: pytest.fail("Fresh login must not refresh immediately"),
    )
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={
            "email": "owner@example.com",
            "password": "private-password",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("step") == "completed"
    assert response.json().get("connection", {}).get("status") == "connected"
    assert "private-" not in response.text and login.closed
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token
        assert (
            json.loads(
                TokenCipher(app.state.settings).decrypt(connection.encrypted_refresh_token)
            ).get("refresh_token")
            == "private-refresh"
        )


def test_mfa_flow_can_be_used_only_for_its_connection_and_once(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(authorized)
    login = FakeLogin()
    app.state.lidl_logins = LoginSessions(factory=lambda: login)
    first, second = create(authorized), create(authorized)
    monkeypatch.setattr(
        "cartalytix.lidl_routes.exchange_refresh",
        lambda _: pytest.fail("Fresh login must not refresh immediately"),
    )
    response = authorized.post(
        f"{PREFIX}/{first}/login",
        json={
            "email": "owner@example.com",
            "password": "private-password",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    flow = response.json().get("flow_id")
    assert response.json().get("step") == "otp" and flow
    verify = {"code": "123456"}
    assert (
        authorized.post(
            f"{PREFIX}/{second}/login/{flow}/verify", json=verify, headers=HEADERS
        ).status_code
        == 409
    )
    response = authorized.post(
        f"{PREFIX}/{first}/login/{flow}/verify", json=verify, headers=HEADERS
    )
    assert response.status_code == 200 and response.json().get("step") == "completed"
    assert (
        authorized.post(
            f"{PREFIX}/{first}/login/{flow}/verify", json=verify, headers=HEADERS
        ).status_code
        == 409
    )
    assert login.posts == 2 and login.closed


def test_foreign_connection_origin_and_validation_stop_before_lidl(
    authorized: TestClient, engine: Engine
) -> None:
    app = application(authorized)
    login = FakeLogin()
    app.state.lidl_logins = LoginSessions(factory=lambda: login)
    with Session(engine) as db:
        foreign = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    payload = {"email": "owner@example.com", "password": "private-password"}
    assert (
        authorized.post(f"{PREFIX}/{foreign}/login", json=payload, headers=HEADERS).status_code
        == 404
    )
    identifier = create(authorized)
    assert authorized.post(f"{PREFIX}/{identifier}/login", json=payload).status_code == 403
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={
            "email": "invalid",
            "password": "private-password",
        },
        headers=HEADERS,
    )
    assert response.status_code == 422 and "private-password" not in response.text
    assert login.posts == 0


def test_cancellation_and_provider_failure_never_expose_provider_body(
    authorized: TestClient,
) -> None:
    app = application(authorized)
    login = FakeLogin()
    app.state.lidl_logins = LoginSessions(factory=lambda: login)
    identifier = create(authorized)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={
            "email": "owner@example.com",
            "password": "private-password",
        },
        headers=HEADERS,
    )
    flow = response.json().get("flow_id")
    assert flow
    assert (
        authorized.delete(f"{PREFIX}/{identifier}/login/{flow}", headers=HEADERS).status_code == 204
    )
    assert login.closed
    failure = FakeLogin(fail=True)
    app.state.lidl_logins = LoginSessions(factory=lambda: failure)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={
            "email": "owner@example.com",
            "password": "private-password",
        },
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "login_request_uncertain"
    assert "private-password" not in response.text and failure.closed


def test_rate_limit_stops_before_initialization(authorized: TestClient) -> None:
    class RejectLimiter:
        def check(self, address: str, email: str) -> bool:
            return False

    app = application(authorized)
    login = FakeLogin()
    app.state.lidl_logins = LoginSessions(factory=lambda: login)
    identifier = create(authorized)
    app.state.limiter = RejectLimiter()
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"email": "owner@example.com", "password": "private-password"},
        headers=HEADERS,
    )
    assert response.status_code == 429 and login.posts == 0


@pytest.mark.parametrize("otp", [False, True])
@pytest.mark.parametrize("old_subject", [None, "account-a", "account-b"])
def test_fresh_login_persists_initial_tokens_without_early_refresh(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    otp: bool,
    old_subject: str | None,
) -> None:
    app = application(authorized)
    identifier = create(authorized)
    cipher = TokenCipher(app.state.settings)
    with Session(engine) as db, db.begin():
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None
        connection.account_subject = old_subject
        connection.encrypted_refresh_token = cipher.encrypt("previous-refresh")
    state = ""
    grants: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal state
        if request.url.host == "tickets.lidlplus.com":
            assert request.headers.get("Authorization") == (
                "Bearer header.eyJzdWIiOiJhY2NvdW50LWEifQ.signature"
            )
            return httpx.Response(200, json={"tickets": [], "totalCount": 0})
        if request.method == "GET":
            state = request.url.params.get("state", "")
            return httpx.Response(200, text=page({"CountryCode": "CZ"}))
        values = parse_qs(request.content.decode())
        if request.url.path == "/Account/Login" and otp:
            return httpx.Response(200, text=page({"TwoFactorRequired": True, "OtpMethod": "Email"}))
        if request.url.path in {"/Account/Login", "/Account/Login/Otp"}:
            return httpx.Response(
                302, headers={"Location": f"com.lidlplus.app://callback?code=once&state={state}"}
            )
        grants.append(values.get("grant_type", [""])[0])
        if values.get("grant_type") != ["authorization_code"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "access_token": "header.eyJzdWIiOiJhY2NvdW50LWEifQ.signature",
                "refresh_token": "initial-refresh",
                "expires_in": 1200,
            },
        )

    def reject_early_refresh(refresh_token: str) -> TokenData:
        raise LidlError(
            "auth_required",
            diagnostic={"phase": "refresh", "status": 400, "reason": "invalid_grant"},
        )

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", reject_early_refresh)
    with httpx.Client(transport=httpx.MockTransport(provider)) as http:
        app.state.lidl_logins = LoginSessions(factory=lambda: HttpLogin(http=http))
        response = authorized.post(
            f"{PREFIX}/{identifier}/login",
            json={"email": "owner@example.com", "password": "private-password"},
            headers=HEADERS,
        )
        if otp:
            assert response.status_code == 200 and response.json().get("step") == "otp"
            flow = response.json().get("flow_id")
            response = authorized.post(
                f"{PREFIX}/{identifier}/login/{flow}/verify",
                json={"code": "123456"},
                headers=HEADERS,
            )
        if old_subject != "account-b":
            assert response.status_code == 200, response.text
            with Session(engine) as db:
                connection = db.get(ProviderConnection, UUID(identifier))
                assert connection is not None
                owner_id = connection.owner_id

            def sync_client(
                credentials: str,
                save_refresh: Callable[[str], None],
                *,
                heartbeat: Callable[[], None] | None = None,
            ) -> LidlClient:
                return LidlClient(
                    credentials, save_refresh, http=http, delay=0, heartbeat=heartbeat
                )

            run = enqueue_sync(engine, UUID(identifier), owner_id)
            run_sync(engine, run.id, app.state.settings, client_factory=sync_client)
            with Session(engine) as db:
                synced = db.get(SyncRun, run.id)
                assert synced is not None and synced.status == "completed"
    assert response.status_code == (409 if old_subject == "account-b" else 200), response.text
    assert "initial-refresh" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        stored = cipher.decrypt(connection.encrypted_refresh_token)
        if old_subject == "account-b":
            assert stored == "previous-refresh"
            assert connection.account_subject == "account-b"
        else:
            tokens = json.loads(stored)
            assert tokens.get("refresh_token") == "initial-refresh"
            assert tokens.get("access_token") == "header.eyJzdWIiOiJhY2NvdW50LWEifQ.signature"
            assert connection.account_subject == "account-a"
            assert connection.status == "connected"
    assert grants == ["authorization_code"]
