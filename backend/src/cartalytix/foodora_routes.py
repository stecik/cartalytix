"""Foodora account linking with password, optional OTP and encrypted refresh sessions."""

from collections.abc import Callable
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.foodora.client import FoodoraError, link
from cartalytix.providers.foodora.login_sessions import LoginOutcome, LoginSessions
from cartalytix.schemas import (
    ConnectionOutput,
    FoodoraAuthorizeInput,
    FoodoraLoginInput,
    FoodoraLoginOutput,
    FoodoraMethod,
    FoodoraMethodInput,
    FoodoraVerificationInput,
)
from cartalytix.sync import connection_lock

PROVIDER = "foodora"
router = APIRouter(prefix="/api/v1/providers/foodora", tags=["foodora"])


def login_error(error: FoodoraError) -> HTTPException:
    status = 422
    if error.code in {"login_expired", "login_busy", "account_mismatch"}:
        status = 409
    elif error.code in {"rate_limited", "login_rate_limited"}:
        status = 429
    elif error.code in {"service_unavailable", "provider_unavailable", "network_error"}:
        status = 503
    return HTTPException(status, error.code)


def _login(
    identifier: UUID,
    request: Request,
    owner_id: UUID,
    db: Session,
    operation: Callable[[LoginSessions], LoginOutcome],
) -> FoodoraLoginOutput:
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"foodora-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
        try:
            # Fail before sending credentials when encrypted persistence is unavailable.
            cipher = TokenCipher(request.app.state.settings)
            outcome = operation(cast(LoginSessions, request.app.state.foodora_logins))
            if outcome.step == "completed":
                if outcome.session is None:
                    raise FoodoraError("service_unavailable")
                connection = persist_credentials(
                    engine,
                    db,
                    identifier,
                    owner_id,
                    PROVIDER,
                    old_subject,
                    outcome.session.subject,
                    cipher.encrypt(outcome.session.serialize()),
                    outcome.email,
                )
                return FoodoraLoginOutput(step="completed", connection=connection)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except FoodoraError as error:
            raise login_error(error) from None
        return FoodoraLoginOutput(
            step=outcome.step,
            flow_id=outcome.flow_id,
            methods=[FoodoraMethod(method) for method in outcome.methods],
            method=FoodoraMethod(outcome.method) if outcome.method else None,
        )


@router.post("/connections/{identifier}/login", response_model=FoodoraLoginOutput)
def login_connection(
    identifier: UUID, payload: FoodoraLoginInput, request: Request, owner: Owner, db: Db
) -> FoodoraLoginOutput:
    owner_id = owner.id
    return _login(
        identifier,
        request,
        owner_id,
        db,
        lambda sessions: sessions.start(
            owner_id, identifier, str(payload.email), payload.password.get_secret_value()
        ),
    )


@router.post("/connections/{identifier}/login/{flow_id}/send", response_model=FoodoraLoginOutput)
def send_connection_code(
    identifier: UUID,
    flow_id: str,
    payload: FoodoraMethodInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> FoodoraLoginOutput:
    owner_id = owner.id
    return _login(
        identifier,
        request,
        owner_id,
        db,
        lambda sessions: sessions.send(owner_id, identifier, flow_id, payload.method.value),
    )


@router.post("/connections/{identifier}/login/{flow_id}/verify", response_model=FoodoraLoginOutput)
def verify_connection_code(
    identifier: UUID,
    flow_id: str,
    payload: FoodoraVerificationInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> FoodoraLoginOutput:
    owner_id = owner.id
    return _login(
        identifier,
        request,
        owner_id,
        db,
        lambda sessions: sessions.verify(
            owner_id, identifier, flow_id, payload.code.get_secret_value()
        ),
    )


@router.delete("/connections/{identifier}/login/{flow_id}", status_code=204)
def cancel_connection_login(
    identifier: UUID, flow_id: str, request: Request, owner: Owner, db: Db
) -> None:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    try:
        cast(LoginSessions, request.app.state.foodora_logins).cancel(owner_id, identifier, flow_id)
    except FoodoraError as error:
        raise login_error(error) from None


@router.post("/connections/{identifier}/authorize", response_model=ConnectionOutput)
def authorize_connection(
    identifier: UUID, payload: FoodoraAuthorizeInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before locks, Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"foodora-auth:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "authorization_rate_limited")
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
        try:
            cipher = TokenCipher(request.app.state.settings)
            account = link(payload.access_token.get_secret_value().strip())
            encrypted = cipher.encrypt(account.credential)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except FoodoraError as error:
            raise HTTPException(429 if error.code == "rate_limited" else 422, error.code) from None
        return persist_credentials(
            engine, db, identifier, owner_id, PROVIDER, old_subject, account.subject, encrypted
        )
