import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, nullcontext, suppress
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from secrets import token_urlsafe
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, create_engine, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cartalytix.admin_routes import bootstrap_admin
from cartalytix.admin_routes import router as admin_router
from cartalytix.bolt_routes import router as bolt_router
from cartalytix.category_routes import router as category_router
from cartalytix.change_routes import router as change_router
from cartalytix.connection_routes import connection_outputs, next_sync
from cartalytix.connection_routes import router as connection_router
from cartalytix.dependencies import (
    COOKIE_NAME,
    ActiveUser,
    Db,
    Owner,
    SessionUser,
    set_session_cookie,
)
from cartalytix.diagnostics import HTTP_ERROR_CODES, capture_error
from cartalytix.diagnostics_routes import router as diagnostics_router
from cartalytix.foodora_routes import router as foodora_router
from cartalytix.globus_routes import router as globus_router
from cartalytix.i18n import translate
from cartalytix.item_routes import router as item_router
from cartalytix.kaufland_routes import router as kaufland_router
from cartalytix.lidl_routes import router as lidl_router
from cartalytix.manual_receipt_routes import router as manual_receipt_router
from cartalytix.mcp_server import create_mcp
from cartalytix.mixit_routes import router as mixit_router
from cartalytix.models import ProviderConnection, User, UserSession
from cartalytix.oauth import router as oauth_router
from cartalytix.providers.foodora.login_sessions import LoginSessions as FoodoraLoginSessions
from cartalytix.providers.kaufland.client import KauflandLogin
from cartalytix.providers.lidl.login_sessions import LoginSessions
from cartalytix.receipt_routes import MANUAL_PROVIDER
from cartalytix.receipt_routes import router as receipt_router
from cartalytix.schemas import (
    ConnectionsOutput,
    CredentialsInput,
    LoginInput,
    ProviderCode,
    ProviderOutput,
    ProvidersOutput,
    UserOutput,
    UserSettingsInput,
)
from cartalytix.search_routes import router as search_router
from cartalytix.security import (
    PASSWORD_RULE,
    hash_password,
    is_strong_password,
    token_hash,
    verify_password,
)
from cartalytix.settings import Settings
from cartalytix.shoptet_routes import router as shoptet_router
from cartalytix.stats_routes import router as stats_router
from cartalytix.wolt_routes import router as wolt_router

PROVIDERS = (
    ("lidl", "Lidl"),
    ("mixit", "Mixit"),
    ("fermato", "Fermato"),
    ("nejkafe", "Nejkafe"),
    ("bolt", "Bolt Food"),
    ("wolt", "Wolt"),
    ("kaufland", "Kaufland"),
    ("foodora", "Foodora"),
    ("albert", "Albert"),
    ("globus", "Globus"),
    ("rohlik", "Rohlik"),
    ("kosik", "Kosik"),
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


def error_response(
    request: Request, status: int, code: str, message: str, params: dict[str, Any] | None = None
) -> JSONResponse:
    locale = request.headers.get("Accept-Language", "en")
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": translate(message, locale),
                "params": params or {},
            }
        },
        headers={"Cache-Control": "no-store"},
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
    lidl_logins = LoginSessions()
    kaufland_logins = LoginSessions(factory=KauflandLogin)
    foodora_logins = FoodoraLoginSessions()
    mcp = create_mcp(database, config) if config.public_url is not None else None
    # Bearer-authenticated endpoints called by MCP clients, not by the cookie-based web app.
    token_paths = {"/mcp", *(f"/api/v1/oauth/{name}" for name in ("token", "register", "revoke"))}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        async def expire_logins() -> None:
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(lidl_logins.cleanup)
                await asyncio.to_thread(kaufland_logins.cleanup)
                await asyncio.to_thread(foodora_logins.cleanup)

        sweeper = asyncio.create_task(expire_logins())
        try:
            await asyncio.to_thread(bootstrap_admin, database, config.bootstrap_password)
            async with mcp.server.session_manager.run() if mcp else nullcontext():
                yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper
            _app.state.lidl_logins.close()
            _app.state.kaufland_logins.close()
            _app.state.foodora_logins.close()
            redis.close()
            if engine is None:
                database.dispose()

    app = FastAPI(title="Cartalytix", version=version("cartalytix"), lifespan=lifespan)
    if config.trusted_proxy_hosts:
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=config.trusted_proxy_hosts)
    app.state.engine = database
    app.state.redis = redis
    app.state.settings = config
    app.state.lidl_logins = lidl_logins
    app.state.kaufland_logins = kaufland_logins
    app.state.foodora_logins = foodora_logins
    app.state.limiter = limiter or RedisLoginLimiter(redis, config)
    app.state.mcp_urls = mcp.urls if mcp else None
    app.include_router(connection_router)
    app.include_router(lidl_router)
    app.include_router(mixit_router)
    app.include_router(bolt_router)
    app.include_router(wolt_router)
    app.include_router(kaufland_router)
    app.include_router(globus_router)
    app.include_router(foodora_router)
    app.include_router(shoptet_router)
    app.include_router(stats_router)
    app.include_router(search_router)
    app.include_router(manual_receipt_router)
    app.include_router(receipt_router)
    app.include_router(change_router)
    app.include_router(item_router)
    app.include_router(category_router)
    app.include_router(oauth_router)
    app.include_router(admin_router)
    app.include_router(diagnostics_router)
    if mcp:
        app.router.routes.extend(mcp.routes)

    @app.middleware("http")
    async def browser_security(
        request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.url.path not in token_paths
            and request.headers.get("Origin") not in config.allowed_origins
        ):
            return error_response(request, 403, "origin_rejected", "Request origin rejected")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            await asyncio.to_thread(
                capture_error,
                database,
                getattr(request.state, "diagnostic_owner_id", None),
                "api",
                exc.detail
                if isinstance(exc.detail, str) and exc.detail in HTTP_ERROR_CODES
                else "http_error",
                route=getattr(request.scope.get("route"), "path", None),
                status=exc.status_code,
            )
        messages = {
            "authentication_required": "Authentication required",
            "invalid_credentials": "Invalid email or password",
            "login_rate_limited": "Too many login attempts",
            "service_unavailable": "Service temporarily unavailable",
            "account_disabled": "This account is disabled",
            "password_change_required": "Change your password to continue",
            "invalid_current_password": "Current password is incorrect",
            "password_unchanged": "Choose a password different from the current one",
            "weak_password": PASSWORD_RULE,
            "email_change_required": "Choose a new email address",
            "email_taken": "An account with this email already exists",
            "credentials_unchanged": "Change the email address or the password",
            "admin_forbidden": "Administrators manage accounts only",
            "admin_required": "Administrator access required",
            "admin_protected": "Administrator accounts cannot be changed here",
            "user_not_found": "Account not found",
        }
        # A dict detail carries its code with parameters, e.g. the failing batch operation.
        params = dict(exc.detail) if isinstance(exc.detail, dict) else {}
        code = str(params.pop("code", exc.detail))
        return error_response(
            request, exc.status_code, code, messages.get(code, "Request failed"), params
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        await asyncio.to_thread(
            capture_error,
            database,
            getattr(request.state, "diagnostic_owner_id", None),
            "api",
            "internal_error",
            error=exc,
            route=getattr(request.scope.get("route"), "path", None),
            status=500,
        )
        return error_response(request, 500, "internal_error", "Request failed")

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

    def limit_password_attempts(request: Request, email: str) -> None:
        try:
            allowed = request.app.state.limiter.check(
                request.client.host if request.client else "unknown",
                email,
            )
        except RedisError as exc:
            raise HTTPException(503, "service_unavailable") from exc
        if not allowed:
            raise HTTPException(429, "login_rate_limited")

    @app.post("/api/v1/auth/login", response_model=UserOutput)
    def login(payload: LoginInput, request: Request, response: Response, db: Db) -> User:
        email = str(payload.email).strip().casefold()
        limit_password_attempts(request, email)
        user = db.scalar(select(User).where(User.email == email))
        if not verify_password(user.password_hash if user else None, payload.password):
            raise HTTPException(401, "invalid_credentials")
        assert user is not None
        if user.is_disabled:
            raise HTTPException(403, "account_disabled")
        token = token_urlsafe(32)
        lifetime = (
            timedelta(days=config.remember_days)
            if payload.remember
            else timedelta(hours=config.session_hours)
        )
        expires = datetime.now(UTC) + lifetime
        old_token = request.cookies.get(COOKIE_NAME)
        if old_token:
            db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old_token)))
        db.execute(
            delete(UserSession).where(
                UserSession.owner_id == user.id, UserSession.expires_at <= datetime.now(UTC)
            )
        )
        db.add(
            UserSession(
                token_hash=token_hash(token),
                owner_id=user.id,
                expires_at=expires,
                remember=payload.remember,
            )
        )
        db.commit()
        set_session_cookie(response, token, config, lifetime if payload.remember else None)
        return user

    @app.get("/api/v1/auth/me", response_model=UserOutput)
    def me(user: SessionUser) -> User:
        return user

    @app.post("/api/v1/auth/credentials", response_model=UserOutput)
    def change_credentials(
        payload: CredentialsInput, request: Request, user: SessionUser, db: Db
    ) -> User:
        limit_password_attempts(request, user.email)
        # A forced change follows a fresh sign-in with the issued password, so it is not re-asked.
        if not user.must_change_password and not verify_password(
            user.password_hash, payload.current_password or ""
        ):
            raise HTTPException(403, "invalid_current_password")
        new_password = payload.new_password
        if new_password is None and user.must_change_password:
            raise HTTPException(422, "weak_password")
        if new_password is not None:
            if not is_strong_password(new_password):
                raise HTTPException(422, "weak_password")
            if verify_password(user.password_hash, new_password):
                raise HTTPException(422, "password_unchanged")
        email = str(payload.email).strip().casefold() if payload.email else user.email
        # The first-start administrator must replace the well-known e-mail as well.
        if user.is_admin and user.must_change_password and email == user.email:
            raise HTTPException(422, "email_change_required")
        if new_password is None and email == user.email:
            raise HTTPException(422, "credentials_unchanged")
        if email != user.email:
            if db.scalar(select(User.id).where(User.email == email)) is not None:
                raise HTTPException(409, "email_taken")
            user.email = email
        if new_password is not None:
            user.password_hash = hash_password(new_password)
        user.must_change_password = False
        # Other devices signed in with the old password are signed out.
        current = token_hash(request.cookies.get(COOKIE_NAME, ""))
        db.execute(
            delete(UserSession).where(
                UserSession.owner_id == user.id, UserSession.token_hash != current
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "email_taken") from None
        return user

    @app.patch("/api/v1/auth/me", response_model=UserOutput)
    def update_me(payload: UserSettingsInput, user: ActiveUser, db: Db) -> User:
        if payload.locale is not None:
            user.locale = payload.locale
        if payload.receipt_view is not None:
            user.receipt_view = payload.receipt_view
        if payload.theme is not None:
            user.theme = payload.theme
        if payload.dashboard_period is not None:
            user.dashboard_period = payload.dashboard_period
        if payload.dashboard_category_slices is not None:
            user.dashboard_category_slices = payload.dashboard_category_slices
        if payload.cycle_start_day is not None:
            user.cycle_start_day = payload.cycle_start_day
        if payload.cycle_weekend_shift is not None:
            user.cycle_weekend_shift = payload.cycle_weekend_shift
        if "default_sync_interval_minutes" in payload.model_fields_set:
            user.default_sync_interval_minutes = payload.default_sync_interval_minutes
        db.commit()
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
    def providers(db: Db) -> ProvidersOutput:
        counts: dict[str, int] = dict(
            db.execute(
                select(ProviderConnection.provider, func.count()).group_by(
                    ProviderConnection.provider
                )
            ).all()
        )
        return ProvidersOutput(
            items=[
                ProviderOutput(
                    code=code,
                    name=name,
                    status="available"
                    if code in ProviderCode and config.token_key_file
                    else "not_implemented",
                    connection_count=counts.get(code, 0),
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
                    ProviderConnection.provider != MANUAL_PROVIDER,
                )
                .order_by(ProviderConnection.name, ProviderConnection.id)
                .limit(100)
            )
        )
        return ConnectionsOutput(items=connection_outputs(db, rows))

    @app.post("/api/v1/connections/apply-default-sync", status_code=204)
    def apply_default_sync(user: Owner, db: Db) -> None:
        interval = user.default_sync_interval_minutes
        db.execute(
            update(ProviderConnection)
            .where(
                ProviderConnection.owner_id == user.id,
                ProviderConnection.provider != MANUAL_PROVIDER,
            )
            .values(sync_interval_minutes=interval, next_sync_at=next_sync(interval))
        )
        db.commit()

    return app


app = create_app()
