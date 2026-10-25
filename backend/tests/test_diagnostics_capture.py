"""Technical diagnostics omit exception messages, credentials and request values."""

import json
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session
from test_api import application, login
from test_api import client as client
from test_api import engine as engine
from test_lidl_sync import Provider, detail
from test_lidl_sync import database as database

from cartalytix.dependencies import Owner
from cartalytix.models import DiagnosticEvent, RawDocument, User
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync


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
