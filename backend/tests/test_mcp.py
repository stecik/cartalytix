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

from cartalytix.main import create_app
from cartalytix.models import (
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
OWNER = ("owner@example.com", "correct horse battery")
OTHER = ("other@example.com", "other secret password")
TOOLS = {
    "search_receipts",
    "get_receipt",
    "search_items",
    "get_item_price_history",
    "get_spending_overview",
    "get_spending_stats",
}
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
        assert resource.get("scopes_supported") == [SCOPE]
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
        assert set(metadata.get("scopes_supported")) == {SCOPE, "offline_access"}
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
    app: FastAPI, account: tuple[str, str], mode: str = "auto"
) -> AsyncGenerator[Client]:
    """Official MCP client: discovery, registration, PKCE login with consent, then tools."""
    transport = httpx2.ASGITransport(app=app)
    browser = httpx2.AsyncClient(transport=transport, base_url=PUBLIC)
    callback: dict[str, str] = {}

    async def redirect_handler(url: str) -> None:
        assert url.startswith(f"{ISSUER}/authorize?")
        assert query(url).get("resource") == RESOURCE
        pending = await browser.get(url)
        request = query(pending.headers.get("location", "")).get("request")
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
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert "user_id" not in str(tool.input_schema)
            assert (tool.meta or {}).get("securitySchemes") == [
                {"type": "oauth2", "scopes": [SCOPE]}
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
