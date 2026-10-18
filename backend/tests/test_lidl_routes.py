"""Authenticated Lidl routes; synthetic credentials and isolated test database only."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import application, login
from test_api import client as client
from test_api import engine as engine

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection, SyncRun, User
from cartalytix.providers.lidl.client import LidlError, TokenData

PREFIX = "/api/v1/providers/lidl/connections"
HEADERS = {"Origin": "http://testserver"}


@pytest.fixture
def authorized(client: TestClient, tmp_path: Path) -> Iterator[TestClient]:
    key = tmp_path / "token.key"
    key.write_bytes(Fernet.generate_key())
    application(client).state.settings.token_key_file = str(key)
    login(client)
    yield client


def create(client: TestClient, **values: str | int | None) -> str:
    response = client.post(PREFIX, json={"name": "New Lidl", **values}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def exchange(refresh_token: str) -> TokenData:
    assert refresh_token == "synthetic-refresh"
    return {
        "access_token": "private-access",
        "refresh_token": "rotated-private",
        "expires_at": 9999999999,
        "subject": "account-a",
    }


def authorize(client: TestClient, identifier: str) -> Response:
    return client.post(
        f"{PREFIX}/{identifier}/authorize",
        json={"refresh_token": "synthetic-refresh"},
        headers=HEADERS,
    )


def test_lidl_creation_requires_session_and_allowed_origin(client: TestClient) -> None:
    assert client.post(PREFIX, json={"name": "Lidl"}, headers=HEADERS).status_code == 401
    login(client)
    assert client.post(PREFIX, json={"name": "Lidl"}).status_code == 403


def test_multiple_lidl_connections_and_nullable_schedule(authorized: TestClient) -> None:
    first = create(authorized)
    second = create(authorized, name="Family Lidl", sync_interval_minutes=120)
    assert first != second
    rows = authorized.get("/api/v1/connections").json().get("items")
    assert len(rows) == 3
    scheduled = next(row for row in rows if row.get("id") == second)
    assert scheduled.get("sync_interval_minutes") == 120
    assert scheduled.get("next_sync_at") is not None
    response = authorized.patch(
        f"{PREFIX}/{second}", json={"sync_interval_minutes": None}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json().get("next_sync_at") is None
    assert response.json().get("sync_interval_minutes") is None
    response = authorized.patch(f"{PREFIX}/{first}", json={"name": "Renamed"}, headers=HEADERS)
    assert response.json().get("name") == "Renamed"


@pytest.mark.parametrize("interval", [0, 59, 10081])
def test_schedule_bounds(authorized: TestClient, interval: int) -> None:
    response = authorized.post(
        PREFIX, json={"name": "Lidl", "sync_interval_minutes": interval}, headers=HEADERS
    )
    assert response.status_code == 422


def test_connection_cap_includes_existing_owned_connections(
    authorized: TestClient,
    engine: Engine,
) -> None:
    with Session(engine) as db, db.begin():
        owner_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        db.add_all(
            ProviderConnection(owner_id=owner_id, provider="lidl", name=f"Lidl {n}")
            for n in range(99)
        )
    response = authorized.post(PREFIX, json={"name": "Too many"}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "connection_limit_reached"


@pytest.mark.parametrize("operation", ["patch", "authorize", "sync", "runs"])
def test_other_owner_connections_are_hidden(
    authorized: TestClient,
    engine: Engine,
    operation: str,
) -> None:
    with Session(engine) as db:
        foreign_id = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    url = f"{PREFIX}/{foreign_id}"
    if operation == "patch":
        response = authorized.patch(url, json={"name": "Other"}, headers=HEADERS)
    elif operation == "runs":
        response = authorized.get(url + "/runs")
    else:
        body = {"refresh_token": "synthetic-refresh"} if operation == "authorize" else {}
        response = authorized.post(url + "/" + operation, json=body, headers=HEADERS)
    assert response.status_code == 404


def test_authorize_encrypts_rotated_refresh_without_exposing_tokens(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)
    identifier = create(authorized)
    response = authorize(authorized, identifier)
    assert response.status_code == 200
    assert response.json().get("status") == "connected"
    assert "private" not in response.text
    assert "token" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None
        assert connection.account_subject == "account-a"
        encrypted = connection.encrypted_refresh_token
        assert encrypted is not None
        assert b"rotated-private" not in encrypted
        assert (
            TokenCipher(application(authorized).state.settings).decrypt(encrypted)
            == "rotated-private"
        )


def test_other_identity_cannot_replace_existing_authorization(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)
    identifier = create(authorized)
    assert authorize(authorized, identifier).status_code == 200
    with Session(engine) as db:
        before = db.get(ProviderConnection, UUID(identifier))
        assert before is not None
        encrypted = before.encrypted_refresh_token

    def different(refresh_token: str) -> TokenData:
        return {**exchange(refresh_token), "subject": "account-b"}

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", different)
    response = authorize(authorized, identifier)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "account_mismatch"
    with Session(engine) as db:
        after = db.get(ProviderConnection, UUID(identifier))
        assert after is not None
        assert after.account_subject == "account-a"
        assert after.encrypted_refresh_token == encrypted
        assert after.status == "connected"


def test_missing_subject_rejected_without_persisting_new_credentials(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def opaque(refresh_token: str) -> TokenData:
        result = exchange(refresh_token)
        result.pop("subject", None)
        return result

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", opaque)
    identifier = create(authorized)
    response = authorize(authorized, identifier)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "account_identity_unavailable"
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None
        assert connection.encrypted_refresh_token is None


def test_unknown_old_identity_with_receipts_cannot_mix_accounts(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(refresh_token: str) -> TokenData:
        pytest.fail(
            "Existing anonymous purchase history must be protected before exchanging tokens"
        )

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", forbidden)
    with Session(engine) as db:
        identifier = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "My Lidl")
        )
    response = authorize(authorized, str(identifier))
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "account_identity_unavailable"


def test_authorization_failure_is_sanitized(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(refresh_token: str) -> TokenData:
        raise LidlError("auth_required")

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", failed)
    response = authorize(authorized, create(authorized))
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "auth_required"
    assert "synthetic-refresh" not in response.text


def test_unavailable_encryption_key_prevents_token_rotation(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(refresh_token: str) -> TokenData:
        pytest.fail("No exchange without available encryption key")

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", forbidden)
    application(authorized).state.settings.token_key_file = None
    response = authorize(authorized, create(authorized))
    assert response.status_code == 503
    assert response.json().get("error", {}).get("code") == "credentials_unavailable"


def test_broker_failure_keeps_idempotent_pending_job(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)

    def offline(identifier: str) -> None:
        raise OSError("private broker details")

    monkeypatch.setattr("cartalytix.connection_routes.sync_run.delay", offline)
    identifier = create(authorized)
    assert authorize(authorized, identifier).status_code == 200
    first = authorized.post(
        f"{PREFIX}/{identifier}/sync", json={"refresh_existing": True}, headers=HEADERS
    )
    assert first.status_code == 202
    assert first.json().get("status") == "pending"
    again = authorized.post(f"{PREFIX}/{identifier}/sync", json={}, headers=HEADERS)
    assert again.status_code == 202
    assert again.json().get("id") == first.json().get("id")
    with Session(engine) as db:
        runs = list(db.scalars(select(SyncRun).where(SyncRun.connection_id == UUID(identifier))))
        assert len(runs) == 1
        assert runs[0].refresh_existing is True
    history = authorized.get(f"{PREFIX}/{identifier}/runs").json().get("items")
    assert len(history) == 1
    assert history[0].get("id") == first.json().get("id")
    assert "private" not in first.text


def test_latest_run_is_batched_and_history_limit_enforced(
    authorized: TestClient,
    engine: Engine,
) -> None:
    identifiers = [create(authorized, name=f"Lidl {n}") for n in range(5)]
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        for identifier in identifiers:
            db.add_all(
                [
                    SyncRun(connection_id=UUID(identifier), status="completed", created_at=now),
                    SyncRun(
                        connection_id=UUID(identifier),
                        status="failed",
                        created_at=now + timedelta(seconds=1),
                        error_code="quality_error",
                    ),
                ]
            )
    queries: list[str] = []

    def track(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    try:
        response = authorized.get("/api/v1/connections")
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert response.status_code == 200
    assert len(queries) <= 4
    for row in response.json().get("items"):
        if row.get("id") in identifiers:
            assert row.get("latest_sync", {}).get("status") == "failed"
    history = authorized.get(f"{PREFIX}/{identifiers[0]}/runs?limit=1")
    assert len(history.json().get("items")) == 1
    assert history.json().get("items")[0].get("status") == "failed"
    assert history.json().get("total") == 2
    older = authorized.get(f"{PREFIX}/{identifiers[0]}/runs?limit=1&offset=1").json()
    assert [run.get("status") for run in older.get("items")] == ["completed"]
    assert (older.get("total"), older.get("limit"), older.get("offset")) == (2, 1, 1)
    assert authorized.get(f"{PREFIX}/{identifiers[0]}/runs?limit=51").status_code == 422
    assert authorized.get(f"{PREFIX}/{identifiers[0]}/runs?offset=-1").status_code == 422


def test_oauth_exchange_holds_no_orm_transaction(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[Session] = []

    def began(session: Session, transaction: object, connection: object) -> None:
        sessions.append(session)

    def checked(refresh_token: str) -> TokenData:
        assert sessions
        assert all(not session.in_transaction() for session in sessions)
        return exchange(refresh_token)

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", checked)
    identifier = create(authorized)
    event.listen(Session, "after_begin", began)
    try:
        assert authorize(authorized, identifier).status_code == 200
    finally:
        event.remove(Session, "after_begin", began)


def test_busy_connection_never_exchanges_token(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cartalytix.sync import connection_lock

    def forbidden(refresh_token: str) -> TokenData:
        pytest.fail("Concurrent authorization must never exchange another rotating token")

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", forbidden)
    identifier = create(authorized)
    with connection_lock(engine, UUID(identifier)) as acquired:
        assert acquired
        response = authorize(authorized, identifier)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "connection_busy"


def test_sync_requires_connected_account(authorized: TestClient) -> None:
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/sync", json={}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "auth_required"


def test_non_lidl_connection_is_hidden(authorized: TestClient, engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        owner_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        connection = ProviderConnection(owner_id=owner_id, provider="albert", name="Albert")
        db.add(connection)
        db.flush()
        identifier = connection.id
    response = authorized.patch(f"{PREFIX}/{identifier}", json={"name": "Other"}, headers=HEADERS)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "limiter_name,status,code",
    [
        ("denied", 429, "authorization_rate_limited"),
        ("unavailable", 503, "service_unavailable"),
    ],
)
def test_authorization_limiter_fails_closed(
    authorized: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    limiter_name: str,
    status: int,
    code: str,
) -> None:
    from test_api import DeniedLimiter, UnavailableLimiter

    def forbidden(refresh_token: str) -> TokenData:
        pytest.fail("Rate-limited authorization must never exchange tokens")

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", forbidden)
    identifier = create(authorized)
    application(authorized).state.limiter = (
        DeniedLimiter() if limiter_name == "denied" else UnavailableLimiter()
    )
    response = authorize(authorized, identifier)
    assert response.status_code == status
    assert response.json().get("error", {}).get("code") == code


def test_sync_dispatches_only_submitted_run_not_other_owner_backlog(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)
    sent: list[str] = []
    monkeypatch.setattr("cartalytix.connection_routes.sync_run.delay", sent.append)
    identifier = create(authorized)
    assert authorize(authorized, identifier).status_code == 200
    with Session(engine) as db:
        foreign_id = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
        foreign_run = SyncRun(connection_id=foreign_id)
        db.add(foreign_run)
        db.commit()
        foreign_run_id = foreign_run.id
    response = authorized.post(f"{PREFIX}/{identifier}/sync", json={}, headers=HEADERS)
    assert response.status_code == 202
    assert sent == [response.json().get("id")]
    with Session(engine) as db:
        untouched = db.get(SyncRun, foreign_run_id)
        assert untouched is not None and untouched.last_dispatched_at is None


def test_sync_registers_publish_after_response_instead_of_request_publish(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import Callable
    from typing import Any

    from fastapi import BackgroundTasks

    registered: list[tuple[Callable[..., Any], tuple[object, ...], dict[str, object]]] = []

    def capture(
        self: BackgroundTasks, function: Callable[..., Any], *args: object, **kwargs: object
    ) -> None:
        registered.append((function, args, kwargs))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Broker publication must happen after the HTTP response")

    monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)
    monkeypatch.setattr("cartalytix.connection_routes.publish_pending", forbidden)
    monkeypatch.setattr(BackgroundTasks, "add_task", capture)
    identifier = create(authorized)
    assert authorize(authorized, identifier).status_code == 200
    response = authorized.post(f"{PREFIX}/{identifier}/sync", json={}, headers=HEADERS)
    assert response.status_code == 202
    assert len(registered) == 1
    function, args, kwargs = registered[0]
    assert function is forbidden
    assert args[0] is engine
    assert kwargs.get("run_id") == UUID(response.json().get("id"))
    with Session(engine) as db:
        pending = db.get(SyncRun, kwargs.get("run_id"))
        assert (
            pending is not None
            and pending.status == "pending"
            and pending.last_dispatched_at is None
        )
