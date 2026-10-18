"""Bolt Food account linking: phone + SMS code; only the rotating refresh token is stored."""

import json
import time
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.bolt.client import (
    BoltError,
    confirm_verification,
    new_device,
    start_verification,
)
from cartalytix.schemas import (
    BoltLoginInput,
    BoltLoginOutput,
    BoltVerificationInput,
    ConnectionOutput,
)
from cartalytix.sync import connection_lock

PROVIDER = "bolt"
FLOW_SECONDS = 600
router = APIRouter(prefix="/api/v1/providers/bolt", tags=["bolt"])


def _limit(request: Request, owner_id: UUID) -> None:
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"bolt-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")


def _error(error: BoltError) -> HTTPException:
    return HTTPException(429 if error.code == "login_rate_limited" else 422, error.code)


def _cipher(request: Request) -> TokenCipher:
    try:
        return TokenCipher(request.app.state.settings)
    except CredentialError:
        raise HTTPException(503, "credentials_unavailable") from None


@router.post("/connections/{identifier}/login", response_model=BoltLoginOutput)
def start_login(
    identifier: UUID, payload: BoltLoginInput, request: Request, owner: Owner, db: Db
) -> BoltLoginOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    # Release authentication/read transactions before Redis and network waits.
    db.rollback()
    _limit(request, owner_id)
    cipher = _cipher(request)
    device = new_device()
    try:
        start_verification(payload.phone, device)
    except BoltError as error:
        raise _error(error) from None
    # Stateless flow: the encrypted handle survives restarts and binds owner and connection.
    flow = {
        "phone": payload.phone,
        "device_id": device,
        "owner": str(owner_id),
        "connection": str(identifier),
        "expires_at": time.time() + FLOW_SECONDS,
    }
    try:
        return BoltLoginOutput(flow_id=cipher.encrypt(json.dumps(flow)).decode())
    except CredentialError:
        raise HTTPException(503, "credentials_unavailable") from None


@router.post("/connections/{identifier}/login/verify", response_model=ConnectionOutput)
def verify_login(
    identifier: UUID, payload: BoltVerificationInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    _limit(request, owner_id)
    cipher = _cipher(request)
    try:
        flow = json.loads(cipher.decrypt(payload.flow_id.get_secret_value().encode()))
    except CredentialError, ValueError:
        raise HTTPException(409, "login_expired") from None
    if (
        not isinstance(flow, dict)
        or flow.get("owner") != str(owner_id)
        or flow.get("connection") != str(identifier)
        or float(flow.get("expires_at", 0)) < time.time()
    ):
        raise HTTPException(409, "login_expired")
    phone, device = str(flow.get("phone", "")), str(flow.get("device_id", ""))
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(engine, identifier, owner_id, PROVIDER)
        try:
            account = confirm_verification(phone, device, payload.code.get_secret_value())
            encrypted = cipher.encrypt(account.credential)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except BoltError as error:
            raise _error(error) from None
        return persist_credentials(
            engine,
            db,
            identifier,
            owner_id,
            PROVIDER,
            old_subject,
            account.subject,
            encrypted,
            phone,
        )
