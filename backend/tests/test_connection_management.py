"""Connection lifecycle must preserve ownership and serialize with provider jobs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_lidl_login_sessions import FakeLogin
from test_lidl_routes import HEADERS, PREFIX
from test_lidl_routes import authorized as authorized

from cartalytix.models import (
    Discount,
    ProviderConnection,
    RawDocument,
    Receipt,
    ReceiptItem,
    SyncRun,
)
from cartalytix.providers.lidl.login_protocol import Status
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.sync import connection_lock, run_sync


def seed(engine: Engine) -> UUID:
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    with Session(engine) as db, db.begin():
        connection = db.scalar(
            select(ProviderConnection).where(ProviderConnection.name == "My Lidl")
        )
        assert connection is not None
        connection.encrypted_refresh_token = b"synthetic-encrypted"
        connection.account_subject = "account-a"
        connection.status = "connected"
        connection.sync_interval_minutes = 60
        connection.next_sync_at = datetime.now(UTC) + timedelta(hours=1)
        raw = RawDocument(
            connection_id=connection.id,
            external_id="1",
            payload=b"{}",
            checksum="a",
            parser_version="test",
        )
        db.add(raw)
        db.flush()
        receipt = db.scalar(
            select(Receipt).where(
                Receipt.connection_id == connection.id, Receipt.external_id == "1"
            )
        )
        assert receipt is not None
        receipt.raw_document_id = raw.id
        item = ReceiptItem(
            receipt_id=receipt.id,
            position=1,
            name="Milk",
            quantity=Decimal(1),
            total_amount=Decimal(10),
        )
        db.add(item)
        db.flush()
        db.add(Discount(receipt_id=receipt.id, item_id=item.id, name="Offer", amount=Decimal(-1)))
        db.add(SyncRun(connection_id=connection.id, status="pending"))
        return connection.id


def test_disconnect_preserves_purchases_identity_and_history(
    authorized: TestClient, engine: Engine
) -> None:
    identifier = seed(engine)
    response = authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("receipt_count") == 2
    assert response.json().get("status") == "disconnected"
    with Session(engine) as db:
        connection = db.get(ProviderConnection, identifier)
        assert connection is not None and connection.encrypted_refresh_token is None
        assert connection.account_subject == "account-a"
        assert connection.sync_interval_minutes is None and connection.next_sync_at is None
        assert db.scalar(select(func.count()).select_from(Receipt)) == 3
        run = db.scalar(select(SyncRun).where(SyncRun.connection_id == identifier))
        assert (
            run is not None
            and run.status == "failed"
            and run.error_code == "connection_disconnected"
        )
    assert (
        authorized.post(f"{PREFIX}/{identifier}/sync", json={}, headers=HEADERS).status_code == 409
    )


@pytest.mark.parametrize("pause", [None, False])
def test_clear_data_removes_children_preserves_authorization_history_and_other_owner(
    authorized: TestClient, engine: Engine, pause: bool | None
) -> None:
    identifier = seed(engine)
    response = authorized.post(
        f"{PREFIX}/{identifier}/clear-data",
        json={} if pause is None else {"pause_sync": pause},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("receipt_count") == 0
    with Session(engine) as db:
        connection = db.get(ProviderConnection, identifier)
        assert (
            connection is not None and connection.encrypted_refresh_token == b"synthetic-encrypted"
        )
        assert connection.account_subject == "account-a" and connection.status == "connected"
        assert connection.sync_interval_minutes == (None if pause is None else 60)
        assert (connection.next_sync_at is None) == (pause is None)
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1
        for model in (Discount, ReceiptItem, RawDocument):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        run = db.scalar(select(SyncRun).where(SyncRun.connection_id == identifier))
        assert run is not None and run.status == "failed" and run.error_code == "data_cleared"
        stopped_run = run.id
    # A previously dispatched task must not import again after the deletion commits.
    run_sync(engine, stopped_run, application(authorized).state.settings)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_remove_connection_deletes_all_owned_rows(authorized: TestClient, engine: Engine) -> None:
    identifier = seed(engine)
    with Session(engine) as db:
        stopped_run = db.scalar(select(SyncRun.id).where(SyncRun.connection_id == identifier))
        assert stopped_run is not None
    response = authorized.delete(f"{PREFIX}/{identifier}", headers=HEADERS)
    assert response.status_code == 204, response.text
    with Session(engine) as db:
        assert db.get(ProviderConnection, identifier) is None
        assert db.scalar(select(func.count()).select_from(ProviderConnection)) == 1
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1
        for model in (Discount, ReceiptItem, RawDocument, SyncRun):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    run_sync(engine, stopped_run, application(authorized).state.settings)


@pytest.mark.parametrize("operation", ["disconnect", "clear-data", "remove"])
def test_management_checks_origin_owner_and_busy_worker(
    authorized: TestClient, engine: Engine, operation: str
) -> None:
    identifier = seed(engine)
    with Session(engine) as db:
        foreign = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )

    def submit(value: UUID | None, headers: dict[str, str]) -> Response:
        url = f"{PREFIX}/{value}"
        return (
            authorized.delete(url, headers=headers)
            if operation == "remove"
            else authorized.post(url + "/" + operation, json={}, headers=headers)
        )

    assert submit(identifier, {}).status_code == 403
    assert submit(foreign, HEADERS).status_code == 404
    with connection_lock(engine, identifier) as acquired:
        assert acquired
        response = submit(identifier, HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "connection_busy"
    with Session(engine) as db:
        assert db.get(ProviderConnection, identifier) is not None
        assert db.scalar(select(func.count()).select_from(Receipt)) == 3


def test_disconnect_invalidates_pending_mfa(authorized: TestClient, engine: Engine) -> None:
    identifier = seed(engine)
    fake = FakeLogin()
    sessions = LoginSessions(factory=lambda: fake)
    application(authorized).state.lidl_logins = sessions
    flow = (
        authorized.post(
            f"{PREFIX}/{identifier}/login",
            json={"email": "owner@example.com", "password": "private-password"},
            headers=HEADERS,
        )
        .json()
        .get("flow_id")
    )
    assert flow
    assert authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS).status_code == 200
    assert fake.closed
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/{flow}/verify", json={"code": "123456"}, headers=HEADERS
    )
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "login_expired"


def test_login_cannot_start_while_connection_is_locked(
    authorized: TestClient, engine: Engine
) -> None:
    identifier = seed(engine)
    fake = FakeLogin()
    application(authorized).state.lidl_logins = LoginSessions(factory=lambda: fake)
    with connection_lock(engine, identifier) as acquired:
        assert acquired
        response = authorized.post(
            f"{PREFIX}/{identifier}/login",
            json={"email": "owner@example.com", "password": "private-password"},
            headers=HEADERS,
        )
    assert response.status_code == 409
    assert fake.posts == 0


def test_connection_counts_are_owner_scoped(authorized: TestClient) -> None:
    rows = authorized.get("/api/v1/connections").json().get("items")
    assert len(rows) == 1 and rows[0].get("receipt_count") == 2


def test_disconnect_cannot_race_login_handoff(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = seed(engine)

    class InterleavedLogin(FakeLogin):
        def credentials(self, email: str, password: str) -> Status:
            response = authorized.post(f"{PREFIX}/{identifier}/disconnect", headers=HEADERS)
            assert response.status_code == 409
            return super().credentials(email, password)

    fake = InterleavedLogin(otp=False)
    application(authorized).state.lidl_logins = LoginSessions(factory=lambda: fake)
    monkeypatch.setattr(
        "cartalytix.lidl_routes.exchange_refresh",
        lambda _token: {"subject": "account-a", "refresh_token": "rotated-private"},
    )
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"email": "owner@example.com", "password": "private-password"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("connection", {}).get("status") == "connected"
