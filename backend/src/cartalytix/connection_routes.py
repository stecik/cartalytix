"""Owner-scoped provider connections, lifecycle management and durable sync submission."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from sqlalchemy import Engine, delete, func, select, tuple_
from sqlalchemy.orm import Session

from cartalytix.dependencies import Db, Owner
from cartalytix.models import ProviderConnection, Receipt, SyncRun, User
from cartalytix.providers.lidl.login_protocol import LoginError
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.providers.lidl.management import clear_purchases, stop_pending_runs
from cartalytix.schemas import (
    ClearConnectionDataInput,
    ConnectionInput,
    ConnectionOutput,
    ConnectionUpdate,
    ProviderCode,
    SyncInput,
    SyncRunOutput,
    SyncRunsOutput,
)
from cartalytix.sync import SyncError, connection_lock, enqueue_sync, publish_pending
from cartalytix.tasks import sync_run

router = APIRouter(prefix="/api/v1/providers/{provider}/connections", tags=["connections"])


def owned_connection(
    db: Session, identifier: UUID, owner_id: UUID, provider: str, *, lock: bool = False
) -> ProviderConnection:
    query = select(ProviderConnection).where(
        ProviderConnection.id == identifier,
        ProviderConnection.owner_id == owner_id,
        ProviderConnection.provider == provider,
    )
    connection = db.scalar(query.with_for_update() if lock else query)
    if connection is None:
        raise HTTPException(404, "connection_not_found")
    return connection


def connection_outputs(
    db: Session, connections: list[ProviderConnection]
) -> list[ConnectionOutput]:
    if not connections:
        return []
    identifiers = [connection.id for connection in connections]
    newest = (
        select(SyncRun.connection_id, func.max(SyncRun.created_at))
        .where(SyncRun.connection_id.in_(identifiers))
        .group_by(SyncRun.connection_id)
    )
    runs = db.scalars(
        select(SyncRun)
        .where(tuple_(SyncRun.connection_id, SyncRun.created_at).in_(newest))
        .order_by(SyncRun.id.desc())
    )
    latest: dict[UUID, SyncRunOutput] = {}
    for run in runs:
        if run.connection_id not in latest:
            latest[run.connection_id] = SyncRunOutput.model_validate(run)
    counts = dict(
        db.execute(
            select(Receipt.connection_id, func.count(Receipt.id))
            .where(Receipt.connection_id.in_(identifiers))
            .group_by(Receipt.connection_id)
        ).all()
    )
    return [
        ConnectionOutput.model_validate(connection).model_copy(
            update={
                "latest_sync": latest.get(connection.id),
                "receipt_count": counts.get(connection.id, 0),
            }
        )
        for connection in connections
    ]


def lidl_login_error(error: LoginError) -> HTTPException:
    code = str(error)
    status = 409 if code in {"login_expired", "login_busy", "device_bootstrap_busy"} else 422
    if code == "login_rate_limited":
        status = 429
    return HTTPException(status, code)


def next_sync(interval: int | None) -> datetime | None:
    return datetime.now(UTC) + timedelta(minutes=interval) if interval is not None else None


def account_guard(engine: Engine, identifier: UUID, owner_id: UUID, provider: str) -> str | None:
    """Return the bound account; history without a binding can never be re-linked safely."""
    with Session(engine) as snapshot:
        connection = owned_connection(snapshot, identifier, owner_id, provider)
        old_subject = connection.account_subject or None
        if (
            old_subject is None
            and snapshot.scalar(
                select(Receipt.id).where(Receipt.connection_id == identifier).limit(1)
            )
            is not None
        ):
            raise HTTPException(409, "account_identity_unavailable")
    return old_subject


def persist_credentials(
    engine: Engine,
    db: Session,
    identifier: UUID,
    owner_id: UUID,
    provider: str,
    old_subject: str | None,
    subject: str,
    encrypted: bytes,
    label: str | None = None,
) -> ConnectionOutput:
    """Caller holds the connection advisory lock through network login and persistence."""
    if not subject or len(subject) > 256:
        raise HTTPException(422, "account_identity_unavailable")
    if old_subject is not None and old_subject != subject:
        raise HTTPException(409, "account_mismatch")
    with Session(engine, expire_on_commit=False) as persistence, persistence.begin():
        connection = owned_connection(persistence, identifier, owner_id, provider)
        connection.encrypted_refresh_token = encrypted
        connection.account_subject = subject
        if label:
            # Sign-in e-mail shown when the owner leaves the account unnamed.
            connection.account_label = label[:254]
        connection.status = "connected"
        connection.next_sync_at = next_sync(connection.sync_interval_minutes)
    # Read response only after the credential transaction is committed.
    connection = owned_connection(db, identifier, owner_id, provider)
    return connection_outputs(db, [connection])[0]


@router.post("", response_model=ConnectionOutput, status_code=201)
def create_connection(
    provider: ProviderCode, payload: ConnectionInput, owner: Owner, db: Db
) -> ConnectionOutput:
    # Serialize the owner cap across concurrent requests, without lazy relationships.
    db.scalar(select(User).where(User.id == owner.id).with_for_update())
    count = (
        db.scalar(
            select(func.count())
            .select_from(ProviderConnection)
            .where(ProviderConnection.owner_id == owner.id)
        )
        or 0
    )
    if count >= 100:
        raise HTTPException(409, "connection_limit_reached")
    connection = ProviderConnection(
        owner_id=owner.id,
        provider=provider.value,
        country="CZ",
        name=payload.name.strip(),
        sync_interval_minutes=payload.sync_interval_minutes,
        next_sync_at=next_sync(payload.sync_interval_minutes),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return ConnectionOutput.model_validate(connection)


@router.patch("/{identifier}", response_model=ConnectionOutput)
def update_connection(
    provider: ProviderCode, identifier: UUID, payload: ConnectionUpdate, owner: Owner, db: Db
) -> ConnectionOutput:
    connection = owned_connection(db, identifier, owner.id, provider.value)
    if "name" in payload.model_fields_set:
        if payload.name is None:
            raise HTTPException(422, "validation_error")
        connection.name = payload.name.strip()
    if "sync_interval_minutes" in payload.model_fields_set:
        connection.sync_interval_minutes = payload.sync_interval_minutes
        connection.next_sync_at = next_sync(payload.sync_interval_minutes)
    db.commit()
    db.refresh(connection)
    return connection_outputs(db, [connection])[0]


def _manage_connection(
    provider: str,
    identifier: UUID,
    action: Literal["disconnect", "clear", "remove"],
    request: Request,
    owner_id: UUID,
    db: Session,
    *,
    pause_sync: bool = True,
) -> ConnectionOutput | None:
    owned_connection(db, identifier, owner_id, provider)
    db.rollback()
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        # The row lock also serializes scheduling/enqueueing against lifecycle changes.
        connection = owned_connection(db, identifier, owner_id, provider, lock=True)
        if action in {"disconnect", "remove"} and provider == ProviderCode.LIDL:
            sessions = cast(LoginSessions, request.app.state.lidl_logins)
            try:
                sessions.cancel_connection(owner_id, identifier)
            except LoginError as error:
                raise lidl_login_error(error) from None
        if action == "remove":
            clear_purchases(db, identifier)
            db.execute(delete(SyncRun).where(SyncRun.connection_id == identifier))
            db.execute(delete(ProviderConnection).where(ProviderConnection.id == identifier))
            db.commit()
            return None
        if action == "disconnect":
            connection.encrypted_refresh_token = None
            # Keep the identity binding while its purchase history remains stored.
            connection.status = "disconnected"
        else:
            clear_purchases(db, identifier)
        if pause_sync or action == "disconnect":
            connection.sync_interval_minutes = None
            connection.next_sync_at = None
        stop_pending_runs(
            db, identifier, "connection_disconnected" if action == "disconnect" else "data_cleared"
        )
        db.commit()
        return connection_outputs(db, [connection])[0]


@router.post("/{identifier}/disconnect", response_model=ConnectionOutput)
def disconnect_connection(
    provider: ProviderCode, identifier: UUID, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    result = _manage_connection(provider.value, identifier, "disconnect", request, owner.id, db)
    assert result is not None
    return result


@router.post("/{identifier}/clear-data", response_model=ConnectionOutput)
def clear_connection_data(
    provider: ProviderCode,
    identifier: UUID,
    payload: ClearConnectionDataInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> ConnectionOutput:
    result = _manage_connection(
        provider.value, identifier, "clear", request, owner.id, db, pause_sync=payload.pause_sync
    )
    assert result is not None
    return result


@router.delete("/{identifier}", status_code=204)
def remove_connection(
    provider: ProviderCode, identifier: UUID, request: Request, owner: Owner, db: Db
) -> None:
    _manage_connection(provider.value, identifier, "remove", request, owner.id, db)


@router.post("/{identifier}/sync", response_model=SyncRunOutput, status_code=202)
def synchronize_connection(
    provider: ProviderCode,
    identifier: UUID,
    payload: SyncInput,
    request: Request,
    background: BackgroundTasks,
    owner: Owner,
    db: Db,
) -> SyncRunOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, provider.value)
    db.rollback()
    engine = cast(Engine, request.app.state.engine)
    try:
        run = enqueue_sync(engine, identifier, owner_id, payload.refresh_existing)
    except SyncError as error:
        status = 404 if error.code == "connection_not_found" else 409
        raise HTTPException(status, error.code) from None

    def send(run_id: str) -> None:
        sync_run.delay(run_id)

    background.add_task(publish_pending, engine, send, run_id=run.id)
    return SyncRunOutput.model_validate(run)


@router.get("/{identifier}/runs", response_model=SyncRunsOutput)
def sync_runs(
    provider: ProviderCode,
    identifier: UUID,
    owner: Owner,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SyncRunsOutput:
    owned_connection(db, identifier, owner.id, provider.value)
    total = db.scalar(
        select(func.count()).select_from(SyncRun).where(SyncRun.connection_id == identifier)
    )
    runs = db.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == identifier)
        .order_by(SyncRun.created_at.desc(), SyncRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return SyncRunsOutput(
        items=[SyncRunOutput.model_validate(run) for run in runs],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
