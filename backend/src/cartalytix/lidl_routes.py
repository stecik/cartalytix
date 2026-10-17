"""Owner-scoped Lidl connections, refresh handoff and durable sync submission."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine, delete, func, select, tuple_
from sqlalchemy.orm import Session

from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.models import ProviderConnection, Receipt, SyncRun, User
from cartalytix.providers.lidl.client import LidlError, exchange_refresh
from cartalytix.providers.lidl.login_protocol import LoginError
from cartalytix.providers.lidl.login_sessions import LoginOutcome, LoginSessions
from cartalytix.providers.lidl.management import clear_purchases, stop_pending_runs
from cartalytix.schemas import (
    ClearConnectionDataInput,
    ConnectionOutput,
    LidlAuthorizeInput,
    LidlConnectionInput,
    LidlConnectionUpdate,
    LidlLoginInput,
    LidlLoginOutput,
    LidlVerificationInput,
    SyncInput,
    SyncRunOutput,
    SyncRunsOutput,
)
from cartalytix.sync import SyncError, connection_lock, enqueue_sync, publish_pending
from cartalytix.tasks import sync_run

router = APIRouter(prefix="/api/v1/providers/lidl", tags=["lidl"])


def _connection(db: Session, identifier: UUID, owner_id: UUID) -> ProviderConnection:
    connection = db.scalar(
        select(ProviderConnection).where(
            ProviderConnection.id == identifier,
            ProviderConnection.owner_id == owner_id,
            ProviderConnection.provider == "lidl",
        )
    )
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


def _next_sync(interval: int | None) -> datetime | None:
    return datetime.now(UTC) + timedelta(minutes=interval) if interval is not None else None


@router.post("/connections", response_model=ConnectionOutput, status_code=201)
def create_connection(payload: LidlConnectionInput, owner: Owner, db: Db) -> ConnectionOutput:
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
        provider="lidl",
        country="CZ",
        name=payload.name.strip(),
        sync_interval_minutes=payload.sync_interval_minutes,
        next_sync_at=_next_sync(payload.sync_interval_minutes),
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return ConnectionOutput.model_validate(connection)


@router.patch("/connections/{identifier}", response_model=ConnectionOutput)
def update_connection(
    identifier: UUID, payload: LidlConnectionUpdate, owner: Owner, db: Db
) -> ConnectionOutput:
    connection = _connection(db, identifier, owner.id)
    if "name" in payload.model_fields_set:
        if payload.name is None:
            raise HTTPException(422, "validation_error")
        connection.name = payload.name.strip()
    if "sync_interval_minutes" in payload.model_fields_set:
        connection.sync_interval_minutes = payload.sync_interval_minutes
        connection.next_sync_at = _next_sync(payload.sync_interval_minutes)
    db.commit()
    db.refresh(connection)
    return connection_outputs(db, [connection])[0]


def _manage_connection(
    identifier: UUID,
    action: Literal["disconnect", "clear", "remove"],
    request: Request,
    owner_id: UUID,
    db: Session,
    *,
    pause_sync: bool = True,
) -> ConnectionOutput | None:
    _connection(db, identifier, owner_id)
    db.rollback()
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        # The row lock also serializes scheduling/enqueueing against lifecycle changes.
        connection = db.scalar(
            select(ProviderConnection)
            .where(
                ProviderConnection.id == identifier,
                ProviderConnection.owner_id == owner_id,
                ProviderConnection.provider == "lidl",
            )
            .with_for_update()
        )
        if connection is None:
            raise HTTPException(404, "connection_not_found")
        if action in {"disconnect", "remove"}:
            sessions = cast(LoginSessions, request.app.state.lidl_logins)
            try:
                sessions.cancel_connection(owner_id, identifier)
            except LoginError as error:
                raise _login_error(error) from None
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


@router.post("/connections/{identifier}/disconnect", response_model=ConnectionOutput)
def disconnect_connection(
    identifier: UUID, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    result = _manage_connection(identifier, "disconnect", request, owner.id, db)
    assert result is not None
    return result


@router.post("/connections/{identifier}/clear-data", response_model=ConnectionOutput)
def clear_connection_data(
    identifier: UUID,
    payload: ClearConnectionDataInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> ConnectionOutput:
    result = _manage_connection(
        identifier, "clear", request, owner.id, db, pause_sync=payload.pause_sync
    )
    assert result is not None
    return result


@router.delete("/connections/{identifier}", status_code=204)
def remove_connection(identifier: UUID, request: Request, owner: Owner, db: Db) -> None:
    _manage_connection(identifier, "remove", request, owner.id, db)


def _authorize(
    identifier: UUID, refresh_token: str, request: Request, owner_id: UUID, db: Session
) -> ConnectionOutput:
    _connection(db, identifier, owner_id)
    # Release authentication/read transactions before locks, Redis and OAuth network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"lidl-auth:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "authorization_rate_limited")
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        return _store_authorization(identifier, refresh_token, request, owner_id, db)


def _store_authorization(
    identifier: UUID, refresh_token: str, request: Request, owner_id: UUID, db: Session
) -> ConnectionOutput:
    """Caller holds the advisory lock through OAuth and credential persistence."""
    engine = cast(Engine, request.app.state.engine)
    with Session(engine) as snapshot:
        connection = _connection(snapshot, identifier, owner_id)
        old_subject = connection.account_subject or None
        if (
            old_subject is None
            and snapshot.scalar(
                select(Receipt.id).where(Receipt.connection_id == identifier).limit(1)
            )
            is not None
        ):
            raise HTTPException(409, "account_identity_unavailable")
    try:
        cipher = TokenCipher(request.app.state.settings)
        tokens = exchange_refresh(refresh_token)
        subject = tokens.get("subject")
        if not isinstance(subject, str) or not subject or len(subject) > 256:
            raise HTTPException(422, "account_identity_unavailable")
        if old_subject is not None and old_subject != subject:
            raise HTTPException(409, "account_mismatch")
        encrypted = cipher.encrypt(tokens.get("refresh_token", ""))
    except CredentialError:
        raise HTTPException(503, "credentials_unavailable") from None
    except LidlError as error:
        raise HTTPException(422, error.code) from None
    with Session(engine, expire_on_commit=False) as persistence, persistence.begin():
        connection = _connection(persistence, identifier, owner_id)
        connection.encrypted_refresh_token = encrypted
        connection.account_subject = subject
        connection.status = "connected"
        connection.next_sync_at = _next_sync(connection.sync_interval_minutes)
    # Read response only after the rotation transaction is committed.
    connection = _connection(db, identifier, owner_id)
    return connection_outputs(db, [connection])[0]


@router.post("/connections/{identifier}/authorize", response_model=ConnectionOutput)
def authorize_connection(
    identifier: UUID, payload: LidlAuthorizeInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    return _authorize(identifier, payload.refresh_token.get_secret_value(), request, owner.id, db)


def _login_error(error: LoginError) -> HTTPException:
    code = str(error)
    status = 409 if code in {"login_expired", "login_busy", "device_bootstrap_busy"} else 422
    if code == "login_rate_limited":
        status = 429
    return HTTPException(status, code)


def _login_output(
    outcome: LoginOutcome, identifier: UUID, request: Request, owner_id: UUID, db: Session
) -> LidlLoginOutput:
    if outcome.step == "completed" and outcome.refresh_token:
        connection = _store_authorization(identifier, outcome.refresh_token, request, owner_id, db)
        return LidlLoginOutput(step="completed", connection=connection)
    method = (outcome.method or "").casefold()
    return LidlLoginOutput(
        step="otp",
        flow_id=outcome.flow_id,
        method="email" if method == "email" else "sms" if method in {"sms", "phone"} else None,
    )


@router.post("/connections/{identifier}/login", response_model=LidlLoginOutput)
def login_connection(
    identifier: UUID, payload: LidlLoginInput, request: Request, owner: Owner, db: Db
) -> LidlLoginOutput:
    owner_id = owner.id
    _connection(db, identifier, owner_id)
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"lidl-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    sessions = cast(LoginSessions, request.app.state.lidl_logins)
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        with Session(engine) as snapshot:
            _connection(snapshot, identifier, owner_id)
        try:
            outcome = sessions.start(
                owner_id, identifier, str(payload.email), payload.password.get_secret_value()
            )
        except LoginError as error:
            raise _login_error(error) from None
        return _login_output(outcome, identifier, request, owner_id, db)


@router.post("/connections/{identifier}/login/{flow_id}/verify", response_model=LidlLoginOutput)
def verify_connection_login(
    identifier: UUID,
    flow_id: str,
    payload: LidlVerificationInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> LidlLoginOutput:
    owner_id = owner.id
    _connection(db, identifier, owner_id)
    db.rollback()
    sessions = cast(LoginSessions, request.app.state.lidl_logins)
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        with Session(engine) as snapshot:
            _connection(snapshot, identifier, owner_id)
        try:
            outcome = sessions.verify(
                owner_id, identifier, flow_id, payload.code.get_secret_value()
            )
        except LoginError as error:
            raise _login_error(error) from None
        return _login_output(outcome, identifier, request, owner_id, db)


@router.delete("/connections/{identifier}/login/{flow_id}", status_code=204)
def cancel_connection_login(
    identifier: UUID, flow_id: str, request: Request, owner: Owner, db: Db
) -> None:
    owner_id = owner.id
    _connection(db, identifier, owner_id)
    db.rollback()
    sessions = cast(LoginSessions, request.app.state.lidl_logins)
    try:
        sessions.cancel(owner_id, identifier, flow_id)
    except LoginError as error:
        raise _login_error(error) from None


@router.post("/connections/{identifier}/sync", response_model=SyncRunOutput, status_code=202)
def synchronize_connection(
    identifier: UUID,
    payload: SyncInput,
    request: Request,
    background: BackgroundTasks,
    owner: Owner,
    db: Db,
) -> SyncRunOutput:
    owner_id = owner.id
    _connection(db, identifier, owner_id)
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


@router.get("/connections/{identifier}/runs", response_model=SyncRunsOutput)
def sync_runs(
    identifier: UUID,
    owner: Owner,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SyncRunsOutput:
    _connection(db, identifier, owner.id)
    runs = db.scalars(
        select(SyncRun)
        .where(SyncRun.connection_id == identifier)
        .order_by(SyncRun.created_at.desc(), SyncRun.id.desc())
        .limit(limit)
    )
    return SyncRunsOutput(items=[SyncRunOutput.model_validate(run) for run in runs])
