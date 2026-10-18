"""Read-only MCP connector (Streamable HTTP) over the owner-scoped web queries."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route, request_response

from cartalytix.credentials import TokenCipher
from cartalytix.item_routes import item_detail, items
from cartalytix.models import User
from cartalytix.oauth import OFFLINE_SCOPE, SCOPE, DatabaseOAuthProvider, McpUrls
from cartalytix.receipt_routes import overview, receipt_detail, receipts
from cartalytix.schemas import (
    ItemDetailOutput,
    ItemSort,
    ItemsOutput,
    OverviewOutput,
    ReceiptDetailOutput,
    ReceiptSort,
    ReceiptsOutput,
    StatsOutput,
    StatsPeriod,
)
from cartalytix.settings import Settings
from cartalytix.stats_routes import overview_stats

PAGE_LIMIT = 50
TOKEN_BODY_LIMIT = 64 * 1024
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
SECURITY = {"securitySchemes": [{"type": "oauth2", "scopes": [SCOPE]}]}
INSTRUCTIONS = (
    "Read-only access to the signed-in user's Cartalytix receipts. Amounts are exact decimal "
    "strings in the currency next to them; never add or compare amounts of different currencies."
)

Limit = Annotated[int, Field(ge=1, le=PAGE_LIMIT, description="Page size")]
Offset = Annotated[int, Field(ge=0, le=100000, description="Rows to skip")]
Query = Annotated[str | None, Field(max_length=100, description="Accent-insensitive text")]
Money = Annotated[Decimal | None, Field(ge=0, max_digits=18, decimal_places=2)]
Since = Annotated[datetime | None, Field(description="Purchased at or after (ISO 8601)")]
Until = Annotated[datetime | None, Field(description="Purchased before (ISO 8601)")]
Connection = Annotated[UUID | None, Field(description="Only this provider connection")]


class McpConnector:
    def __init__(self, server: MCPServer, routes: list[BaseRoute], urls: McpUrls) -> None:
        self.server = server
        self.routes = routes
        self.urls = urls


def _owner(db: Session) -> User:
    # The owner comes only from the verified token; tools never accept a user id.
    token = get_access_token()
    user = db.get(User, UUID(token.subject)) if token and token.subject else None
    if user is None or user.is_disabled or user.is_admin or user.must_change_password:
        raise ToolError("authentication_required")
    return user


def _register_tools(server: MCPServer, engine: Engine) -> None:
    def tool[**P, R](description: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
        return server.tool(description=description, annotations=READ_ONLY, meta=SECURITY)

    @tool("Search and page through receipts, newest first by default.")
    def search_receipts(
        query: Query = None,
        purchased_from: Since = None,
        purchased_to: Until = None,
        amount_min: Money = None,
        amount_max: Money = None,
        connection_id: Connection = None,
        sort: ReceiptSort = ReceiptSort.PURCHASED_AT_DESC,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> ReceiptsOutput:
        with Session(engine) as db:
            return receipts(
                _owner(db),
                db,
                limit=limit,
                offset=offset,
                connection_id=connection_id,
                q=query,
                purchased_from=purchased_from,
                purchased_to=purchased_to,
                amount_min=amount_min,
                amount_max=amount_max,
                sort=sort,
            )

    @tool("Receipt detail with its line items and discounts.")
    def get_receipt(receipt_id: UUID) -> ReceiptDetailOutput:
        with Session(engine) as db:
            try:
                return receipt_detail(receipt_id, _owner(db), db)
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @tool("Search purchased items grouped by name and currency, with price statistics.")
    def search_items(
        query: Query = None,
        purchased_from: Since = None,
        purchased_to: Until = None,
        price_min: Money = None,
        price_max: Money = None,
        connection_id: Connection = None,
        sort: ItemSort = ItemSort.NAME_ASC,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> ItemsOutput:
        with Session(engine) as db:
            return items(
                _owner(db),
                db,
                limit=limit,
                offset=offset,
                connection_id=connection_id,
                q=query,
                purchased_from=purchased_from,
                purchased_to=purchased_to,
                price_min=price_min,
                price_max=price_max,
                sort=sort,
            )

    @tool("Purchase price history of one item (exact name and currency from search_items).")
    def get_item_price_history(
        name: Annotated[str, Field(min_length=1, max_length=1000)],
        currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")],
    ) -> ItemDetailOutput:
        with Session(engine) as db:
            try:
                return item_detail(_owner(db), db, name=name, currency=currency)
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @tool("Receipt and connection counts, totals per currency and the latest receipts.")
    def get_spending_overview() -> OverviewOutput:
        with Session(engine) as db:
            return overview(_owner(db), db)

    @tool("Spending statistics for a period or one month (YYYY-MM), in the main currency.")
    def get_spending_stats(
        period: StatsPeriod = StatsPeriod.LAST_12_MONTHS,
        month: Annotated[str | None, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
    ) -> StatsOutput:
        with Session(engine) as db:
            return overview_stats(_owner(db), db, period=period, month=month)


def _token_route(provider: DatabaseOAuthProvider, urls: McpUrls) -> Route:
    handler = TokenHandler(provider, ClientAuthenticator(provider))

    async def token(request: Request) -> Response:
        # RFC 8707: the token request must not name another resource than the authorization.
        resource = (await request.form()).get("resource")
        if resource is not None and not urls.is_resource(str(resource)):
            return JSONResponse(
                {"error": "invalid_target", "error_description": "Unknown resource"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return await handler.handle(request)

    return Route(
        "/api/v1/oauth/token",
        endpoint=RequestBodyLimitMiddleware(request_response(token), TOKEN_BODY_LIMIT),
        methods=["POST"],
    )


def create_mcp(engine: Engine, settings: Settings) -> McpConnector:
    assert settings.public_url is not None
    urls = McpUrls(settings.public_url)
    provider = DatabaseOAuthProvider(engine, TokenCipher(settings), urls)
    server = MCPServer(
        name="Cartalytix",
        instructions=INSTRUCTIONS,
        token_verifier=ProviderTokenVerifier(provider),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(urls.issuer),
            resource_server_url=AnyHttpUrl(urls.resource),
            required_scopes=[SCOPE],
            validate_token_resource=True,
        ),
    )
    _register_tools(server, engine)
    host = settings.public_url.host or ""
    transport = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host, f"{host}:{settings.public_url.port}"],
            allowed_origins=[urls.origin, "https://chatgpt.com"],
        ),
    )
    routes: list[BaseRoute] = [Route("/mcp", endpoint=transport)]
    for route in transport.routes:
        if isinstance(route, Route) and route.path.startswith("/.well-known/"):
            # RFC 9728 path insertion for /mcp, plus the root location ChatGPT also reads.
            routes += [
                Route(route.path, endpoint=route.app),
                Route("/.well-known/oauth-protected-resource", endpoint=route.app),
            ]
    for route in create_auth_routes(
        provider,
        AnyHttpUrl(urls.issuer),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=[SCOPE, OFFLINE_SCOPE], default_scopes=[SCOPE]
        ),
        revocation_options=RevocationOptions(enabled=True),
    ):
        if not isinstance(route, Route) or route.path == "/token":
            continue
        if route.path == "/.well-known/oauth-authorization-server":
            # RFC 8414 path insertion for the issuer, plus the legacy root location.
            routes += [
                Route(f"{route.path}/api/v1/oauth", endpoint=route.app),
                Route(route.path, endpoint=route.app),
            ]
        else:
            routes.append(
                Route(f"/api/v1/oauth{route.path}", endpoint=route.app, methods=route.methods)
            )
    routes.append(_token_route(provider, urls))
    return McpConnector(server, routes, urls)
