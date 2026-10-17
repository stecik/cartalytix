from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cartalytix.dependencies import COOKIE_NAME, Db, Owner
from cartalytix.i18n import translate
from cartalytix.lidl_routes import connection_outputs
from cartalytix.lidl_routes import router as lidl_router
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, User, UserSession
from cartalytix.schemas import (
    ConnectionsOutput,
    CurrencyTotal,
    DiscountOutput,
    LoginInput,
    OverviewOutput,
    ProviderOutput,
    ProvidersOutput,
    ReceiptDetailOutput,
    ReceiptItemOutput,
    ReceiptOutput,
    ReceiptsOutput,
    UserOutput,
)
from cartalytix.security import token_hash, verify_password
from cartalytix.settings import Settings

PROVIDERS = (
    ("lidl", "Lidl"),
    ("albert", "Albert"),
    ("globus", "Globus"),
    ("rohlik", "Rohlik"),
    ("kosik", "Kosik"),
    ("billa", "Billa"),
)


class LoginLimiter(Protocol):
    def check(self, address: str, email: str) -> bool: ...


class RedisLoginLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    def check(self, address: str, email: str) -> bool:
        # Identities are hashed; proxy headers are accepted only from configured peers.
        keys = [f"login:ip:{token_hash(address)}", f"login:email:{token_hash(email)}"]
        script = """
        local allowed = 1
        for _, key in ipairs(KEYS) do
            local attempts = redis.call('INCR', key)
            if attempts == 1 then redis.call('EXPIRE', key, ARGV[1]) end
            if attempts > tonumber(ARGV[2]) then allowed = 0 end
        end
        return allowed
        """
        return bool(
            self.redis.eval(
                script,
                len(keys),
                *keys,
                str(self.settings.login_window_seconds),
                str(self.settings.login_attempts),
            )
        )


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    locale = request.headers.get("Accept-Language", "en")
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": translate(message, locale),
                "params": {},
            }
        },
        headers={"Cache-Control": "no-store"},
    )


def receipt_output(receipt: Receipt, provider: str) -> ReceiptOutput:
    return ReceiptOutput(
        id=receipt.id,
        connection_id=receipt.connection_id,
        provider=provider,
        external_id=receipt.external_id,
        purchased_at=receipt.purchased_at,
        merchant=receipt.merchant,
        total_amount=format(receipt.total_amount, ".2f"),
        currency=receipt.currency,
    )


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    limiter: LoginLimiter | None = None,
) -> FastAPI:
    config = settings or Settings()
    database = (
        engine if engine is not None else create_engine(config.database_url, pool_pre_ping=True)
    )
    redis = Redis.from_url(config.redis_url, socket_connect_timeout=2, socket_timeout=2)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        redis.close()
        if engine is None:
            database.dispose()

    app = FastAPI(title="Cartalytix", version="0.1.0", lifespan=lifespan)
    if config.trusted_proxy_hosts:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=config.trusted_proxy_hosts)
    app.state.engine = database
    app.state.redis = redis
    app.state.settings = config
    app.state.limiter = limiter or RedisLoginLimiter(redis, config)
    app.include_router(lidl_router)

    @app.middleware("http")
    async def browser_security(
        request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("Origin") not in config.allowed_origins
        ):
            return error_response(request, 403, "origin_rejected", "Request origin rejected")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        messages = {
            "authentication_required": "Authentication required",
            "invalid_credentials": "Invalid email or password",
            "login_rate_limited": "Too many login attempts",
            "service_unavailable": "Service temporarily unavailable",
        }
        code = str(exc.detail)
        return error_response(request, exc.status_code, code, messages.get(code, "Request failed"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "validation_error", "Invalid request")

    @app.get("/api/v1/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health/ready")
    def ready(request: Request, db: Db) -> dict[str, str]:
        try:
            db.execute(text("SELECT 1"))
            request.app.state.redis.ping()
        except (SQLAlchemyError, RedisError) as exc:
            raise HTTPException(503, "service_unavailable") from exc
        return {"status": "ok"}

    @app.post("/api/v1/auth/login", response_model=UserOutput)
    def login(payload: LoginInput, request: Request, response: Response, db: Db) -> User:
        email = str(payload.email).strip().casefold()
        try:
            allowed = request.app.state.limiter.check(
                request.client.host if request.client else "unknown",
                email,
            )
        except RedisError as exc:
            raise HTTPException(503, "service_unavailable") from exc
        if not allowed:
            raise HTTPException(429, "login_rate_limited")
        user = db.scalar(select(User).where(User.email == email))
        if not verify_password(user.password_hash if user else None, payload.password):
            raise HTTPException(401, "invalid_credentials")
        assert user is not None
        token = token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(hours=config.session_hours)
        old_token = request.cookies.get(COOKIE_NAME)
        if old_token:
            db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old_token)))
        db.execute(
            delete(UserSession).where(
                UserSession.owner_id == user.id, UserSession.expires_at <= datetime.now(UTC)
            )
        )
        db.add(UserSession(token_hash=token_hash(token), owner_id=user.id, expires_at=expires))
        db.commit()
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=config.session_hours * 3600,
            httponly=True,
            secure=config.cookie_secure,
            samesite="lax",
            path="/",
        )
        return user

    @app.get("/api/v1/auth/me", response_model=UserOutput)
    def me(user: Owner) -> User:
        return user

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, db: Db) -> None:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))
            db.commit()
        response.delete_cookie(
            COOKIE_NAME, path="/", secure=config.cookie_secure, httponly=True, samesite="lax"
        )

    @app.get("/api/v1/providers", response_model=ProvidersOutput)
    def providers() -> ProvidersOutput:
        return ProvidersOutput(
            items=[
                ProviderOutput(
                    code=code,
                    name=name,
                    status="available"
                    if code == "lidl" and config.token_key_file
                    else "not_implemented",
                )
                for code, name in PROVIDERS
            ]
        )

    @app.get("/api/v1/connections", response_model=ConnectionsOutput)
    def connections(user: Owner, db: Db) -> ConnectionsOutput:
        rows = list(
            db.scalars(
                select(ProviderConnection)
                .where(
                    ProviderConnection.owner_id == user.id,
                )
                .order_by(ProviderConnection.name, ProviderConnection.id)
                .limit(100)
            )
        )
        return ConnectionsOutput(items=connection_outputs(db, rows))

    @app.get("/api/v1/receipts", response_model=ReceiptsOutput)
    def receipts(
        user: Owner,
        db: Db,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        connection_id: UUID | None = None,
    ) -> ReceiptsOutput:
        filters = [ProviderConnection.owner_id == user.id]
        if connection_id is not None:
            filters.append(Receipt.connection_id == connection_id)
        total = (
            db.scalar(
                select(func.count())
                .select_from(Receipt)
                .join(ProviderConnection)
                .where(
                    *filters,
                )
            )
            or 0
        )
        rows = db.execute(
            select(Receipt, ProviderConnection.provider)
            .join(ProviderConnection)
            .where(
                *filters,
            )
            .order_by(Receipt.purchased_at.desc(), Receipt.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return ReceiptsOutput(
            items=[receipt_output(receipt, provider) for receipt, provider in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/receipts/{receipt_id}", response_model=ReceiptDetailOutput)
    def receipt_detail(receipt_id: UUID, user: Owner, db: Db) -> ReceiptDetailOutput:
        row = db.execute(
            select(Receipt, ProviderConnection.provider)
            .join(ProviderConnection)
            .where(
                Receipt.id == receipt_id,
                ProviderConnection.owner_id == user.id,
            )
        ).first()
        if row is None:
            raise HTTPException(404, "receipt_not_found")
        receipt, provider = row
        items = db.scalars(
            select(ReceiptItem)
            .where(ReceiptItem.receipt_id == receipt.id)
            .order_by(ReceiptItem.position)
        )
        discounts = db.scalars(
            select(Discount).where(Discount.receipt_id == receipt.id).order_by(Discount.id)
        )
        return ReceiptDetailOutput(
            **receipt_output(receipt, provider).model_dump(),
            rounding_amount=receipt.rounding_amount,
            quality_issues=receipt.quality_issues,
            source_format=receipt.source_format,
            items=[ReceiptItemOutput.model_validate(item) for item in items],
            discounts=[DiscountOutput.model_validate(discount) for discount in discounts],
        )

    @app.get("/api/v1/overview", response_model=OverviewOutput)
    def overview(user: Owner, db: Db) -> OverviewOutput:
        owner_filter = ProviderConnection.owner_id == user.id
        receipt_count = (
            db.scalar(
                select(func.count())
                .select_from(Receipt)
                .join(
                    ProviderConnection,
                )
                .where(owner_filter)
            )
            or 0
        )
        connection_count = (
            db.scalar(
                select(func.count())
                .select_from(ProviderConnection)
                .where(
                    owner_filter,
                )
            )
            or 0
        )
        totals = db.execute(
            select(Receipt.currency, func.sum(Receipt.total_amount))
            .join(
                ProviderConnection,
            )
            .where(owner_filter)
            .group_by(Receipt.currency)
            .order_by(Receipt.currency)
        )
        latest = db.execute(
            select(Receipt, ProviderConnection.provider)
            .join(ProviderConnection)
            .where(
                owner_filter,
            )
            .order_by(Receipt.purchased_at.desc(), Receipt.id.desc())
            .limit(5)
        )
        return OverviewOutput(
            receipt_count=receipt_count,
            connection_count=connection_count,
            totals=[
                CurrencyTotal(currency=currency, amount=format(amount, ".2f"))
                for currency, amount in totals
            ],
            latest_receipts=[receipt_output(receipt, provider) for receipt, provider in latest],
        )

    return app


app = create_app()
