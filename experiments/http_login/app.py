"""Local-only, authenticated login probe. Run one worker on 127.0.0.1:18081."""

import asyncio
import hmac
import json
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from html import escape
from os import environ
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from .client import HttpLogin, LoginError, Status
from .hybrid import HybridLogin

APPLICATION = "http://localhost:18080"
BACKEND = environ.get("CARTALYTIX_HTTP_PROBE_BACKEND", APPLICATION)
HYBRID = environ.get("CARTALYTIX_HTTP_PROBE_HYBRID") == "1"
ORIGINS = {"http://localhost:18081", "http://127.0.0.1:18081"}
COOKIE = "cartalytix_session"
TTL = 600
CLEANUP_INTERVAL = 30


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
    async def expire() -> None:
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await asyncio.to_thread(cleanup)

    sweeper = asyncio.create_task(expire())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        with guard:
            for flow in flows.values():
                if flow.client is not None:
                    flow.client.close()
            flows.clear()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


@dataclass(repr=False)
class Flow:
    owner: str
    created: float = field(default_factory=time.monotonic)
    client: HttpLogin | None = None
    connection: UUID | None = None
    lock: Lock = field(default_factory=Lock)


flows: dict[str, Flow] = {}
guard = Lock()


class Start(BaseModel):
    csrf: SecretStr
    connection: UUID
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


class Verification(BaseModel):
    csrf: SecretStr
    code: SecretStr = Field(min_length=1, max_length=16)


class Check(BaseModel):
    model_config = {"extra": "forbid"}
    csrf: SecretStr


@app.middleware("http")
async def private_probe(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    if request.method != "GET" and request.headers.get("Origin") not in ORIGINS:
        return JSONResponse({"error": "origin_rejected"}, status_code=403)
    response = await call_next(request)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
        }
    )
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    return JSONResponse({"error": "invalid_request"}, status_code=422)


def application(
    request: Request, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    session = request.cookies.get(COOKIE)
    if not session:
        raise HTTPException(401, "Sign in to Cartalytix at localhost:18080 first")
    try:
        with httpx.Client(
            base_url=BACKEND,
            timeout=30,
            follow_redirects=False,
            cookies={COOKIE: session},
            headers={"Origin": APPLICATION},
        ) as http:
            response = http.request(method, f"/api/v1/{path}", json=payload)
    except httpx.HTTPError:
        raise HTTPException(503, "application_unavailable") from None
    if response.status_code not in {200, 201, 202}:
        raise HTTPException(response.status_code, "application_request_rejected")
    result = response.json()
    if not isinstance(result, dict):
        raise HTTPException(502, "application_response_invalid")
    return result


def cleanup() -> None:
    with guard:
        for key, flow in list(flows.items()):
            if time.monotonic() - flow.created <= TTL or not flow.lock.acquire(
                blocking=False
            ):
                continue
            try:
                if flow.client is not None:
                    flow.client.close()
                flows.pop(key, None)
            finally:
                flow.lock.release()


def active_flow(request: Request, csrf: SecretStr) -> Flow:
    cleanup()
    owner = str(application(request, "GET", "auth/me").get("id"))
    key = request.cookies.get("cartalytix_http_probe", "")
    with guard:
        flow = flows.get(key)
    if not key or not hmac.compare_digest(
        key.encode(), csrf.get_secret_value().encode()
    ):
        raise HTTPException(403, "csrf_rejected")
    if flow is None or flow.owner != owner:
        raise HTTPException(409, "probe_expired")
    return flow


def result(request: Request, flow: Flow, status: Status) -> dict[str, Any]:
    if status.step == "completed":
        assert flow.client is not None and flow.connection is not None
        refresh = flow.client.take_refresh_token()
        try:
            application(
                request,
                "POST",
                f"providers/lidl/connections/{flow.connection}/authorize",
                {"refresh_token": refresh},
            )
        finally:
            flow.client.close()
        print(json.dumps({"step": "completed", "linked": True}), flush=True)
        return {"step": "completed", "linked": True}
    print(json.dumps({"step": status.step}), flush=True)
    return {"step": status.step, "method": status.method}


def failed(flow: Flow, error: LoginError) -> dict[str, Any]:
    diagnostics = dict(flow.client.diagnostics) if flow.client else {}
    if flow.client:
        flow.client.close()
    output = {"step": "failed", "error": str(error), "diagnostics": diagnostics}
    print(json.dumps(output), flush=True)
    return output


@app.post("/check")
def check(payload: Check, request: Request) -> dict[str, Any]:
    flow = active_flow(request, payload.csrf)
    if not flow.lock.acquire(blocking=False):
        raise HTTPException(409, "probe_busy")
    try:
        if flow.client is not None:
            raise HTTPException(409, "probe_already_used_reload_page")
        client = HybridLogin() if HYBRID else HttpLogin()
        try:
            if client.begin().step != "password":
                raise LoginError("unexpected_initial_step")
            output = {
                "step": "ready",
                "diagnostics": dict(client.diagnostics),
                "credentials_submitted": False,
            }
            print(json.dumps(output), flush=True)
            return output
        except LoginError as error:
            return failed(Flow(flow.owner, client=client), error)
        finally:
            client.close()
    finally:
        flow.lock.release()


@app.post("/start")
def start(payload: Start, request: Request) -> dict[str, Any]:
    flow = active_flow(request, payload.csrf)
    if not flow.lock.acquire(blocking=False):
        raise HTTPException(409, "probe_busy")
    try:
        if flow.client is not None:
            raise HTTPException(409, "probe_already_used_reload_page")
        accounts = application(request, "GET", "connections").get("items", [])
        if not any(
            account.get("id") == str(payload.connection)
            and account.get("provider") == "lidl"
            for account in accounts
        ):
            raise HTTPException(404, "connection_not_found")
        flow.connection = payload.connection
        flow.client = HybridLogin() if HYBRID else HttpLogin()
        try:
            status = flow.client.begin()
            if status.step != "password":
                raise LoginError("unexpected_initial_step")
            status = flow.client.credentials(
                payload.email, payload.password.get_secret_value()
            )
            return result(request, flow, status)
        except LoginError as error:
            return failed(flow, error)
    finally:
        flow.lock.release()


@app.post("/verify")
def verify(payload: Verification, request: Request) -> dict[str, Any]:
    flow = active_flow(request, payload.csrf)
    if not flow.lock.acquire(blocking=False):
        raise HTTPException(409, "probe_busy")
    try:
        if flow.client is None:
            raise HTTPException(409, "probe_not_started")
        try:
            return result(
                request, flow, flow.client.verify(payload.code.get_secret_value())
            )
        except LoginError as error:
            return failed(flow, error)
    finally:
        flow.lock.release()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    cleanup()
    owner = str(application(request, "GET", "auth/me").get("id"))
    accounts = application(request, "GET", "connections").get("items", [])
    options = "".join(
        f'<option value="{escape(str(row.get("id")), quote=True)}">'
        f"{escape(str(row.get('name')))}</option>"
        for row in accounts
        if row.get("provider") == "lidl"
    )
    with guard:
        if len(flows) >= 16:
            raise HTTPException(429, "too_many_probe_sessions")
        key = secrets.token_urlsafe(32)
        flows[key] = Flow(owner)
    response = HTMLResponse(
        PAGE.replace("__CSRF__", key)
        .replace("__OPTIONS__", options)
        .replace(
            "__MODE__", "Hybrid login experiment" if HYBRID else "HTTP login experiment"
        )
        .replace(
            "__INITIALIZATION__",
            (
                "Headless Chromium initializes device verification and "
                "reCAPTCHA, then closes. "
                "It repeats reCAPTCHA initialization for verification. "
                "Password and verification are submitted by the HTTP client."
                if HYBRID
                else "Local HTTP test without Chrome."
            ),
        )
    )
    response.set_cookie(
        "cartalytix_http_probe", key, httponly=True, samesite="strict", max_age=TTL
    )
    return response


PAGE = Path(__file__).with_name("page.html").read_text(encoding="utf-8")
