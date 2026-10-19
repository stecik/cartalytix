"""Kaufland linking routes and sync; synthetic e-mail, tokens and receipts only."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_kaufland_adapter import receipt
from test_lidl_login_sessions import FakeLogin
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, SyncRun
from cartalytix.providers.kaufland.client import KauflandAccount
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/kaufland/connections"
CREDENTIALS = {"email": "owner@example.com", "password": "private-password"}


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Kaufland"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def test_code_login_stores_only_encrypted_rotated_refresh(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(authorized)
    login = FakeLogin()
    app.state.kaufland_logins = LoginSessions(factory=lambda: login)
    monkeypatch.setattr(
        "cartalytix.kaufland_routes.exchange_refresh",
        lambda token: (
            KauflandAccount("private-rotated", "sub-1", None)
            if token == "private-refresh"
            else None
        ),
    )
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/login", json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body.get("step"), body.get("method")) == ("otp", "email")
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/{body.get('flow_id')}/verify",
        json={"code": "123456"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("connection", {}).get("status") == "connected"
    assert "private-" not in response.text and login.closed
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert connection.account_subject == "sub-1"
        assert connection.account_label == "owner@example.com"
        cipher = TokenCipher(app.state.settings)
        assert cipher.decrypt(connection.encrypted_refresh_token) == "private-rotated"


def test_login_rejects_foreign_account_on_relink(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = application(authorized)
    app.state.kaufland_logins = LoginSessions(factory=lambda: FakeLogin(otp=False))
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    monkeypatch.setattr(
        "cartalytix.kaufland_routes.exchange_refresh",
        lambda token: KauflandAccount("r", "sub-1", None),
    )
    assert authorized.post(url, json=CREDENTIALS, headers=HEADERS).status_code == 200
    monkeypatch.setattr(
        "cartalytix.kaufland_routes.exchange_refresh",
        lambda token: KauflandAccount("r", "sub-2", None),
    )
    response = authorized.post(url, json=CREDENTIALS, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "account_mismatch"


class Transactions:
    def __init__(self, receipts: list[dict[str, Any]]) -> None:
        self.receipts = {item["id"]: item for item in receipts}

    def factory(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> Transactions:
        return self

    def page(self, number: int) -> dict[str, Any]:
        return {"transactions": list(self.receipts.values()) if number == 1 else []}

    def detail(self, identifier: str) -> dict[str, Any]:
        return self.receipts[identifier]

    def close(self) -> None:
        pass


def test_sync_imports_receipts_with_card_discounts(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "kaufland"
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=Transactions([receipt()]).factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert stored.receipts_imported == 1
        imported = db.scalar(select(Receipt))
        assert imported is not None and imported.merchant == "Kaufland"
        assert imported.total_amount == Decimal("26.34")
        assert sorted(db.scalars(select(Discount.amount)).all()) == [
            Decimal("10.90"),
            Decimal("17.56"),
        ]
