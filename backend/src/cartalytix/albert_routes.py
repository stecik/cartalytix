"""Albert linking through SMS with owner-bound encrypted flows and refresh credentials."""

import json
import math
import time
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from redis.exceptions import RedisError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from cartalytix.connection_routes import account_guard, owned_connection, persist_credentials
from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.providers.albert.client import (
    AlbertConfig,
    AlbertError,
    confirm_verification,
    start_verification,
)
from cartalytix.schemas import (
    AlbertLoginInput,
    AlbertVerificationInput,
    BoltLoginOutput,
    ConnectionOutput,
)
from cartalytix.sync import connection_lock

PROVIDER = "albert"
router = APIRouter(prefix="/api/v1/providers/albert", tags=["albert"])


def _limit(request: Request, owner_id: UUID) -> None:
    try:
        allowed = request.app.state.limiter.check(
            request.client.host if request.client else "unknown", f"albert-login:{owner_id}"
        )
    except RedisError:
        raise HTTPException(503, "service_unavailable") from None
    if not allowed:
        raise HTTPException(429, "login_rate_limited")


def _error(error: AlbertError) -> HTTPException:
    status = 429 if error.code == "login_rate_limited" else 422
    if error.code == "credentials_unavailable":
        status = 503
    return HTTPException(status, error.code)


def _cipher(request: Request) -> TokenCipher:
    try:
        return TokenCipher(request.app.state.settings)
    except CredentialError:
        raise HTTPException(503, "credentials_unavailable") from None


@router.post("/connections/{identifier}/login", response_model=BoltLoginOutput)
def start_login(
    identifier: UUID, payload: AlbertLoginInput, request: Request, owner: Owner, db: Db
) -> BoltLoginOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    _limit(request, owner_id)
    cipher = _cipher(request)
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        try:
            config = AlbertConfig.from_settings(request.app.state.settings)
            with Session(engine) as persistence, persistence.begin():
                connection = owned_connection(
                    persistence, identifier, owner_id, PROVIDER, lock=True
                )
                connection.login_revision += 1
                revision = connection.login_revision
            result = start_verification(payload.phone, config=config)
        except AlbertError as error:
            raise _error(error) from None
        flow = {
            "revision": revision,
            "phone": result.phone,
            "installation_id": result.installation_id,
            "login_id": result.login_id,
            "clock_offset": result.clock_offset,
            "owner": str(owner_id),
            "connection": str(identifier),
            "expires_at": result.expires_at,
        }
        try:
            return BoltLoginOutput(flow_id=cipher.encrypt(json.dumps(flow)).decode())
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None


def _flow(value: str, cipher: TokenCipher, owner: UUID, identifier: UUID) -> dict[str, Any]:
    try:
        flow = json.loads(cipher.decrypt(value.encode()))
    except CredentialError, ValueError:
        raise HTTPException(409, "login_expired") from None
    if not isinstance(flow, dict):
        raise HTTPException(409, "login_expired")
    expires = flow.get("expires_at")
    offset = flow.get("clock_offset")
    if (
        type(flow.get("revision")) is not int
        or flow.get("revision", 0) < 1
        or flow.get("owner") != str(owner)
        or flow.get("connection") != str(identifier)
        or isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or not math.isfinite(expires)
        or expires <= time.time()
        or isinstance(offset, bool)
        or not isinstance(offset, (int, float))
        or not math.isfinite(offset)
        or any(
            not isinstance(flow.get(key), str) or not flow.get(key)
            for key in ("phone", "installation_id", "login_id")
        )
    ):
        raise HTTPException(409, "login_expired")
    return flow


@router.post("/connections/{identifier}/login/verify", response_model=ConnectionOutput)
def verify_login(
    identifier: UUID, payload: AlbertVerificationInput, request: Request, owner: Owner, db: Db
) -> ConnectionOutput:
    owner_id = owner.id
    owned_connection(db, identifier, owner_id, PROVIDER)
    db.rollback()
    _limit(request, owner_id)
    cipher = _cipher(request)
    flow = _flow(payload.flow_id.get_secret_value(), cipher, owner_id, identifier)
    engine = cast(Engine, request.app.state.engine)
    with connection_lock(engine, identifier) as acquired:
        if not acquired:
            raise HTTPException(409, "connection_busy")
        old_subject = account_guard(
            engine, identifier, owner_id, PROVIDER, login_revision=flow.get("revision")
        )
        try:
            config = AlbertConfig.from_settings(request.app.state.settings)
            account = confirm_verification(
                flow.get("phone", ""),
                flow.get("installation_id", ""),
                flow.get("login_id", ""),
                payload.code.get_secret_value(),
                config=config,
                clock_offset=flow.get("clock_offset", 0),
            )
            encrypted = cipher.encrypt(account.credential)
        except CredentialError:
            raise HTTPException(503, "credentials_unavailable") from None
        except AlbertError as error:
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
            account.label,
            login_revision=flow.get("revision"),
        )
