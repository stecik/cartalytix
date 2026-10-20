"""Foodora password/OTP linking with synthetic sessions only."""

import json
from typing import Literal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_foodora_routes import PREFIX, create
from test_foodora_session import payload
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection
from cartalytix.providers.foodora.client import FoodoraError
from cartalytix.providers.foodora.login_sessions import LoginOutcome
from cartalytix.providers.foodora.session import FoodoraSession


class Logins:
    def __init__(self, *, otp: bool = False) -> None:
        self.otp = otp
        self.subject = "42"
        self.calls: list[str] = []

    def outcome(self, step: Literal["method", "otp", "completed"]) -> LoginOutcome:
        return LoginOutcome(
            step=step,
            flow_id="flow" if step != "completed" else None,
            methods=("SMS", "EMAIL") if step == "method" else (),
            method="EMAIL" if step == "otp" else None,
            session=FoodoraSession.from_payload(payload(self.subject))
            if step == "completed"
            else None,
            email="customer@example.com",
        )

    def start(self, owner: UUID, connection: UUID, email: str, password: str) -> LoginOutcome:
        assert email == "customer@example.com" and password == "private-password"
        self.calls.append("start")
        return self.outcome("method" if self.otp else "completed")

    def send(self, owner: UUID, connection: UUID, flow_id: str, method: str) -> LoginOutcome:
        assert flow_id == "flow" and method == "EMAIL"
        self.calls.append("send")
        return self.outcome("otp")

    def verify(self, owner: UUID, connection: UUID, flow_id: str, code: str) -> LoginOutcome:
        self.calls.append("verify")
        if code != "123456":
            raise FoodoraError("otp_rejected")
        return self.outcome("completed")

    def cancel(self, owner: UUID, connection: UUID, flow_id: str) -> None:
        self.calls.append("cancel")

    def cancel_connection(self, owner: UUID, connection: UUID) -> None:
        self.calls.append("disconnect")

    def close(self) -> None:
        pass


@pytest.fixture
def logins(authorized: TestClient) -> Logins:
    sessions = Logins()
    application(authorized).state.foodora_logins = sessions
    return sessions


def login(authorized: TestClient, identifier: str) -> Response:
    return authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"email": "customer@example.com", "password": "private-password"},
        headers=HEADERS,
    )


def test_password_login_encrypts_refresh_session(
    authorized: TestClient, engine: Engine, logins: Logins
) -> None:
    identifier = create(authorized)
    response = login(authorized, identifier)
    assert response.status_code == 200, response.text
    assert response.json().get("step") == "completed"
    assert response.json().get("connection", {}).get("account_label") == "customer@example.com"
    assert all(
        value not in response.text
        for value in ("private-refresh", "private-device", "private-password")
    )
    with Session(engine) as db:
        row = db.get(ProviderConnection, UUID(identifier))
        assert row is not None and row.account_subject == "42" and row.status == "connected"
        assert row.encrypted_refresh_token is not None
        saved = TokenCipher(application(authorized).state.settings).decrypt(
            row.encrypted_refresh_token
        )
        assert json.loads(saved) == payload()


def test_method_and_otp_flow_keeps_secrets_out_of_responses(
    authorized: TestClient, logins: Logins
) -> None:
    logins.otp = True
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    response = login(authorized, identifier)
    assert response.status_code == 200, response.text
    assert response.json().get("step") == "method"
    assert response.json().get("methods") == ["SMS", "EMAIL"]
    response = authorized.post(f"{url}/flow/send", json={"method": "EMAIL"}, headers=HEADERS)
    assert response.status_code == 200 and response.json().get("step") == "otp"
    rejected = authorized.post(f"{url}/flow/verify", json={"code": "000000"}, headers=HEADERS)
    assert (
        rejected.status_code == 422
        and rejected.json().get("error", {}).get("code") == "otp_rejected"
    )
    response = authorized.post(f"{url}/flow/verify", json={"code": "123456"}, headers=HEADERS)
    assert response.status_code == 200 and response.json().get("step") == "completed"
    assert logins.calls == ["start", "send", "verify", "verify"]


def test_reauth_mismatch_preserves_saved_session(
    authorized: TestClient, engine: Engine, logins: Logins
) -> None:
    identifier = create(authorized)
    assert login(authorized, identifier).status_code == 200
    with Session(engine) as db:
        row = db.get(ProviderConnection, UUID(identifier))
        assert row is not None
        previous = row.encrypted_refresh_token
    logins.subject = "other"
    response = login(authorized, identifier)
    assert (
        response.status_code == 409
        and response.json().get("error", {}).get("code") == "account_mismatch"
    )
    with Session(engine) as db:
        row = db.get(ProviderConnection, UUID(identifier))
        assert (
            row is not None
            and row.account_subject == "42"
            and row.encrypted_refresh_token == previous
        )


def test_cancel_and_disconnect_clear_pending_login(authorized: TestClient, logins: Logins) -> None:
    identifier = create(authorized)
    assert (
        authorized.delete(f"{PREFIX}/{identifier}/login/flow", headers=HEADERS).status_code == 204
    )
    assert authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS).status_code == 200
    assert logins.calls == ["cancel", "disconnect"]


def test_validation_and_ownership_precede_upstream(authorized: TestClient, logins: Logins) -> None:
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    response = authorized.post(
        url, json={"email": "bad", "password": "private-password"}, headers=HEADERS
    )
    assert response.status_code == 422 and "private-password" not in response.text
    assert (
        authorized.post(f"{url}/flow/send", json={"method": "OTHER"}, headers=HEADERS).status_code
        == 422
    )
    assert (
        authorized.post(f"{url}/flow/verify", json={"code": "secret"}, headers=HEADERS).status_code
        == 422
    )
    assert login(authorized, "00000000-0000-0000-0000-000000000000").status_code == 404
    assert logins.calls == []


def test_rate_limit_all_login_steps(authorized: TestClient, logins: Logins) -> None:
    identifier = create(authorized)

    class Denied:
        def check(self, ip: str, email: str) -> bool:
            return False

    application(authorized).state.limiter = Denied()
    url = f"{PREFIX}/{identifier}/login"
    assert login(authorized, identifier).status_code == 429
    assert (
        authorized.post(f"{url}/flow/send", json={"method": "EMAIL"}, headers=HEADERS).status_code
        == 429
    )
    assert (
        authorized.post(f"{url}/flow/verify", json={"code": "123456"}, headers=HEADERS).status_code
        == 429
    )
    assert logins.calls == []
