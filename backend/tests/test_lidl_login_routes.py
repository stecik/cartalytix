from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_lidl_login_sessions import FakeLogin
from test_lidl_routes import HEADERS, PREFIX, create
from test_lidl_routes import authorized as authorized

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection
from cartalytix.providers.lidl.login_sessions import LoginSessions


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
        lambda _token: {
            "access_token": "private-access",
            "refresh_token": "private-rotated",
            "subject": "account-a",
            "expires_at": 9999999999,
        },
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
            TokenCipher(app.state.settings).decrypt(connection.encrypted_refresh_token)
            == "private-rotated"
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
        lambda _token: {
            "refresh_token": "private-rotated",
            "subject": "account-a",
        },
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
