"""Diagnostic exports use synthetic data in the isolated API test database."""

import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import (
    DiagnosticEvent,
    ProviderConnection,
    RawDocument,
    Receipt,
    SyncRun,
    User,
)


def test_auth_diagnostics_survive_export_with_only_allowlisted_fields(
    client: TestClient, engine: Engine
) -> None:
    login(client)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        run_id = str(uuid4())
        connection_id = str(own.id)
        db.add(
            DiagnosticEvent(
                owner_id=own.owner_id,
                component="worker",
                code="auth_required",
                context={
                    "provider": "wolt",
                    "connection_id": connection_id,
                    "run_id": run_id,
                    "auth": {
                        "phase": "refresh",
                        "status": 401,
                        "reason": "invalid_grant",
                        "body": "private-token",
                        "csrf_token": "private-csrf",
                    },
                },
            )
        )
    response = client.get("/api/v1/diagnostics/download?imports=false&technical=true")
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        technical = json.loads(archive.read("technical-errors.json"))
        context = technical.get("events", [])[0].get("context", {})
        assert context.get("auth") == {"phase": "refresh", "status": 401, "reason": "invalid_grant"}
        assert context.get("provider") == "wolt"
        assert context.get("connection_id") == connection_id
        assert context.get("run_id") == run_id
        assert b"private-" not in archive.read("technical-errors.json")


def test_diagnostics_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/diagnostics/download").status_code == 401


def test_diagnostics_default_zip_and_selection_validation(client: TestClient) -> None:
    login(client)
    response = client.get("/api/v1/diagnostics/download?hours=24")
    assert response.status_code == 200
    assert response.headers.get("content-type") == "application/zip"
    assert response.headers.get("cache-control") == "no-store"
    assert "attachment; filename=cartalytix-logs-" in response.headers.get(
        "content-disposition", ""
    )
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "imports.json"}
        assert json.loads(archive.read("manifest.json")).get("period", {}).get("hours") == 24
    assert client.get("/api/v1/diagnostics/download?hours=2").status_code == 422
    response = client.get("/api/v1/diagnostics/download?imports=false&technical=false")
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "diagnostics_empty_selection"


def test_owner_period_redaction_and_failed_sources(client: TestClient, engine: Engine) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        connections = list(db.scalars(select(ProviderConnection)))
        own = next(row for row in connections if row.name == "My Lidl")
        foreign = next(row for row in connections if row.name == "Private Lidl")
        own.account_label = "synthetic-private-label"
        own.encrypted_refresh_token = b"synthetic-private-token"
        run = SyncRun(
            connection_id=own.id,
            status="failed",
            error_code="invalid_receipt",
            created_at=now - timedelta(minutes=5),
            finished_at=now,
        )
        db.add(run)
        db.flush()
        raw = RawDocument(
            connection_id=own.id,
            external_id="../../private",
            payload=b"%PDF-test",
            checksum="synthetic",
            parser_version="old-parser",
            received_at=now,
        )
        foreign_raw = RawDocument(
            connection_id=foreign.id,
            external_id="foreign",
            payload=b"%PDF-foreign",
            checksum="foreign",
            parser_version="old",
        )
        db.add_all([raw, foreign_raw])
        db.flush()
        raw_id = str(raw.id)
        db.add_all(
            [
                DiagnosticEvent(
                    owner_id=own.owner_id,
                    component="import",
                    code="invalid_receipt",
                    context={
                        "raw_document_id": str(foreign_raw.id),
                        "run_id": str(uuid4()),
                        "provider": "lidl",
                        "issues": ["invalid_total", {"token": "secret"}],
                        "message": "synthetic-private-message",
                    },
                ),
                DiagnosticEvent(
                    owner_id=own.owner_id,
                    component="api",
                    code="internal_error",
                    context={
                        "exception_type": "ValueError",
                        "headers": "private-header",
                        "route": "/private/token",
                        "frames": [
                            {
                                "filename": "routes.py",
                                "function": "download",
                                "line": 12,
                                "locals": "private-locals",
                            },
                            {"filename": "C:/private/token.py", "function": "bad", "line": 1},
                        ],
                    },
                ),
                DiagnosticEvent(
                    owner_id=foreign.owner_id, component="worker", code="foreign_error", context={}
                ),
                DiagnosticEvent(owner_id=None, component="api", code="unowned_error", context={}),
                DiagnosticEvent(
                    owner_id=own.owner_id,
                    component="api",
                    code="old_error",
                    context={},
                    created_at=now - timedelta(days=10),
                ),
            ]
        )
    response = client.get("/api/v1/diagnostics/download?technical=true&hours=1")
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        source = f"sources/lidl/{raw_id}.pdf"
        assert archive.read(source) == b"%PDF-test"
        assert len([name for name in archive.namelist() if name.startswith("sources/")]) == 1
        technical = json.loads(archive.read("technical-errors.json")).get("events")
        assert [row.get("code") for row in technical] == ["internal_error"]
        assert technical[0].get("context", {}).get("frames") == [
            {"filename": "routes.py", "function": "download", "line": 12}
        ]
        exported = b"".join(archive.read(name) for name in archive.namelist())
        for forbidden in (
            b"synthetic-private",
            b"private-header",
            b"private-locals",
            b"foreign_error",
            b"old_error",
            b"unowned_error",
            b"%PDF-foreign",
            b"owner@example.com",
            b"../",
        ):
            assert forbidden not in exported
    response = client.get("/api/v1/diagnostics/download?imports=false&technical=true")
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "technical-errors.json"}


def test_sources_skip_oversize_unknown_and_bound_count(client: TestClient, engine: Engine) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        db.add(
            SyncRun(
                connection_id=own.id,
                status="failed",
                created_at=now - timedelta(minutes=1),
                finished_at=now + timedelta(minutes=1),
            )
        )
        for index in range(53):
            payload = b'{"receipt": true}'
            if index == 0:
                payload = b"%PDF" + b"x" * (2 * 1024 * 1024)
            if index == 1:
                payload = b"unknown format"
            db.add(
                RawDocument(
                    connection_id=own.id,
                    external_id=str(uuid4()),
                    payload=payload,
                    checksum=str(index),
                    parser_version="old",
                    received_at=now,
                )
            )
    response = client.get("/api/v1/diagnostics/download")
    with ZipFile(io.BytesIO(response.content)) as archive:
        sources = [name for name in archive.namelist() if name.startswith("sources/")]
        assert 49 <= len(sources) <= 50
        assert json.loads(archive.read("manifest.json")).get("sources", {}).get("omitted") >= 3


def test_admin_cannot_export_receipts(client: TestClient, engine: Engine) -> None:
    login(client)
    with Session(engine) as db, db.begin():
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        assert owner is not None
        owner.is_admin = True
    assert client.get("/api/v1/diagnostics/download").status_code == 403


def test_invalid_deep_json_is_omitted(client: TestClient, engine: Engine) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        raw = RawDocument(
            connection_id=own.id,
            external_id="deep",
            payload=b"[" * 2000,
            checksum="deep",
            parser_version="old",
            received_at=now,
        )
        db.add(raw)
        db.flush()
        db.add(
            DiagnosticEvent(
                owner_id=own.owner_id,
                component="import",
                code="invalid_receipt",
                context={"raw_document_id": str(raw.id), "provider": []},
            )
        )
    response = client.get("/api/v1/diagnostics/download")
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("manifest.json")).get("sources", {}).get("omitted") == 1


def test_event_and_total_source_byte_caps(client: TestClient, engine: Engine) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        db.add(
            SyncRun(
                connection_id=own.id,
                status="failed",
                created_at=now - timedelta(minutes=1),
                finished_at=now + timedelta(minutes=1),
            )
        )
        for index in range(11):
            db.add(
                RawDocument(
                    connection_id=own.id,
                    external_id=str(index),
                    checksum=str(index),
                    payload=b"%PDF" + b"x" * (2 * 1024 * 1024 - 4),
                    parser_version="old",
                    received_at=now,
                )
            )
        db.add_all(
            DiagnosticEvent(
                owner_id=own.owner_id, component="worker", code="internal_error", context={}
            )
            for _ in range(501)
        )
    response = client.get("/api/v1/diagnostics/download?technical=true")
    with ZipFile(io.BytesIO(response.content)) as archive:
        sources = [name for name in archive.namelist() if name.startswith("sources/")]
        assert len(sources) == 10
        assert sum(archive.getinfo(name).file_size for name in sources) == 20 * 1024 * 1024
        assert len(json.loads(archive.read("technical-errors.json")).get("events")) == 500
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest.get("omissions", {}).get("technical_events_truncated") is True


def test_failed_run_does_not_attach_provider_list_responses(
    client: TestClient,
    engine: Engine,
) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        db.add(
            SyncRun(
                connection_id=own.id,
                status="failed",
                created_at=now - timedelta(minutes=1),
                finished_at=now + timedelta(minutes=1),
            )
        )
        db.add(
            RawDocument(
                connection_id=own.id,
                external_id="list:0",
                payload=b'{"tickets": []}',
                checksum="list",
                parser_version="old",
                received_at=now,
            )
        )
    response = client.get("/api/v1/diagnostics/download")
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert not any(name.startswith("sources/") for name in archive.namelist())


def test_json_source_redacts_nested_credentials(client: TestClient, engine: Engine) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        raw = RawDocument(
            connection_id=own.id,
            external_id="receipt",
            checksum="redaction",
            parser_version="old",
            received_at=now,
            payload=json.dumps(
                {
                    "id": "receipt",
                    "access_token": "PRIVATE",
                    "nested": {"password": "PRIVATE", "Set-Cookie": "PRIVATE"},
                    "items": [{"CLIENT_SECRET": "PRIVATE", "name": "Bread"}],
                }
            ).encode(),
        )
        db.add(raw)
        db.flush()
        db.add(
            DiagnosticEvent(
                owner_id=own.owner_id,
                component="import",
                code="invalid_receipt",
                context={"raw_document_id": str(raw.id)},
            )
        )
        identifier = str(raw.id)
    response = client.get("/api/v1/diagnostics/download")
    with ZipFile(io.BytesIO(response.content)) as archive:
        payload = archive.read(f"sources/lidl/{identifier}.json")
        assert b"PRIVATE" not in payload
        parsed = json.loads(payload)
        assert parsed.get("id") == "receipt"
        assert parsed.get("items")[0].get("name") == "Bread"
        sources = json.loads(archive.read("manifest.json")).get("sources", {})
        assert sources.get("documents")[0].get("redacted") is True


def test_recent_event_attaches_deduplicated_old_source(client: TestClient, engine: Engine) -> None:
    login(client)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        raw = RawDocument(
            connection_id=own.id,
            external_id="old-receipt",
            checksum="deduplicated",
            parser_version="old",
            received_at=datetime.now(UTC) - timedelta(days=8),
            payload=b"%PDF-deduplicated",
        )
        db.add(raw)
        db.flush()
        identifier = str(raw.id)
        db.add(
            DiagnosticEvent(
                owner_id=own.owner_id,
                component="import",
                code="invalid_receipt",
                context={"raw_document_id": identifier},
            )
        )
    response = client.get("/api/v1/diagnostics/download?hours=1")
    with ZipFile(io.BytesIO(response.content)) as archive:
        source_names = [name for name in archive.namelist() if name.startswith("sources/")]
        assert source_names == [f"sources/lidl/{identifier}.pdf"]
        assert archive.read(source_names[0]) == b"%PDF-deduplicated"


def test_exact_failed_source_excludes_successful_receipts_from_same_run(
    client: TestClient,
    engine: Engine,
) -> None:
    login(client)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        run = SyncRun(
            connection_id=own.id,
            status="failed",
            created_at=now - timedelta(minutes=5),
            finished_at=now,
        )
        db.add(run)
        db.flush()
        bad = RawDocument(
            connection_id=own.id,
            external_id="bad",
            checksum="bad",
            parser_version="old",
            received_at=now - timedelta(days=8),
            payload=b"%PDF-bad",
        )
        db.add(bad)
        db.flush()
        bad_id = str(bad.id)
        db.add(
            DiagnosticEvent(
                owner_id=own.owner_id,
                component="import",
                code="invalid_receipt",
                context={"run_id": str(run.id), "raw_document_id": bad_id},
            )
        )
        for index in range(51):
            raw = RawDocument(
                connection_id=own.id,
                external_id=f"success-{index}",
                checksum=str(index),
                parser_version="old",
                received_at=now,
                payload=b"%PDF-success",
            )
            db.add(raw)
            db.flush()
            db.add(
                Receipt(
                    connection_id=own.id,
                    raw_document_id=raw.id,
                    external_id=f"success-{index}",
                    purchased_at=now,
                    merchant="Lidl",
                    currency="CZK",
                    total_amount=1,
                )
            )
    response = client.get("/api/v1/diagnostics/download?hours=1")
    with ZipFile(io.BytesIO(response.content)) as archive:
        sources = [name for name in archive.namelist() if name.startswith("sources/")]
        assert sources == [f"sources/lidl/{bad_id}.pdf"]
