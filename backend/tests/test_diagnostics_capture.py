"""Technical diagnostics omit exception messages, credentials and request values."""

import io
import json
from collections.abc import Callable
from typing import Never, cast
from uuid import UUID
from zipfile import ZipFile

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session
from test_api import application, login
from test_api import client as client
from test_api import engine as engine
from test_lidl_login_sessions import FakeLogin
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_routes import create as create_lidl
from test_lidl_sync import Provider, detail
from test_lidl_sync import database as database
from test_wolt_adapter import LINK

from cartalytix.dependencies import Owner
from cartalytix.models import DiagnosticEvent, ProviderConnection, RawDocument, SyncRun, User
from cartalytix.providers.lidl.client import TokenData, exchange_refresh
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.providers.shoptet.client import ShoptetError
from cartalytix.providers.wolt.client import WoltClient, WoltError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync


def test_refresh_rejection_is_recorded_even_without_receipt_source(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "wolt"

    def factory(
        credentials: str,
        save: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> WoltClient:
        return WoltClient(credentials, save, http=http, delay=0, heartbeat=heartbeat)

    response = httpx.Response(401, json={"error": "invalid_grant", "token": "private-token"})
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as http:
        run = enqueue_sync(engine, connection, owner)
        run_sync(engine, run.id, config, client_factory=factory)
    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id == owner
        assert (event.component, event.code) == ("worker", "auth_required")
        assert event.context.get("provider") == "wolt"
        assert event.context.get("connection_id") == str(connection)
        assert event.context.get("run_id") == str(run.id)
        assert event.context.get("auth") == {
            "phase": "refresh",
            "status": 401,
            "reason": "invalid_grant",
        }
        assert "private" not in json.dumps(event.context)
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.error_code == "auth_required"


@pytest.mark.parametrize("provider", ["wolt", "nejkafe"])
def test_failed_link_login_records_owned_safe_diagnostics(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    prefix = f"/api/v1/providers/{provider}/connections"
    identifier = authorized.post(prefix, json={}, headers=HEADERS).json().get("id")
    assert identifier

    def rejected(*args: object, **kwargs: object) -> Never:
        kind = WoltError if provider == "wolt" else ShoptetError
        raise kind("credentials_rejected", diagnostic={"phase": "login", "status": 403})

    monkeypatch.setattr(
        "cartalytix.wolt_routes.exchange_magic_link"
        if provider == "wolt"
        else "cartalytix.shoptet_routes.sign_in",
        rejected,
    )
    payload = (
        {"link": LINK}
        if provider == "wolt"
        else {"email": "owner@example.com", "password": "private-password"}
    )
    response = authorized.post(f"{prefix}/{identifier}/login", json=payload, headers=HEADERS)
    assert response.status_code == 422
    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id is not None
        assert event.component == "api"
        assert event.context.get("provider") == provider
        assert event.context.get("connection_id") == identifier
        assert event.context.get("auth") == {"phase": "login", "status": 403}
        assert "private" not in json.dumps(event.context)


def test_unexpected_api_error_is_recorded_without_private_values(
    client: TestClient, engine: Engine
) -> None:
    app = application(client)

    @app.get("/api/v1/test-crash/{identifier}")
    def crash(identifier: str, owner: Owner) -> None:
        raise RuntimeError("password=private-password; token=private-token")

    with TestClient(app, raise_server_exceptions=False) as browser:
        login(browser)
        response = browser.get("/api/v1/test-crash/private-path?token=private-query")
    assert response.status_code == 500
    assert response.headers.get("content-type", "").startswith("application/json")
    assert response.json().get("error", {}).get("code") == "internal_error"

    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id is not None
        assert (event.component, event.code) == ("api", "internal_error")
        context = cast(dict[str, object], event.context)
        assert context.get("exception_type") == "RuntimeError"
        assert context.get("route") == "/api/v1/test-crash/{identifier}"
        assert context.get("frames")
        assert "private-" not in json.dumps(context)


def test_expected_api_server_error_is_recorded_with_fixed_code(
    client: TestClient, engine: Engine
) -> None:
    @application(client).get("/api/v1/test-unavailable")
    def unavailable(owner: Owner) -> None:
        raise HTTPException(503, "provider_unavailable")

    login(client)
    assert client.get("/api/v1/test-unavailable").status_code == 503

    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.code == "provider_unavailable"
        assert event.context.get("status") == 503


def test_parser_failure_records_exact_source_without_credentials(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {1: {"tickets": [{"id": "bad"}], "totalCount": 1}},
        {"bad": detail("bad", amount="999")},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id == owner
        assert (event.component, event.code) == ("import", "quality_error")
        assert event.context.get("run_id") == str(run.id)
        assert event.context.get("issues")
        document = db.get(RawDocument, UUID(event.context.get("raw_document_id", "")))
        assert document is not None and document.external_id == "bad"
        assert "rotated" not in json.dumps(event.context)


def test_worker_exception_records_locations_without_exception_message(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {1: {"tickets": [{"id": "bad"}], "totalCount": 1}},
        {"bad": RuntimeError("private-password private-token")},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id == owner
        assert (event.component, event.code) == ("worker", "sync_failed")
        assert event.context.get("exception_type") == "RuntimeError"
        assert event.context.get("frames")
        assert "private-" not in json.dumps(event.context)


def test_deleting_account_also_deletes_its_diagnostics(engine: Engine) -> None:
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    with Session(engine) as db, db.begin():
        user = User(email="delete-diagnostics@example.com", password_hash="unused")
        db.add(user)
        db.flush()
        owner_id = user.id
        db.add(DiagnosticEvent(owner_id=owner_id, component="api", code="internal_error"))
    with Session(engine) as db, db.begin():
        db.execute(delete(User).where(User.id == owner_id))
    with Session(engine) as db:
        assert (
            db.scalar(select(DiagnosticEvent).where(DiagnosticEvent.owner_id == owner_id)) is None
        )


@pytest.mark.parametrize(
    "status,body,reason",
    [
        (400, '{"error":"invalid_grant","refresh_token":"private-refresh"}', "invalid_grant"),
        (403, "private-provider-body", None),
        (200, '{"refresh_token":"private-refresh"}', "invalid_provider_response"),
    ],
)
def test_lidl_login_refresh_failure_exports_safe_provider_reason(
    authorized: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: str,
    reason: str | None,
) -> None:
    app = application(authorized)
    app.state.lidl_logins = LoginSessions(factory=lambda: FakeLogin(otp=False))
    identifier = create_lidl(authorized)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text=body))
    ) as http:

        def exchange(refresh_token: str) -> TokenData:
            return exchange_refresh(refresh_token, http=http)

        monkeypatch.setattr("cartalytix.lidl_routes.exchange_refresh", exchange)
        response = authorized.post(
            f"/api/v1/providers/lidl/connections/{identifier}/login",
            json={"email": "owner@example.com", "password": "private-password"},
            headers=HEADERS,
        )
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "auth_required"
    assert "private" not in response.text
    expected: dict[str, str | int] = {"phase": "refresh", "status": status}
    if reason is not None:
        expected["reason"] = reason
    with Session(engine) as db:
        event = db.scalar(select(DiagnosticEvent))
        assert event is not None and event.owner_id is not None
        assert (event.component, event.code) == ("api", "auth_required")
        assert event.context.get("provider") == "lidl"
        assert event.context.get("connection_id") == identifier
        assert event.context.get("auth") == expected
        assert "private" not in json.dumps(event.context)
    bundle = authorized.get("/api/v1/diagnostics/download?technical=true&imports=false")
    assert bundle.status_code == 200
    with ZipFile(io.BytesIO(bundle.content)) as archive:
        raw = archive.read("technical-errors.json")
        events = json.loads(raw).get("events", [])
        assert len(events) == 1
        assert events[0].get("context", {}).get("auth") == expected
        assert events[0].get("context", {}).get("provider") == "lidl"
        assert b"private" not in raw
