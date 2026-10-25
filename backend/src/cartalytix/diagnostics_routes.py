"""Bounded, owner-scoped diagnostic bundles with explicitly selected fields."""

import io
import json
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from cartalytix.dependencies import Db, Owner
from cartalytix.models import DiagnosticEvent, ProviderConnection, RawDocument, Receipt, SyncRun
from cartalytix.sync import PROVIDERS

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])
MAX_ROWS = 500
MAX_SOURCES = 50
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_SECRET_KEYS = frozenset(
    {
        "password",
        "currentpassword",
        "newpassword",
        "accesstoken",
        "refreshtoken",
        "devicetoken",
        "token",
        "authorization",
        "cookie",
        "setcookie",
        "clientsecret",
        "apikey",
        "credentials",
        "headers",
        "session",
    }
)


class Period(StrEnum):
    HOUR = "1"
    DAY = "24"
    WEEK = "168"


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()


def _uuid(value: object) -> str | None:
    try:
        return str(UUID(str(value)))
    except ValueError, TypeError, AttributeError:
        return None


def _code(value: object) -> str | None:
    return value if isinstance(value, str) and _CODE.fullmatch(value) else None


def _redact_source(value: object, depth: int = 0) -> tuple[object, bool]:
    if depth > 40:
        raise ValueError
    changed = False
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = key.lower().replace("_", "").replace("-", "")
            if normalized in _SECRET_KEYS:
                result[key] = "[redacted]"
                changed = True
            else:
                result[key], item_changed = _redact_source(item, depth + 1)
                changed = changed or item_changed
        return result, changed
    if isinstance(value, list):
        items: list[Any] = []
        for item in value:
            redacted, item_changed = _redact_source(item, depth + 1)
            items.append(redacted)
            changed = changed or item_changed
        return items, changed
    return value, False


def _event(event: DiagnosticEvent) -> dict[str, Any]:
    context = event.context if isinstance(event.context, dict) else {}
    safe: dict[str, Any] = {}
    if event.component == "import":
        for key in ("run_id", "connection_id", "receipt_id", "raw_document_id"):
            if identifier := _uuid(context.get(key)):
                safe[key] = identifier
        if isinstance(context.get("provider"), str) and context.get("provider") in PROVIDERS:
            safe["provider"] = context.get("provider")
        issues = context.get("issues")
        if isinstance(issues, list):
            safe["issues"] = [code for value in issues[:100] if (code := _code(value))]
    else:
        exception = context.get("exception_type")
        if isinstance(exception, str) and _IDENTIFIER.fullmatch(exception):
            safe["exception_type"] = exception
        frames = context.get("frames")
        if isinstance(frames, list):
            safe_frames: list[dict[str, Any]] = []
            for frame in frames[-20:]:
                if not isinstance(frame, dict):
                    continue
                filename, function, line = (
                    frame.get("filename"),
                    frame.get("function"),
                    frame.get("line"),
                )
                if (
                    isinstance(filename, str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}\.py", filename)
                    and isinstance(function, str)
                    and (_IDENTIFIER.fullmatch(function) or function == "<module>")
                    and type(line) is int
                    and 0 < line < 10_000_000
                ):
                    safe_frames.append({"filename": filename, "function": function, "line": line})
            safe["frames"] = safe_frames
        status = context.get("status")
        if type(status) is int and 100 <= status <= 599:
            safe["status"] = status
    return {
        "id": str(event.id),
        "created_at": _timestamp(event.created_at),
        "component": event.component,
        "code": _code(event.code),
        "context": safe,
    }


def _sources(
    db: Session,
    owner_id: UUID,
    start: datetime,
    end: datetime,
    runs: list[SyncRun],
    events: list[DiagnosticEvent],
    archive: ZipFile,
) -> dict[str, Any]:
    event_ids = [
        UUID(identifier)
        for event in events
        if isinstance(event.context, dict)
        if (identifier := _uuid(event.context.get("raw_document_id")))
    ]
    event_run_ids = {
        UUID(identifier)
        for event in events
        if isinstance(event.context, dict)
        if (identifier := _uuid(event.context.get("run_id")))
    }
    failed_ids = [run.id for run in runs if run.status == "failed" and run.id not in event_run_ids]
    historical = exists(
        select(SyncRun.id).where(
            SyncRun.id.in_(failed_ids),
            SyncRun.connection_id == RawDocument.connection_id,
            RawDocument.received_at >= SyncRun.created_at,
            RawDocument.received_at <= SyncRun.finished_at,
        )
    )
    imported = exists(select(Receipt.id).where(Receipt.raw_document_id == RawDocument.id))
    event_priority: dict[UUID, int] = {}
    for index, identifier in enumerate(event_ids):
        event_priority.setdefault(identifier, index)
    source_order = (
        case(event_priority, value=RawDocument.id, else_=MAX_ROWS + 1)
        if event_priority
        else RawDocument.received_at.desc()
    )
    metadata = db.execute(
        select(
            RawDocument.id,
            ProviderConnection.provider,
            func.length(RawDocument.payload),
            RawDocument.parser_version,
            RawDocument.received_at,
        )
        .join(ProviderConnection, ProviderConnection.id == RawDocument.connection_id)
        .where(
            ProviderConnection.owner_id == owner_id,
            RawDocument.external_id.not_like("list:%"),
            or_(
                RawDocument.id.in_(event_ids),
                and_(
                    historical,
                    ~imported,
                    RawDocument.received_at >= start,
                    RawDocument.received_at <= end,
                ),
            ),
        )
        .order_by(source_order, RawDocument.received_at.desc(), RawDocument.id)
        .limit(MAX_ROWS + 1)
    ).all()
    selected: dict[UUID, str] = {}
    source_metadata: dict[UUID, dict[str, Any]] = {}
    omitted = max(0, len(metadata) - MAX_ROWS)
    total = 0
    for identifier, provider, size, parser_version, received_at in metadata[:MAX_ROWS]:
        if (
            provider not in PROVIDERS
            or size > MAX_SOURCE_BYTES
            or len(selected) >= MAX_SOURCES
            or total + size > MAX_TOTAL_BYTES
        ):
            omitted += 1
            continue
        selected[identifier] = provider
        source_metadata[identifier] = {
            "id": str(identifier),
            "provider": provider,
            "parser_version": parser_version,
            "received_at": _timestamp(received_at),
        }
        total += size
    count = 0
    documents: list[dict[str, Any]] = []
    written_bytes = 0
    if selected:
        payloads = db.execute(
            select(RawDocument.id, RawDocument.payload)
            .join(ProviderConnection, ProviderConnection.id == RawDocument.connection_id)
            .where(RawDocument.id.in_(selected), ProviderConnection.owner_id == owner_id)
        )
        for identifier, payload in payloads:
            if len(payload) > MAX_SOURCE_BYTES or written_bytes + len(payload) > MAX_TOTAL_BYTES:
                omitted += 1
                continue
            extension = "pdf" if payload.startswith(b"%PDF") else "json"
            redacted = False
            if extension == "json":
                try:
                    sanitized, redacted = _redact_source(json.loads(payload))
                    if redacted:
                        payload = json.dumps(sanitized, ensure_ascii=True).encode()
                except ValueError, UnicodeError, RecursionError:
                    omitted += 1
                    continue
                if (
                    len(payload) > MAX_SOURCE_BYTES
                    or written_bytes + len(payload) > MAX_TOTAL_BYTES
                ):
                    omitted += 1
                    continue
            path = f"sources/{selected.get(identifier)}/{identifier}.{extension}"
            archive.writestr(path, payload)
            documents.append(
                {**source_metadata.get(identifier, {}), "path": path, "redacted": redacted}
            )
            count += 1
            written_bytes += len(payload)
    return {
        "included": count,
        "omitted": omitted,
        "metadata_truncated": len(metadata) > MAX_ROWS,
        "documents": documents,
    }


@router.get("/download")
def download(
    user: Owner,
    db: Db,
    hours: Period = Period.DAY,
    imports: bool = True,
    technical: bool = False,
) -> Response:
    if not imports and not technical:
        raise HTTPException(422, "diagnostics_empty_selection")
    end = datetime.now(UTC)
    start = end - timedelta(hours=int(hours))
    try:
        app_version = version("cartalytix")
    except PackageNotFoundError:
        app_version = "unknown"
    manifest: dict[str, Any] = {
        "format_version": 1,
        "app_version": app_version,
        "period": {"hours": int(hours), "from": _timestamp(start), "to": _timestamp(end)},
        "features": {"imports": imports, "technical": technical},
        "history": "Structured events are available only since diagnostic recording was enabled.",
        "limits": {
            "runs": MAX_ROWS,
            "events_per_category": MAX_ROWS,
            "sources": MAX_SOURCES,
            "source_bytes": MAX_SOURCE_BYTES,
            "total_source_bytes": MAX_TOTAL_BYTES,
        },
        "omissions": {},
    }
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for component, enabled in (("import", imports), ("technical", technical)):
            if not enabled:
                continue
            components = ["import"] if component == "import" else ["api", "worker"]
            events = list(
                db.scalars(
                    select(DiagnosticEvent)
                    .where(
                        DiagnosticEvent.owner_id == user.id,
                        DiagnosticEvent.created_at >= start,
                        DiagnosticEvent.created_at <= end,
                        DiagnosticEvent.component.in_(components),
                    )
                    .order_by(DiagnosticEvent.created_at.desc(), DiagnosticEvent.id)
                    .limit(MAX_ROWS + 1)
                )
            )
            manifest["omissions"][f"{component}_events_truncated"] = len(events) > MAX_ROWS
            events = events[:MAX_ROWS]
            data: dict[str, Any] = {"events": [_event(event) for event in events]}
            if component == "import":
                rows = db.execute(
                    select(SyncRun, ProviderConnection.provider)
                    .join(ProviderConnection, ProviderConnection.id == SyncRun.connection_id)
                    .where(
                        ProviderConnection.owner_id == user.id,
                        SyncRun.created_at >= start,
                        SyncRun.created_at <= end,
                    )
                    .order_by(SyncRun.created_at.desc(), SyncRun.id)
                    .limit(MAX_ROWS + 1)
                ).all()
                manifest["omissions"]["runs_truncated"] = len(rows) > MAX_ROWS
                rows = rows[:MAX_ROWS]
                data["parser_versions"] = {
                    key: spec.parser_version for key, spec in PROVIDERS.items()
                }
                data["runs"] = [
                    {
                        "id": str(run.id),
                        "connection_id": str(run.connection_id),
                        "provider": provider if provider in PROVIDERS else "unknown",
                        "status": _code(run.status),
                        "error_code": _code(run.error_code),
                        "created_at": _timestamp(run.created_at),
                        "started_at": _timestamp(run.started_at),
                        "finished_at": _timestamp(run.finished_at),
                        "receipts_seen": run.receipts_seen,
                        "receipts_imported": run.receipts_imported,
                        "receipts_skipped": run.receipts_skipped,
                        "pages_count": run.pages_count,
                    }
                    for run, provider in rows
                ]
                manifest["sources"] = _sources(
                    db,
                    user.id,
                    start,
                    end,
                    [run for run, _ in rows],
                    events,
                    archive,
                )
            filename = "imports.json" if component == "import" else "technical-errors.json"
            archive.writestr(filename, json.dumps(data, ensure_ascii=True))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=True))
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename=cartalytix-logs-{end:%Y%m%dT%H%M%SZ}.zip",
        },
    )
