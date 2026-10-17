"""Mixit account linking: credentials go straight to Mixit and are never stored."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.mixit.client import MixitError, sign_in
from cartalytix.schemas import ConnectionOutput, MixitLoginInput
from cartalytix.sync import connection_lock

PROVIDER = "mixit"
router = APIRouter(prefix="/api/v1/providers/mixit", tags=["mixit"])


@router.post("/connections/{identifier}/login", response_model=ConnectionOutput)
def login_connection(
    identifier: UUID, payload: MixitLoginInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before locks, Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"mixit-login:{owner_id}"
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
            cipher = TokenCipher(request.app.state.settings)
            account = sign_in(str(payload.email), payload.password.get_secret_value())
            encrypted = cipher.encrypt(account.token)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except MixitError as error:
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
            str(payload.email),
        )
