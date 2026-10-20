"""MCP connector with owner-scoped queries and explicitly authorized category writes."""

import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from mcp.server.auth.handlers.token import TokenHandler
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.middleware.client_auth import ClientAuthenticator
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.routes import create_auth_routes, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route, request_response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cartalytix.category_routes import (
    assign_category_items as assign_items,
)
from cartalytix.category_routes import (
    categories,
    category_items,
)
from cartalytix.category_routes import (
    create_category as create_category_record,
)
from cartalytix.category_routes import (
    delete_category as delete_category_record,
)
from cartalytix.category_routes import (
    update_category as update_category_record,
)
from cartalytix.category_schemas import (
    CategoriesOutput,
    CategoryAssignmentInput,
    CategoryAssignmentOutput,
    CategoryInput,
    CategoryItemsOutput,
    CategoryKeyInput,
    CategoryOutput,
)
from cartalytix.credentials import TokenCipher
from cartalytix.item_routes import item_detail, item_totals, items
from cartalytix.item_routes import set_product_exclusion as product_exclusion_record
from cartalytix.models import User
from cartalytix.oauth import (
    CALCULATOR_SCOPE,
    CATEGORIES_WRITE_SCOPE,
    CLIENT_SCOPES,
    EXCLUSIONS_WRITE_SCOPE,
    SCOPE,
    DatabaseOAuthProvider,
    McpUrls,
)
from cartalytix.receipt_routes import overview, receipt_detail, receipts
from cartalytix.receipt_routes import set_receipt_exclusion as receipt_exclusion_record
from cartalytix.receipt_routes import set_receipt_item_exclusion as receipt_item_exclusion_record
from cartalytix.schemas import (
    STATS_BUCKET_PATTERN,
    ExclusionFilter,
    ExclusionInput,
    ItemDetailOutput,
    ItemKeyInput,
    ItemSort,
    ItemsOutput,
    ItemTotalsInput,
    ItemTotalsOutput,
    OverviewOutput,
    ProductExclusionInput,
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
MCP_BODY_LIMIT = 1024 * 1024
# Optional permissions the owner enables per connection; every other tool needs only read.
TOOL_SCOPES = {
    "create_category": CATEGORIES_WRITE_SCOPE,
    "update_category": CATEGORIES_WRITE_SCOPE,
    "delete_category": CATEGORIES_WRITE_SCOPE,
    "assign_category_items": CATEGORIES_WRITE_SCOPE,
    "calculate_items_spending": CALCULATOR_SCOPE,
    "set_receipt_exclusion": EXCLUSIONS_WRITE_SCOPE,
    "set_product_exclusion": EXCLUSIONS_WRITE_SCOPE,
    "set_receipt_item_exclusion": EXCLUSIONS_WRITE_SCOPE,
}
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
SECURITY = {"securitySchemes": [{"type": "oauth2", "scopes": [SCOPE]}]}
WRITE_SECURITY = {
    "securitySchemes": [{"type": "oauth2", "scopes": [SCOPE, CATEGORIES_WRITE_SCOPE]}]
}
CALCULATOR_SECURITY = {"securitySchemes": [{"type": "oauth2", "scopes": [SCOPE, CALCULATOR_SCOPE]}]}
EXCLUSIONS_SECURITY = {
    "securitySchemes": [{"type": "oauth2", "scopes": [SCOPE, EXCLUSIONS_WRITE_SCOPE]}]
}
# Exclusions only change what counts in spending and are reversible with excluded=false.
EXCLUSION_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
INSTRUCTIONS = (
    "Access to the signed-in user's Cartalytix receipts and categories. Amounts are exact "
    "decimal strings in the currency next to them; never add or compare different currencies. "
    "Use included_amount for spending; total_amount and unit_price retain source amounts. "
    "Excluded data remains available through exclusion=all and detail tools. "
    "Category writes require cartalytix:categories:write authorization. Page list_category_items "
    "until next_cursor is null to inspect all purchased products. Products use the exact "
    "connection_id, name and currency returned there. Use assign_category_items with dry_run "
    "before applying batches of up to 200 products. Manual assignments are protected unless "
    "overwrite_manual is explicitly enabled. Each product currently has at most one category. "
    "To total spending on chosen products, find their exact name and currency with search_items, "
    "then pass up to 200 of them to calculate_items_spending (cartalytix:calculator). "
    "Exclusion writes require cartalytix:exclusions:write and are reversible with "
    "excluded=false: set_receipt_exclusion leaves out a whole receipt, set_product_exclusion "
    "leaves out a product (exact name and currency) on every current and future receipt, and "
    "set_receipt_item_exclusion leaves out one line (item id from get_receipt) on one receipt."
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


class ToolScopeMiddleware:
    """Challenge optional permissions after bearer verification, before executing a tool."""

    def __init__(self, app: ASGIApp, urls: McpUrls) -> None:
        self.app = app
        self.urls = urls

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = get_access_token()
        if scope["type"] != "http" or scope.get("method") != "POST" or token is None:

            async def initial_challenge(message: Message) -> None:
                if message["type"] == "http.response.start" and message["status"] == 401:
                    headers = MutableHeaders(scope=message)
                    challenge = headers.get("WWW-Authenticate", "")
                    if challenge.startswith("Bearer") and "scope=" not in challenge:
                        headers["WWW-Authenticate"] = f'{challenge}, scope="{SCOPE}"'
                await send(message)

            await self.app(scope, receive, initial_challenge)
            return
        body = bytearray()
        async for chunk in Request(scope, receive).stream():
            body.extend(chunk)
            if len(body) > MCP_BODY_LIMIT:
                await JSONResponse({"error": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
                return
        try:
            payload = json.loads(body)
        except ValueError, UnicodeDecodeError, RecursionError:
            await JSONResponse({"error": "invalid_request"}, status_code=400)(scope, receive, send)
            return
        params = payload.get("params") if isinstance(payload, dict) else None
        name = params.get("name") if isinstance(params, dict) else None
        required = TOOL_SCOPES.get(name) if isinstance(name, str) else None
        if (
            isinstance(payload, dict)
            and payload.get("method") == "tools/call"
            and required is not None
            and required not in token.scopes
        ):
            challenge = (
                'Bearer error="insufficient_scope", '
                f'scope="{SCOPE} {required}", '
                f'resource_metadata="{self.urls.origin}/.well-known/oauth-protected-resource/mcp"'
            )
            await JSONResponse(
                {"error": "insufficient_scope"},
                status_code=403,
                headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"},
            )(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _owner(db: Session, *, scope: str | None = None) -> User:
    # The owner comes only from the verified token; tools never accept a user id.
    token = get_access_token()
    user = db.get(User, UUID(token.subject)) if token and token.subject else None
    if user is None or user.is_disabled or user.is_admin or user.must_change_password:
        raise ToolError("authentication_required")
    if scope is not None and token is not None and scope not in token.scopes:
        raise ToolError(f"insufficient_scope: {scope} required")
    return user


def _register_tools(server: MCPServer, engine: Engine) -> None:
    def tool[**P, R](description: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
        return server.tool(description=description, annotations=READ_ONLY, meta=SECURITY)

    def write_tool[**P, R](
        description: str, *, destructive: bool = False, idempotent: bool = True
    ) -> Callable[[Callable[P, R]], Callable[P, R]]:
        return server.tool(
            description=description,
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=destructive,
                idempotent_hint=idempotent,
                open_world_hint=False,
            ),
            meta=WRITE_SECURITY,
        )

    @tool("List your categories with the number of assigned products.")
    def list_categories() -> CategoriesOutput:
        with Session(engine) as db:
            return categories(_owner(db), db)

    @write_tool("Create one of your categories.", idempotent=False)
    def create_category(
        name: Annotated[str, Field(min_length=1, max_length=100)],
    ) -> CategoryOutput:
        with Session(engine) as db:
            try:
                return create_category_record(
                    _owner(db, scope=CATEGORIES_WRITE_SCOPE), db, CategoryInput(name=name)
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @write_tool("Rename one of your categories.")
    def update_category(
        category_id: UUID, name: Annotated[str, Field(min_length=1, max_length=100)]
    ) -> CategoryOutput:
        with Session(engine) as db:
            try:
                return update_category_record(
                    category_id,
                    _owner(db, scope=CATEGORIES_WRITE_SCOPE),
                    db,
                    CategoryInput(name=name),
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @write_tool(
        "Delete one of your categories and remove its assignments; purchases are preserved. "
        "Manual assignments prevent deletion unless overwrite_manual=true explicitly allows it.",
        destructive=True,
    )
    def delete_category(category_id: UUID, overwrite_manual: bool = False) -> dict[str, bool]:
        with Session(engine) as db:
            try:
                delete_category_record(
                    category_id,
                    _owner(db, scope=CATEGORIES_WRITE_SCOPE),
                    db,
                    source="mcp",
                    overwrite_manual=overwrite_manual,
                )
                return {"deleted": True}
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @tool(
        "Page purchased products by connection, exact name and currency, with category and "
        "assignment source. Follow next_cursor until null. Filter uncategorized to plan batches."
    )
    def list_category_items(
        query: Query = None,
        category_id: UUID | None = None,
        uncategorized: bool = False,
        limit: Limit = PAGE_LIMIT,
        cursor: Annotated[str | None, Field(max_length=12000)] = None,
    ) -> CategoryItemsOutput:
        with Session(engine) as db:
            try:
                return category_items(
                    _owner(db),
                    db,
                    limit=limit,
                    cursor=cursor,
                    q=query,
                    category_id=category_id,
                    uncategorized=uncategorized,
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @write_tool(
        "Assign up to 200 exact products to a category, replacing the previous assignment. "
        "Set category_id null to remove assignment. Preview with dry_run=true. Manual edits "
        "are protected by default; overwrite_manual=true explicitly allows replacing them.",
        destructive=True,
    )
    def assign_category_items(
        items: Annotated[list[CategoryKeyInput], Field(min_length=1, max_length=200)],
        category_id: UUID | None,
        dry_run: bool = False,
        overwrite_manual: bool = False,
    ) -> CategoryAssignmentOutput:
        with Session(engine) as db:
            try:
                return assign_items(
                    _owner(db, scope=CATEGORIES_WRITE_SCOPE),
                    db,
                    CategoryAssignmentInput(
                        items=items,
                        category_id=category_id,
                        dry_run=dry_run,
                        overwrite_manual=overwrite_manual,
                    ),
                    source="mcp",
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @tool("Search and page through receipts, newest first by default.")
    def search_receipts(
        query: Query = None,
        purchased_from: Since = None,
        purchased_to: Until = None,
        amount_min: Money = None,
        amount_max: Money = None,
        connection_id: Connection = None,
        sort: ReceiptSort = ReceiptSort.PURCHASED_AT_DESC,
        exclusion: ExclusionFilter = ExclusionFilter.INCLUDED,
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
                exclusion=exclusion,
            )

    @tool("Receipt detail with its line items and discounts.")
    def get_receipt(receipt_id: UUID) -> ReceiptDetailOutput:
        with Session(engine) as db:
            try:
                return receipt_detail(receipt_id, _owner(db), db)
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    def exclusion_tool[**P, R](description: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
        return server.tool(
            description=description, annotations=EXCLUSION_WRITE, meta=EXCLUSIONS_SECURITY
        )

    @exclusion_tool(
        "Leave a whole receipt out of spending (excluded=true) or count it again (false). "
        "Needs cartalytix:exclusions:write."
    )
    def set_receipt_exclusion(receipt_id: UUID, excluded: bool = True) -> ReceiptDetailOutput:
        with Session(engine) as db:
            try:
                return receipt_exclusion_record(
                    receipt_id,
                    ExclusionInput(excluded=excluded),
                    _owner(db, scope=EXCLUSIONS_WRITE_SCOPE),
                    db,
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @exclusion_tool(
        "Leave a product (exact name and currency from search_items) out of spending on every "
        "current and future receipt (excluded=true), or count it again (false). Needs "
        "cartalytix:exclusions:write."
    )
    def set_product_exclusion(
        name: Annotated[str, Field(min_length=1, max_length=1000)],
        currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")],
        excluded: bool = True,
    ) -> ItemDetailOutput:
        with Session(engine) as db:
            try:
                return product_exclusion_record(
                    _owner(db, scope=EXCLUSIONS_WRITE_SCOPE),
                    db,
                    ProductExclusionInput(name=name, currency=currency, excluded=excluded),
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc

    @exclusion_tool(
        "Leave one line (item id from get_receipt) of one receipt out of spending (excluded=true) "
        "or count it again (false); other purchases of the product stay counted. Needs "
        "cartalytix:exclusions:write."
    )
    def set_receipt_item_exclusion(
        receipt_id: UUID, item_id: UUID, excluded: bool = True
    ) -> ReceiptDetailOutput:
        with Session(engine) as db:
            try:
                return receipt_item_exclusion_record(
                    receipt_id,
                    item_id,
                    ExclusionInput(excluded=excluded),
                    _owner(db, scope=EXCLUSIONS_WRITE_SCOPE),
                    db,
                )
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
        exclusion: ExclusionFilter = ExclusionFilter.INCLUDED,
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
                exclusion=exclusion,
            )

    @server.tool(
        description=(
            "Total spending on chosen products (exact name and currency from search_items) in a "
            "period, with a per-product breakdown and totals per currency. Needs "
            "cartalytix:calculator."
        ),
        annotations=READ_ONLY,
        meta=CALCULATOR_SECURITY,
    )
    def calculate_items_spending(
        items: Annotated[list[ItemKeyInput], Field(min_length=1, max_length=200)],
        purchased_from: Since = None,
        purchased_to: Until = None,
        connection_id: Connection = None,
    ) -> ItemTotalsOutput:
        with Session(engine) as db:
            return item_totals(
                _owner(db, scope=CALCULATOR_SCOPE),
                db,
                ItemTotalsInput(
                    items=items,
                    purchased_from=purchased_from,
                    purchased_to=purchased_to,
                    connection_id=connection_id,
                ),
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

    @tool(
        "Spending statistics for a period, in the main currency. Custom period needs date_from "
        "and date_to (inclusive). bucket narrows it to one month (YYYY-MM) or day (YYYY-MM-DD)."
    )
    def get_spending_stats(
        period: StatsPeriod = StatsPeriod.LAST_12_MONTHS,
        bucket: Annotated[str | None, Field(pattern=STATS_BUCKET_PATTERN)] = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> StatsOutput:
        with Session(engine) as db:
            try:
                return overview_stats(
                    _owner(db),
                    db,
                    period=period,
                    bucket=bucket,
                    date_from=date_from,
                    date_to=date_to,
                )
            except HTTPException as exc:
                raise ToolError(str(exc.detail)) from exc


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
        max_request_body_size=MCP_BODY_LIMIT,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host, f"{host}:{settings.public_url.port}"],
            allowed_origins=[urls.origin, "https://chatgpt.com"],
        ),
    )
    for route in transport.routes:
        if isinstance(route, Route) and route.path == "/mcp":
            route.app = ToolScopeMiddleware(route.app, urls)
    routes: list[BaseRoute] = [Route("/mcp", endpoint=ToolScopeMiddleware(transport, urls))]
    for route in create_protected_resource_routes(
        resource_url=AnyHttpUrl(urls.resource),
        authorization_servers=[AnyHttpUrl(urls.issuer)],
        scopes_supported=[SCOPE, CATEGORIES_WRITE_SCOPE, CALCULATOR_SCOPE, EXCLUSIONS_WRITE_SCOPE],
    ):
        # RFC 9728 path insertion for /mcp, plus the root location ChatGPT also reads.
        routes += [
            Route(route.path, endpoint=route.app),
            Route("/.well-known/oauth-protected-resource", endpoint=route.app),
        ]
    for route in create_auth_routes(
        provider,
        AnyHttpUrl(urls.issuer),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=CLIENT_SCOPES,
            default_scopes=[SCOPE],
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
