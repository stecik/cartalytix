"""Shoptet shop linking: credentials are verified live, then stored only encrypted."""

import json
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.diagnostics import capture_error
from cartalytix.providers.shoptet.client import SHOPS, ShoptetError, sign_in
from cartalytix.schemas import ConnectionOutput, ShoptetLoginInput, ShoptetShop
from cartalytix.sync import connection_lock

# Included after the fixed-path provider routers, so only Shoptet shops reach it.
router = APIRouter(prefix="/api/v1/providers/{shop}", tags=["shoptet"])


@router.post("/connections/{identifier}/login", response_model=ConnectionOutput)
def login_connection(
    shop: ShoptetShop,
    identifier: UUID,
    payload: ShoptetLoginInput,
    request: Request,
    owner: Owner,
    db: Db,
) -> ConnectionOutput:
    owner_id = owner.id
    provider = shop.value
    owned_connection(db, identifier, owner_id, provider)
    # Release authentication/read transactions before locks, Redis and network waits.
    db.rollback()
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"{provider}-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, provider)
        email = str(payload.email)
        password = payload.password.get_secret_value()
        try:
            cipher = TokenCipher(request.app.state.settings)
            subject = sign_in(email, password, site=SHOPS[provider])
            # Shoptet has no refresh token; background sync signs in with these credentials.
            encrypted = cipher.encrypt(json.dumps({"email": email, "password": password}))
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except ShoptetError as error:
            if error.diagnostic:
                capture_error(
                    engine,
                    owner_id,
                    "api",
                    error.code,
                    error=error,
                    auth=error.diagnostic,
                    provider=provider,
                    connection_id=identifier,
                )
            raise HTTPException(
                429 if error.code == "login_rate_limited" else 422, error.code
            ) from None
        return persist_credentials(
            engine, db, identifier, owner_id, provider, old_subject, subject, encrypted, email
        )
