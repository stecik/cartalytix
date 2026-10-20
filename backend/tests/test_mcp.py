import base64
import hashlib
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx2
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from mcp import Client
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from mcp.types import CallToolResult
from pydantic import AnyUrl
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session
from test_api import UnlimitedLimiter
from test_api import client as client
from test_api import engine as engine

from cartalytix.category_routes import assign_category_items
from cartalytix.category_schemas import CategoryAssignmentInput
from cartalytix.main import create_app
from cartalytix.models import (
    OAuthClient,
    OAuthCode,
    OAuthGrant,
    ProviderConnection,
    Receipt,
    ReceiptItem,
    User,
)
from cartalytix.models import (
    OAuthToken as OAuthTokenRow,
)
from cartalytix.oauth import SCOPE
from cartalytix.security import token_hash
from cartalytix.settings import Settings

PUBLIC = "http://localhost:8080"
RESOURCE = f"{PUBLIC}/mcp"
ISSUER = f"{PUBLIC}/api/v1/oauth"
ORIGIN = {"Origin": PUBLIC}
CODEX_REDIRECT = "http://127.0.0.1:47123/callback"
CHATGPT_REDIRECT = "https://chatgpt.com/connector/oauth/abc_123"
OWNER = ("owner@example.com", "Correct-horse-battery")
OTHER = ("other@example.com", "Other-secret-password")
TOOLS = {
    "search_receipts",
    "get_receipt",
    "search_items",
    "get_item_price_history",
    "get_spending_overview",
    "get_spending_stats",
    "list_categories",
    "create_category",
    "update_category",
    "delete_category",
    "list_category_items",
    "assign_category_items",
}
CATEGORY_WRITES = {"create_category", "update_category", "delete_category", "assign_category_items"}
CATEGORY_WRITE_SCOPE = "cartalytix:categories:write"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mcp_app(client: TestClient, engine: Engine, tmp_path: Path) -> FastAPI:
    with Session(engine) as db:
        for receipt in db.scalars(select(Receipt).join(ProviderConnection)):
            db.add(
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=0,
                    name="Secret" if receipt.total_amount == Decimal("999.00") else "Rohlík",
                    quantity=Decimal("2.5"),
                    unit="ks",
                    unit_price=Decimal("1.333333"),
                    total_amount=Decimal("3.33"),
                )
            )
        db.commit()
    key = tmp_path / "token.key"
    key.write_bytes(Fernet.generate_key())
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        cookie_secure=False,
        allowed_origins=[PUBLIC],
        token_key_file=str(key),
        public_url=PUBLIC,
    )
    return create_app(settings, engine=engine, limiter=UnlimitedLimiter())


@pytest.fixture
def web(mcp_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(mcp_app, base_url=PUBLIC) as test_client:
        yield test_client


def pkce() -> tuple[str, str]:
    verifier = token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def query(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


def login(web: TestClient, account: tuple[str, str]) -> None:
    web.cookies.clear()
    email, password = account
    response = web.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200


def register(web: TestClient, redirect: str = CODEX_REDIRECT, **extra: str) -> dict[str, Any]:
    response = web.post(
        "/api/v1/oauth/register",
        json={
            "redirect_uris": [redirect],
            "client_name": "Codex",
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **extra,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def authorize(
    web: TestClient, registered: dict[str, Any], challenge: str, **params: str
) -> Response:
    return web.get(
        "/api/v1/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": registered.get("client_id"),
            "redirect_uri": CODEX_REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "state-1",
            "resource": RESOURCE,
            "scope": SCOPE,
            **params,
        },
        follow_redirects=False,
    )


def consent(web: TestClient, location: str, approve: bool = True) -> dict[str, str]:
    assert location.startswith(f"{PUBLIC}/oauth/consent?request=")
    response = web.post(
        f"/api/v1/mcp/authorizations/{query(location).get('request')}",
        json={"approve": approve},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return query(response.json().get("redirect_url"))


def code_for(
    web: TestClient, account: tuple[str, str], registered: dict[str, Any], **params: str
) -> tuple[str, str]:
    login(web, account)
    verifier, challenge = pkce()
    response = authorize(web, registered, challenge, **params)
    assert response.status_code == 302
    result = consent(web, response.headers.get("location", ""))
    assert result.get("state") == "state-1"
    return result.get("code", ""), verifier


def exchange(
    web: TestClient, registered: dict[str, Any], code: str, verifier: str, **params: str
) -> Response:
    return web.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": registered.get("client_id"),
            "redirect_uri": CODEX_REDIRECT,
            "resource": RESOURCE,
            **params,
        },
    )


def tokens_for(web: TestClient, account: tuple[str, str], **extra: str) -> dict[str, Any]:
    registered = register(web, **extra)
    code, verifier = code_for(web, account, registered)
    response = exchange(web, registered, code, verifier)
    assert response.status_code == 200, response.text
    return {**response.json(), "client_id": registered.get("client_id")}


def rpc(web: TestClient, token: str | None) -> Response:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return web.post("/mcp", json=INITIALIZE, headers=headers)


def test_discovery_metadata(web: TestClient) -> None:
    for path in [
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-protected-resource",
    ]:
        resource = web.get(path).json()
        assert resource.get("resource") == RESOURCE
        assert resource.get("authorization_servers") == [ISSUER]
        assert resource.get("scopes_supported") == [SCOPE, CATEGORY_WRITE_SCOPE]
    for path in [
        "/.well-known/oauth-authorization-server/api/v1/oauth",
        "/.well-known/oauth-authorization-server",
    ]:
        metadata = web.get(path).json()
        assert metadata.get("issuer") == ISSUER
        assert metadata.get("authorization_endpoint") == f"{ISSUER}/authorize"
        assert metadata.get("token_endpoint") == f"{ISSUER}/token"
        assert metadata.get("registration_endpoint") == f"{ISSUER}/register"
        assert metadata.get("revocation_endpoint") == f"{ISSUER}/revoke"
        assert metadata.get("code_challenge_methods_supported") == ["S256"]
        assert set(metadata.get("scopes_supported")) == {
            SCOPE,
            "offline_access",
            CATEGORY_WRITE_SCOPE,
        }
    missing = rpc(web, None)
    assert missing.status_code == 401
    assert (
        f'resource_metadata="{PUBLIC}/.well-known/oauth-protected-resource/mcp"'
        in missing.headers.get("www-authenticate", "")
    )


def test_untrusted_host_and_disabled_connector(web: TestClient, client: TestClient) -> None:
    token = tokens_for(web, OWNER).get("access_token")
    assert rpc(web, token).status_code == 200
    spoofed = web.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            "Host": "evil.example",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert spoofed.status_code == 421
    assert client.post("/mcp", json=INITIALIZE).status_code in {403, 404}
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404


@pytest.mark.parametrize(
    "redirect",
    [
        "https://evil.example/callback",
        "http://evil.example/callback",
        "https://chatgpt.com/connector/oauth/abc?next=https://evil.example",
        "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
        "https://chatgpt.com/connector/oauth/../../evil",
    ],
)
def test_registration_rejects_foreign_redirects(web: TestClient, redirect: str) -> None:
    response = web.post(
        "/api/v1/oauth/register",
        json={
            "redirect_uris": [redirect],
            "client_name": "Evil",
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 400
    assert response.json().get("error") == "invalid_redirect_uri"


def test_chatgpt_confidential_client_flow(web: TestClient) -> None:
    for redirect in [CHATGPT_REDIRECT, "https://chatgpt.com/connector_platform_oauth_redirect"]:
        registered = register(web, redirect, token_endpoint_auth_method="client_secret_post")
        assert registered.get("client_secret")
    login(web, OWNER)
    verifier, challenge = pkce()
    response = authorize(web, registered, challenge, redirect_uri=registered["redirect_uris"][0])
    code = consent(web, response.headers.get("location", "")).get("code", "")
    request = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": registered.get("client_id"),
        "redirect_uri": registered["redirect_uris"][0],
        "resource": RESOURCE,
    }
    assert web.post("/api/v1/oauth/token", data=request).status_code == 401
    wrong = {**request, "client_secret": "wrong"}
    assert web.post("/api/v1/oauth/token", data=wrong).status_code == 401
    issued = web.post(
        "/api/v1/oauth/token", data={**request, "client_secret": registered.get("client_secret")}
    )
    assert issued.status_code == 200
    assert rpc(web, issued.json().get("access_token")).status_code == 200


def test_authorization_request_validation(web: TestClient) -> None:
    registered = register(web)
    login(web, OWNER)
    _verifier, challenge = pkce()
    foreign = authorize(web, registered, challenge, redirect_uri="http://127.0.0.1:1/other")
    assert foreign.status_code == 400
    plain = authorize(web, registered, challenge, code_challenge_method="plain")
    assert query(plain.headers.get("location", "")).get("error") == "invalid_request"
    target = authorize(web, registered, challenge, resource="https://evil.example/mcp")
    assert query(target.headers.get("location", "")) == {
        "error": "invalid_target",
        "error_description": "Unknown resource",
        "state": "state-1",
    }
    scope = authorize(web, registered, challenge, scope="admin")
    assert query(scope.headers.get("location", "")).get("error") == "invalid_scope"
    unknown = authorize(web, {"client_id": "missing"}, challenge)
    assert unknown.status_code == 400
    pending = authorize(web, registered, challenge).headers.get("location", "")
    request = query(pending).get("request")
    web.cookies.clear()
    assert web.get(f"/api/v1/mcp/authorizations/{request}").status_code == 401
    assert (
        web.post(
            f"/api/v1/mcp/authorizations/{request}", json={"approve": True}, headers=ORIGIN
        ).status_code
        == 401
    )
    login(web, OWNER)
    details = web.get(f"/api/v1/mcp/authorizations/{request}").json()
    assert details == {"client_name": "Codex", "redirect_host": "127.0.0.1", "scopes": [SCOPE]}
    denied = consent(web, pending, approve=False)
    assert denied == {"error": "access_denied", "state": "state-1"}
    reused = web.post(
        f"/api/v1/mcp/authorizations/{request}", json={"approve": True}, headers=ORIGIN
    )
    assert reused.status_code == 404


def test_code_exchange_rules(web: TestClient, engine: Engine) -> None:
    registered = register(web)
    other_client = register(web)
    code, verifier = code_for(web, OWNER, registered)
    wrong_verifier = exchange(web, registered, code, pkce()[0])
    assert wrong_verifier.json().get("error") == "invalid_grant"
    missing_verifier = exchange(web, registered, code, "")
    assert missing_verifier.status_code == 400
    assert exchange(web, other_client, code, verifier).json().get("error") == "invalid_grant"
    target = exchange(web, registered, code, verifier, resource="https://evil.example/mcp")
    assert target.json().get("error") == "invalid_target"
    redirect = exchange(web, registered, code, verifier, redirect_uri="http://127.0.0.1:1/x")
    assert redirect.json().get("error") == "invalid_request"
    issued = exchange(web, registered, code, verifier)
    assert issued.status_code == 200
    body = issued.json()
    assert body.get("token_type") == "Bearer"
    assert body.get("expires_in") == 3600
    assert issued.headers.get("cache-control") == "no-store"
    assert rpc(web, body.get("access_token")).status_code == 200
    replay = exchange(web, registered, code, verifier)
    assert replay.json().get("error") == "invalid_grant"
    # Replaying a code revokes everything it produced.
    assert rpc(web, body.get("access_token")).status_code == 401
    refresh = web.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": body.get("refresh_token"),
            "client_id": registered.get("client_id"),
        },
    )
    assert refresh.json().get("error") == "invalid_grant"
    expired_code, expired_verifier = code_for(web, OWNER, registered)
    with Session(engine) as db:
        db.execute(
            update(OAuthCode)
            .where(OAuthCode.token_hash == token_hash(expired_code))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.commit()
    expired = exchange(web, registered, expired_code, expired_verifier)
    assert expired.json().get("error") == "invalid_grant"


def test_token_validation(web: TestClient, engine: Engine) -> None:
    assert rpc(web, None).status_code == 401
    assert rpc(web, "not-a-token").status_code == 401
    expired = tokens_for(web, OWNER).get("access_token", "")
    wrong_audience = tokens_for(web, OWNER).get("access_token", "")
    refresh_as_access = tokens_for(web, OWNER).get("refresh_token", "")
    with Session(engine) as db:
        db.execute(
            update(OAuthTokenRow)
            .where(OAuthTokenRow.token_hash == token_hash(expired))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        db.execute(
            update(OAuthTokenRow)
            .where(OAuthTokenRow.token_hash == token_hash(wrong_audience))
            .values(resource="https://other.example/mcp")
        )
        db.commit()
    for token in [expired, wrong_audience, refresh_as_access]:
        response = rpc(web, token)
        assert response.status_code == 401
        assert 'error="invalid_token"' in response.headers.get("www-authenticate", "")
    no_scope = tokens_for(web, OWNER, scope=f"{SCOPE} offline_access")
    registered = {"client_id": no_scope.get("client_id")}
    code, verifier = code_for(web, OWNER, registered, scope="offline_access")
    limited = exchange(web, registered, code, verifier).json()
    assert limited.get("scope") == "offline_access"
    forbidden = rpc(web, limited.get("access_token"))
    assert forbidden.status_code == 403
    assert 'error="insufficient_scope"' in forbidden.headers.get("www-authenticate", "")


def test_refresh_rotation_and_revocation(web: TestClient, engine: Engine) -> None:
    issued = tokens_for(web, OWNER)
    client_id = issued.get("client_id")

    def refresh(token: str) -> Response:
        return web.post(
            "/api/v1/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token,
                "client_id": client_id,
                "resource": RESOURCE,
            },
        )

    rotated = refresh(issued.get("refresh_token", ""))
    assert rotated.status_code == 200
    renewed = rotated.json()
    assert renewed.get("refresh_token") != issued.get("refresh_token")
    assert refresh(issued.get("refresh_token", "")).json().get("error") == "invalid_grant"
    target = web.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": renewed.get("refresh_token"),
            "client_id": client_id,
            "resource": "https://evil.example/mcp",
        },
    )
    assert target.json().get("error") == "invalid_target"
    assert rpc(web, renewed.get("access_token")).status_code == 200
    # The SDK's RFC 7009 handler requires the client_secret field even for public clients.
    revoked = web.post(
        "/api/v1/oauth/revoke",
        data={"token": renewed.get("access_token"), "client_id": client_id, "client_secret": ""},
    )
    assert revoked.status_code == 200
    assert rpc(web, renewed.get("access_token")).status_code == 401
    assert refresh(renewed.get("refresh_token", "")).json().get("error") == "invalid_grant"
    with Session(engine) as db:
        assert db.scalar(select(OAuthGrant).where(OAuthGrant.client_id == client_id)) is None


def test_user_revokes_grant(web: TestClient, engine: Engine) -> None:
    issued = tokens_for(web, OWNER)
    grants = web.get("/api/v1/mcp/grants").json().get("items", [])
    assert [grant.get("client_name") for grant in grants] == ["Codex"]
    grant_id = grants[0].get("id")
    login(web, OTHER)
    assert web.get("/api/v1/mcp/grants").json() == {"items": []}
    assert web.delete(f"/api/v1/mcp/grants/{grant_id}", headers=ORIGIN).status_code == 404
    assert rpc(web, issued.get("access_token")).status_code == 200
    login(web, OWNER)
    assert web.delete(f"/api/v1/mcp/grants/{grant_id}").status_code == 403
    assert web.delete(f"/api/v1/mcp/grants/{grant_id}", headers=ORIGIN).status_code == 204
    assert web.delete(f"/api/v1/mcp/grants/{uuid4()}", headers=ORIGIN).status_code == 404
    assert rpc(web, issued.get("access_token")).status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(OAuthTokenRow)) is None


class MemoryStorage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


@asynccontextmanager
async def connect(
    app: FastAPI,
    account: tuple[str, str],
    mode: str = "auto",
    *,
    write: bool = False,
    consents: list[list[str]] | None = None,
) -> AsyncGenerator[Client]:
    """Official MCP client: discovery, registration, PKCE login with consent, then tools."""
    transport = httpx2.ASGITransport(app=app)
    browser = httpx2.AsyncClient(transport=transport, base_url=PUBLIC)
    callback: dict[str, str] = {}

    async def redirect_handler(url: str) -> None:
        assert url.startswith(f"{ISSUER}/authorize?")
        assert query(url).get("resource") == RESOURCE
        pending = await browser.get(url)
        assert pending.status_code == 302, pending.text
        request = query(pending.headers.get("location", "")).get("request")
        details = await browser.get(f"/api/v1/mcp/authorizations/{request}")
        assert details.status_code == 200, details.text
        requested = [
            scope for scope in details.json().get("scopes", []) if scope != "offline_access"
        ]
        assert requested == [SCOPE] or (
            write and set(requested) == {SCOPE, CATEGORY_WRITE_SCOPE}
        ), {
            "request_scopes": query(url).get("scope"),
            "consent_scopes": requested,
        }
        if consents is not None:
            consents.append(requested)
        decision = await browser.post(
            f"/api/v1/mcp/authorizations/{request}", json={"approve": True}, headers=ORIGIN
        )
        callback.update(query(decision.json().get("redirect_url")))

    async def callback_handler() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=callback.get("code", ""), state=callback.get("state"))

    auth = OAuthClientProvider(
        RESOURCE,
        OAuthClientMetadata(
            redirect_uris=[AnyUrl(CODEX_REDIRECT)],
            client_name="Codex test",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=SCOPE,
        ),
        MemoryStorage(),
        redirect_handler,
        callback_handler,
    )
    http = httpx2.AsyncClient(transport=transport, base_url=PUBLIC, auth=auth)
    email, password = account
    async with browser, http:
        login_response = await browser.post(
            "/api/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
        )
        assert login_response.status_code == 200
        async with Client(streamable_http_client(RESOURCE, http_client=http), mode=mode) as mcp:
            yield mcp


def structured(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    return result.structured_content or {}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_mcp_tools_over_oauth(mcp_app: FastAPI, engine: Engine, mode: str) -> None:
    with Session(engine) as db:
        foreign = db.scalar(select(Receipt.id).where(Receipt.total_amount == Decimal("999.00")))
        own = db.scalar(
            select(Receipt.id).where(Receipt.currency == "CZK", Receipt.total_amount < 500)
        )
    async with mcp_app.router.lifespan_context(mcp_app), connect(mcp_app, OWNER, mode) as mcp:
        tools = (await mcp.list_tools()).tools
        assert {tool.name for tool in tools} == TOOLS
        for tool in tools:
            assert tool.annotations is not None
            writable = tool.name in CATEGORY_WRITES
            assert tool.annotations.read_only_hint is not writable
            assert tool.annotations.destructive_hint is (
                tool.name in {"delete_category", "assign_category_items"}
            )
            assert "user_id" not in str(tool.input_schema)
            assert (tool.meta or {}).get("securitySchemes") == [
                {"type": "oauth2", "scopes": [SCOPE, CATEGORY_WRITE_SCOPE] if writable else [SCOPE]}
            ]
        page = structured(await mcp.call_tool("search_receipts", {"limit": 1}))
        assert page.get("total") == 2
        assert len(page.get("items", [])) == 1
        receipts = structured(await mcp.call_tool("search_receipts", {"query": "rohlik"}))
        assert {item.get("total_amount") for item in receipts.get("items", [])} == {
            "100.25",
            "2.50",
        }
        assert (await mcp.call_tool("search_receipts", {"limit": 51})).is_error
        detail = structured(await mcp.call_tool("get_receipt", {"receipt_id": str(own)}))
        assert detail.get("items", [])[0].get("unit_price") == "1.333333"
        assert detail.get("total_amount") == "100.25"
        hidden = await mcp.call_tool("get_receipt", {"receipt_id": str(foreign)})
        assert hidden.is_error
        assert "receipt_not_found" in str(hidden.content)
        overview = structured(await mcp.call_tool("get_spending_overview", {}))
        assert overview.get("totals", []) == [
            {"currency": "CZK", "amount": "100.25"},
            {"currency": "EUR", "amount": "2.50"},
        ]
        items = structured(await mcp.call_tool("search_items", {}))
        assert {(item.get("name"), item.get("currency")) for item in items.get("items", [])} == {
            ("Rohlík", "CZK"),
            ("Rohlík", "EUR"),
        }
        history = structured(
            await mcp.call_tool("get_item_price_history", {"name": "Rohlík", "currency": "CZK"})
        )
        assert [purchase.get("unit_price") for purchase in history.get("purchases", [])] == ["1.33"]
        secret = await mcp.call_tool(
            "get_item_price_history", {"name": "Secret", "currency": "CZK"}
        )
        assert secret.is_error
        stats = structured(await mcp.call_tool("get_spending_stats", {"period": "all"}))
        assert [total.get("currency") for total in stats.get("totals", [])] == ["CZK", "EUR"]


@pytest.mark.anyio
async def test_mcp_users_are_isolated(mcp_app: FastAPI, engine: Engine) -> None:
    with Session(engine) as db:
        other_id = db.scalar(select(User.id).where(User.email == OTHER[0]))
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        connect(mcp_app, OWNER) as owner,
        connect(mcp_app, OTHER) as other,
    ):
        mine = structured(await owner.call_tool("search_receipts", {}))
        theirs = structured(await other.call_tool("search_receipts", {}))
        assert mine.get("total") == 2
        assert [item.get("total_amount") for item in theirs.get("items", [])] == ["999.00"]
        assert not {item.get("id") for item in mine.get("items", [])} & {
            item.get("id") for item in theirs.get("items", [])
        }
        spoof = await owner.call_tool("search_receipts", {"user_id": str(other_id)})
        assert spoof.is_error or structured(spoof).get("total") == 2
        stolen = await owner.call_tool(
            "get_receipt", {"receipt_id": theirs.get("items", [])[0].get("id")}
        )
        assert stolen.is_error
    with Session(engine) as db:
        owners = set(db.scalars(select(OAuthGrant.owner_id)))
    assert len(owners) == 2


def test_public_url_settings() -> None:
    assert Settings(public_url="").public_url is None
    for url in [
        "http://cartalytix.example",
        "https://cartalytix.example/app",
        "https://u:p@x.example",
    ]:
        with pytest.raises(ValueError, match="Public URL"):
            Settings(public_url=url)


def call_rpc(web: TestClient, token: str, name: str, arguments: dict[str, Any]) -> Response:
    return web.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )


def test_category_writes_require_separate_consent(web: TestClient) -> None:
    token = tokens_for(web, OWNER).get("access_token", "")
    for name, arguments in [
        ("create_category", {"name": "Bakery"}),
        ("update_category", {"category_id": str(uuid4()), "name": "Bakery"}),
        ("delete_category", {"category_id": str(uuid4())}),
        (
            "assign_category_items",
            {
                "items": [{"connection_id": str(uuid4()), "name": "Rohlík", "currency": "CZK"}],
                "category_id": None,
            },
        ),
    ]:
        response = call_rpc(web, token, name, arguments)
        assert response.status_code == 403
        assert response.json().get("error") == "insufficient_scope"
        challenge = response.headers.get("www-authenticate", "")
        assert f'scope="{SCOPE} {CATEGORY_WRITE_SCOPE}"' in challenge
        assert 'error="insufficient_scope"' in challenge
    listed = call_rpc(web, token, "list_categories", {})
    assert listed.status_code == 200
    assert listed.json().get("result", {}).get("structuredContent", {}).get("items") == []


@pytest.mark.anyio
async def test_category_mcp_batch_preview_and_owner_isolation(mcp_app: FastAPI) -> None:
    consents: list[list[str]] = []
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        connect(mcp_app, OWNER, write=True, consents=consents) as owner,
        connect(mcp_app, OTHER, write=True) as other,
    ):
        category = structured(await owner.call_tool("create_category", {"name": "Bakery"}))
        assert consents == [[SCOPE], [SCOPE, CATEGORY_WRITE_SCOPE]]
        category_id = category.get("id")
        renamed = structured(
            await owner.call_tool("update_category", {"category_id": category_id, "name": "Bread"})
        )
        assert renamed.get("name") == "Bread"
        first = structured(await owner.call_tool("list_category_items", {"limit": 1}))
        assert len(first.get("items", [])) == 1
        assert first.get("next_cursor")
        second = structured(
            await owner.call_tool("list_category_items", {"cursor": first.get("next_cursor")})
        )
        assert len(second.get("items", [])) == 1
        assert second.get("next_cursor") is None
        assert {
            item.get("currency") for item in first.get("items", []) + second.get("items", [])
        } == {"CZK", "EUR"}
        product = first.get("items", [])[0]
        key = {field: product.get(field) for field in ("connection_id", "name", "currency")}
        assignment = {"items": [key], "category_id": category_id}
        preview = structured(
            await owner.call_tool("assign_category_items", {**assignment, "dry_run": True})
        )
        assert preview == {"matched": 1, "changed": 1, "protected": 0, "dry_run": True}
        assert (
            structured(await owner.call_tool("list_categories", {}))
            .get("items", [])[0]
            .get("item_count")
            == 0
        )
        applied = structured(await owner.call_tool("assign_category_items", assignment))
        assert applied == {"matched": 1, "changed": 1, "protected": 0, "dry_run": False}
        repeat = structured(await owner.call_tool("assign_category_items", assignment))
        assert repeat.get("changed") == 0
        categorized = structured(
            await owner.call_tool("list_category_items", {"category_id": category_id})
        )
        assert categorized.get("items", [])[0].get("assignment_source") == "mcp"
        assert structured(await other.call_tool("list_categories", {})).get("items") == []
        assert (await other.call_tool("delete_category", {"category_id": category_id})).is_error
        foreign = structured(await other.call_tool("create_category", {"name": "Private"}))
        assert (
            await other.call_tool(
                "assign_category_items", {**assignment, "category_id": foreign.get("id")}
            )
        ).is_error
        assert structured(
            await owner.call_tool("delete_category", {"category_id": category_id})
        ) == {"deleted": True}
        assert (
            len(
                structured(
                    await owner.call_tool("list_category_items", {"uncategorized": True})
                ).get("items", [])
            )
            == 2
        )


def test_read_only_refresh_cannot_gain_category_write_scope(web: TestClient) -> None:
    issued = tokens_for(web, OWNER)
    response = web.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": issued.get("refresh_token"),
            "client_id": issued.get("client_id"),
            "scope": f"{SCOPE} {CATEGORY_WRITE_SCOPE}",
        },
    )
    assert response.status_code == 400
    assert response.json().get("error") == "invalid_scope"
    assert rpc(web, issued.get("access_token")).status_code == 200


@pytest.mark.anyio
async def test_mcp_protects_manual_category_assignments(mcp_app: FastAPI, engine: Engine) -> None:
    async with mcp_app.router.lifespan_context(mcp_app), connect(mcp_app, OWNER, write=True) as mcp:
        manual = structured(await mcp.call_tool("create_category", {"name": "Manual choice"}))
        automated = structured(await mcp.call_tool("create_category", {"name": "AI choice"}))
        products = structured(await mcp.call_tool("list_category_items", {}))
        product = products.get("items", [])[0]
        key = {field: product.get(field) for field in ("connection_id", "name", "currency")}
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == OWNER[0]))
            assert user is not None
            result = assign_category_items(
                user,
                db,
                CategoryAssignmentInput.model_validate(
                    {"items": [key], "category_id": manual.get("id")}
                ),
            )
            assert result.changed == 1
        assignment = {"items": [key], "category_id": automated.get("id")}
        protected = structured(await mcp.call_tool("assign_category_items", assignment))
        assert protected == {"matched": 1, "changed": 0, "protected": 1, "dry_run": False}
        deletion = await mcp.call_tool("delete_category", {"category_id": manual.get("id")})
        assert deletion.is_error
        assert "category_contains_manual_items" in str(deletion.content)
        clear = structured(
            await mcp.call_tool("assign_category_items", {"items": [key], "category_id": None})
        )
        assert clear.get("protected") == 1
        forced = structured(
            await mcp.call_tool("assign_category_items", {**assignment, "overwrite_manual": True})
        )
        assert forced == {"matched": 1, "changed": 1, "protected": 0, "dry_run": False}
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == OWNER[0]))
            assert user is not None
            assign_category_items(
                user,
                db,
                CategoryAssignmentInput.model_validate(
                    {"items": [key], "category_id": manual.get("id")}
                ),
            )
        deleted = structured(
            await mcp.call_tool(
                "delete_category", {"category_id": manual.get("id"), "overwrite_manual": True}
            )
        )
        assert deleted == {"deleted": True}


@pytest.mark.parametrize(
    "body,status",
    [(b"{", 400), (b"x" * (1024 * 1024 + 1), 413)],
    ids=["malformed", "oversized"],
)
def test_category_scope_challenge_bounds_request_body(
    web: TestClient, body: bytes, status: int
) -> None:
    token = tokens_for(web, OWNER).get("access_token")
    response = web.post(
        "/mcp",
        content=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == status


def test_legacy_client_reconsent_preserves_old_read_only_tokens(
    web: TestClient, engine: Engine
) -> None:
    issued = tokens_for(web, OWNER)
    assert issued.get("scope") == SCOPE
    registered = {"client_id": issued.get("client_id")}
    with Session(engine) as db:
        row = db.get(OAuthClient, issued.get("client_id"))
        assert row is not None
        row.registration = {**row.registration, "scope": SCOPE}
        db.commit()
    code, verifier = code_for(web, OWNER, registered, scope=f"{SCOPE} {CATEGORY_WRITE_SCOPE}")
    renewed = exchange(web, registered, code, verifier)
    assert renewed.status_code == 200
    assert renewed.json().get("scope") == f"{SCOPE} {CATEGORY_WRITE_SCOPE}"
    denied = call_rpc(web, issued.get("access_token", ""), "create_category", {"name": "Denied"})
    assert denied.status_code == 403
    allowed = call_rpc(
        web, renewed.json().get("access_token", ""), "create_category", {"name": "Allowed"}
    )
    assert allowed.status_code == 200
    assert allowed.json().get("result", {}).get("structuredContent", {}).get("name") == "Allowed"
    assert rpc(web, issued.get("access_token")).status_code == 200


@pytest.mark.anyio
async def test_category_mcp_cursor_supports_long_unicode_names(
    mcp_app: FastAPI, engine: Engine
) -> None:
    with Session(engine) as db:
        receipt = db.scalar(
            select(Receipt).where(Receipt.currency == "CZK", Receipt.total_amount < 500)
        )
        assert receipt is not None
        for position, name in enumerate(["界" * 1000, "界" * 999 + "z"], start=1):
            db.add(
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=position,
                    name=name,
                    quantity=Decimal("1"),
                    unit="ks",
                    unit_price=Decimal("1"),
                    total_amount=Decimal("1"),
                )
            )
        db.commit()
    async with mcp_app.router.lifespan_context(mcp_app), connect(mcp_app, OWNER) as mcp:
        first = structured(await mcp.call_tool("list_category_items", {"limit": 3}))
        cursor = first.get("next_cursor")
        assert cursor is not None and len(cursor) > 4000
        second = structured(await mcp.call_tool("list_category_items", {"cursor": cursor}))
        assert len(second.get("items", [])) == 1
        assert second.get("next_cursor") is None
