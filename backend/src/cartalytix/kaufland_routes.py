"""Kaufland Card linking: e-mail, password and optional code; only the refresh token is kept."""

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
from cartalytix.providers.kaufland.client import KauflandError, exchange_refresh
from cartalytix.providers.lidl.login_protocol import LoginError
from cartalytix.providers.lidl.login_sessions import LoginOutcome, LoginSessions
from cartalytix.schemas import (
    LidlLoginInput,
    LidlLoginOutput,
    LidlVerificationInput,
)
from cartalytix.sync import connection_lock

PROVIDER = "kaufland"
router = APIRouter(prefix="/api/v1/providers/kaufland", tags=["kaufland"])


def _sessions(request: Request) -> LoginSessions:
    return cast(LoginSessions, request.app.state.kaufland_logins)


def _output(
    outcome: LoginOutcome, identifier: UUID, request: Request, owner_id: UUID, db: Session
) -> LidlLoginOutput:
    if outcome.step != "completed" or not outcome.refresh_token:
        method = (outcome.method or "").casefold()
        return LidlLoginOutput(
            step="otp",
            flow_id=outcome.flow_id,
            method="email" if method == "email" else "sms" if method in {"sms", "phone"} else None,
        )
    # Caller holds the advisory lock through token rotation and credential persistence.
    engine = cast(Engine, request.app.state.engine)
    old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
    try:
        cipher = TokenCipher(request.app.state.settings)
        account = exchange_refresh(outcome.refresh_token)
        if old_subject is not None and old_subject != account.subject:
            raise HTTPException(409, "account_mismatch")
        encrypted = cipher.encrypt(account.refresh_token)
    except CredentialError:
        raise HTTPException(503, "credentials_unavailable") from None
    except KauflandError as error:
        raise HTTPException(
            429 if error.code == "login_rate_limited" else 422, error.code
        ) from None
    connection = persist_credentials(
        engine,
        db,
        identifier,
        owner_id,
        PROVIDER,
        old_subject,
        account.subject,
        encrypted,
        account.email or outcome.email,
    )
    return LidlLoginOutput(step="completed", connection=connection)


@router.post("/connections/{identifier}/login", response_model=LidlLoginOutput)
def login_connection(
    identifier: UUID, payload: LidlLoginInput, request: Request, owner: Owner, db: Db
) -> LidlLoginOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before locks, Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"kaufland-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        try:
            outcome = _sessions(request).start(
                owner_id, identifier, str(payload.email), payload.password.get_secret_value()
            )
        except LoginError as error:
            raise lidl_login_error(error) from None
        return _output(outcome, identifier, request, owner_id, db)


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
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        try:
            outcome = _sessions(request).verify(
                owner_id, identifier, flow_id, payload.code.get_secret_value()
            )
        except LoginError as error:
            raise lidl_login_error(error) from None
        return _output(outcome, identifier, request, owner_id, db)


@router.delete("/connections/{identifier}/login/{flow_id}", status_code=204)
def cancel_connection_login(
    identifier: UUID, flow_id: str, request: Request, owner: Owner, db: Db
) -> None:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    try:
        _sessions(request).cancel(owner_id, identifier, flow_id)
    except LoginError as error:
        raise lidl_login_error(error) from None
