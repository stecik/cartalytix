"""Lidl account linking: refresh handoff and in-app login with OTP verification."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cartalytix.connection_routes import (
    account_guard,
    lidl_login_error,
    owned_connection,
    persist_credentials,
)
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.lidl.client import LidlError, exchange_refresh
from cartalytix.providers.lidl.login_protocol import LoginError
from cartalytix.providers.lidl.login_sessions import LoginOutcome, LoginSessions
from cartalytix.schemas import (
    ConnectionOutput,
    LidlAuthorizeInput,
    LidlLoginInput,
    LidlLoginOutput,
    LidlVerificationInput,
)
from cartalytix.sync import connection_lock

PROVIDER = "lidl"
router = APIRouter(prefix="/api/v1/providers/lidl", tags=["lidl"])


def _connection(db: Session, identifier: UUID, owner_id: UUID) -> None:
    owned_connection(db, identifier, owner_id, PROVIDER)


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
    old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
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
    return persist_credentials(
        engine, db, identifier, owner_id, PROVIDER, old_subject, subject, encrypted
    )


@router.post("/connections/{identifier}/authorize", response_model=ConnectionOutput)
def authorize_connection(
    identifier: UUID, payload: LidlAuthorizeInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    return _authorize(identifier, payload.refresh_token.get_secret_value(), request, owner.id, db)


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
            raise lidl_login_error(error) from None
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
            raise lidl_login_error(error) from None
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
        raise lidl_login_error(error) from None
