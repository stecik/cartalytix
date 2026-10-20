"""Globus account linking: the owner pastes the globus.cz session cookie from their browser."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.globus.client import GlobusError, fetch_identity, parse_session_cookie
from cartalytix.schemas import ConnectionOutput, GlobusLoginInput
from cartalytix.sync import connection_lock

PROVIDER = "globus"
router = APIRouter(prefix="/api/v1/providers/globus", tags=["globus"])


@router.post("/connections/{identifier}/login", response_model=ConnectionOutput)
def login_connection(
    identifier: UUID, payload: GlobusLoginInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"globus-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    try:
        cookie = parse_session_cookie(payload.cookie.get_secret_value())
    except GlobusError as error:
        raise HTTPException(422, error.code) from None
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
        try:
            cipher = TokenCipher(request.app.state.settings)
            account = fetch_identity(cookie)
            # The cookie is the only credential; it sits in the refresh-token column.
            encrypted = cipher.encrypt(cookie)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except GlobusError as error:
            raise HTTPException(422, error.code) from None
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
