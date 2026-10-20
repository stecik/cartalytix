"""foodora linking: the owner pastes the browser session's access token.

Login and token refresh are bot-protected, so the app never sees the password and cannot
rotate tokens; the verified token is stored encrypted until it expires.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.foodora.client import FoodoraError, link
from cartalytix.schemas import ConnectionOutput, FoodoraAuthorizeInput
from cartalytix.sync import connection_lock

PROVIDER = "foodora"
router = APIRouter(prefix="/api/v1/providers/foodora", tags=["foodora"])


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
