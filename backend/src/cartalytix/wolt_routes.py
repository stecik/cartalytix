"""Wolt account linking: the owner pastes a sign-in link requested in their own browser."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.diagnostics import capture_error
from cartalytix.providers.wolt.client import WoltError, exchange_magic_link, parse_magic_link
from cartalytix.schemas import ConnectionOutput, WoltLoginInput
from cartalytix.sync import connection_lock

PROVIDER = "wolt"
router = APIRouter(prefix="/api/v1/providers/wolt", tags=["wolt"])


@router.post("/connections/{identifier}/login", response_model=ConnectionOutput)
def login_connection(
    identifier: UUID, payload: WoltLoginInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"wolt-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    try:
        token = parse_magic_link(payload.link.get_secret_value())
    except WoltError as error:
        raise HTTPException(422, error.code) from None
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
        try:
            cipher = TokenCipher(request.app.state.settings)
            account = exchange_magic_link(token)
            encrypted = cipher.encrypt(account.refresh_token)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except WoltError as error:
            if error.diagnostic:
                capture_error(
                    engine,
                    owner_id,
                    "api",
                    error.code,
                    error=error,
                    auth=error.diagnostic,
                    provider=PROVIDER,
                    connection_id=identifier,
                )
            raise HTTPException(
                429 if error.code == "login_rate_limited" else 422, error.code
            ) from None
        return persist_credentials(
            engine,
            db,
            identifier,
            owner_id,
            PROVIDER,
            old_subject,
            account.subject,
            encrypted,
            account.email,
        )
