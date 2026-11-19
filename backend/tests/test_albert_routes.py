"""Albert linking: synthetic credentials, owner-scoped encrypted flows and strict SMS input."""

import json
import time
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection

PREFIX = "/api/v1/providers/albert/connections"
PHONE = "+420000000000"


def create(client: TestClient) -> str:
    settings = application(client).state.settings
    for key in (
        "albert_client_username",
        "albert_client_password",
        "albert_verification_salt",
        "albert_login_salt",
    ):
        setattr(settings, key, SecretStr("synthetic-config"))
    response = client.post(PREFIX, json={"name": "Albert"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def prepare(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = application(client).state.settings
    for key in (
        "albert_client_username",
        "albert_client_password",
        "albert_verification_salt",
        "albert_login_salt",
    ):
        setattr(settings, key, SecretStr("synthetic-config"))

    def start(phone: str, *, config: object) -> SimpleNamespace:
        assert phone == PHONE
        return SimpleNamespace(
            phone=phone,
            installation_id="synthetic-device",
            login_id="private-login",
            expires_at=time.time() + 600,
            clock_offset=0,
        )

    def confirm(
        phone: str,
        installation_id: str,
        login_id: str,
        code: str,
        *,
        config: object,
        clock_offset: float = 0,
    ) -> SimpleNamespace:
        assert (phone, installation_id, login_id, code) == (
            PHONE,
            "synthetic-device",
            "private-login",
            "123456",
        )
        return SimpleNamespace(
            credential=json.dumps({"refresh_token": "private-refresh", "subject": "customer-a"}),
            subject="customer-a",
            label=phone,
        )

    monkeypatch.setattr("cartalytix.albert_routes.start_verification", start)
    monkeypatch.setattr("cartalytix.albert_routes.confirm_verification", confirm)


def start_flow(client: TestClient, identifier: str) -> str:
    response = client.post(f"{PREFIX}/{identifier}/login", json={"phone": PHONE}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return str(response.json().get("flow_id"))


def test_sms_login_encrypts_flow_and_stores_only_refresh(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    assert PHONE not in flow and "private-login" not in flow
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": flow, "code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "private-refresh" not in response.text and "private-login" not in response.text
    with Session(engine) as db:
        row = db.get(ProviderConnection, UUID(identifier))
        assert row is not None and row.encrypted_refresh_token is not None
        assert row.account_subject == "customer-a"
        stored = TokenCipher(application(authorized).state.settings).decrypt(
            row.encrypted_refresh_token
        )
        assert json.loads(stored) == {"refresh_token": "private-refresh", "subject": "customer-a"}


def test_sms_flow_cannot_verify_different_connection(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = create(authorized), create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, first)
    response = authorized.post(
        f"{PREFIX}/{second}/login/verify", json={"flow_id": flow, "code": "123456"}, headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "login_expired"


@pytest.mark.parametrize(
    "phone", ["777123456", "+421777123456", "+420１２３４５６７８９", "+42012345678"]
)
def test_sms_rejects_invalid_phone_before_provider_call(authorized: TestClient, phone: str) -> None:
    identifier = create(authorized)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login", json={"phone": phone}, headers=HEADERS
    )
    assert response.status_code == 422


@pytest.mark.parametrize("code", ["12345", "1234567", "１２３４５６", "ab1234"])
def test_sms_rejects_malformed_code(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify", json={"flow_id": flow, "code": code}, headers=HEADERS
    )
    assert response.status_code == 422


def test_expired_sms_flow_rejected_without_provider_call(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    cipher = TokenCipher(application(authorized).state.settings)
    content: dict[str, Any] = json.loads(cipher.decrypt(flow.encode()))
    content["expires_at"] = 0
    expired = cipher.encrypt(json.dumps(content)).decode()
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": expired, "code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 409


def test_albert_is_available_in_provider_catalog(authorized: TestClient) -> None:
    providers = authorized.get("/api/v1/providers").json().get("items")
    albert = next(row for row in providers if row.get("code") == "albert")
    assert albert.get("status") == "available"


def test_missing_albert_configuration_does_not_start_login(authorized: TestClient) -> None:
    identifier = create(authorized)
    application(authorized).state.settings.albert_client_password = None
    response = authorized.post(
        f"{PREFIX}/{identifier}/login", json={"phone": PHONE}, headers=HEADERS
    )
    assert response.status_code == 503
    assert response.json().get("error", {}).get("code") == "credentials_unavailable"


def test_reconnect_cannot_replace_bound_albert_account(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, UUID(identifier))
        assert row is not None
        row.account_subject = "original-customer"
        row.encrypted_refresh_token = b"original-encrypted"
    flow = start_flow(authorized, identifier)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": flow, "code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "account_mismatch"
    with Session(engine) as db:
        row = db.get(ProviderConnection, UUID(identifier))
        assert row is not None and row.encrypted_refresh_token == b"original-encrypted"
        assert row.account_subject == "original-customer"


def test_sms_start_and_verify_enforce_rate_limit(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    monkeypatch.setattr(application(authorized).state.limiter, "check", lambda address, key: False)
    for path, payload in (
        ("login", {"phone": PHONE}),
        ("login/verify", {"flow_id": flow, "code": "123456"}),
    ):
        response = authorized.post(f"{PREFIX}/{identifier}/{path}", json=payload, headers=HEADERS)
        assert response.status_code == 429


def test_disconnected_account_cannot_be_reconnected_by_old_sms_flow(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    response = authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS)
    assert response.status_code == 200
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": flow, "code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "login_expired"


def test_completed_sms_flow_cannot_replace_credentials_again(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    flow = start_flow(authorized, identifier)
    body = {"flow_id": flow, "code": "123456"}
    assert (
        authorized.post(
            f"{PREFIX}/{identifier}/login/verify", json=body, headers=HEADERS
        ).status_code
        == 200
    )
    assert (
        authorized.post(
            f"{PREFIX}/{identifier}/login/verify", json=body, headers=HEADERS
        ).status_code
        == 409
    )


def test_catalog_reports_albert_client_configuration_readiness(authorized: TestClient) -> None:
    providers = authorized.get("/api/v1/providers").json().get("items")
    albert = next(row for row in providers if row.get("code") == "albert")
    assert albert.get("can_connect") is False


def test_unconfigured_albert_account_cannot_be_created(authorized: TestClient) -> None:
    response = authorized.post(PREFIX, json={"name": "Albert"}, headers=HEADERS)
    assert response.status_code == 503
    assert response.json().get("error", {}).get("code") == "credentials_unavailable"


def test_new_sms_request_invalidates_previous_flow(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    old = start_flow(authorized, identifier)
    new = start_flow(authorized, identifier)
    old_result = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": old, "code": "123456"},
        headers=HEADERS,
    )
    assert old_result.status_code == 409
    new_result = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": new, "code": "123456"},
        headers=HEADERS,
    )
    assert new_result.status_code == 200


def test_repeat_disconnect_also_invalidates_pending_sms(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = create(authorized)
    prepare(authorized, monkeypatch)
    assert authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS).status_code == 200
    flow = start_flow(authorized, identifier)
    assert authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS).status_code == 200
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": flow, "code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 409


def test_upcoming_providers_are_not_connectable(authorized: TestClient) -> None:
    rows = authorized.get("/api/v1/providers").json().get("items")
    upcoming = [row for row in rows if row.get("status") == "not_implemented"]
    assert upcoming and all(row.get("can_connect") is False for row in upcoming)
