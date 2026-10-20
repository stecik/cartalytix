"""Database-backed OAuth 2.1 authorization server for the MCP connector.

The MCP SDK handles the protocol (PKCE S256, client authentication, metadata, error responses);
this module stores clients, consents and opaque tokens so every worker and restart shares state.
Tokens, codes and pending requests are kept only as SHA-256 hashes.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import Annotated
from urllib.parse import urlparse
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as IssuedTokens
from pydantic import AnyHttpUrl, AnyUrl, ValidationError
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from cartalytix.credentials import TokenCipher
from cartalytix.dependencies import Db, Owner
from cartalytix.models import (
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthCode,
    OAuthGrant,
    OAuthToken,
)
from cartalytix.schemas import (
    McpAuthorizationOutput,
    McpConsentInput,
    McpConsentOutput,
    McpGrantInput,
    McpGrantOutput,
    McpGrantsOutput,
    McpScope,
)
from cartalytix.security import token_hash

SCOPE = McpScope.READ.value
CATEGORIES_WRITE_SCOPE = McpScope.CATEGORIES_WRITE.value
CALCULATOR_SCOPE = McpScope.CALCULATOR.value
# Advertised only by the authorization server; refresh tokens are issued either way.
OFFLINE_SCOPE = "offline_access"
CLIENT_SCOPES = [SCOPE, OFFLINE_SCOPE, CATEGORIES_WRITE_SCOPE, CALCULATOR_SCOPE]
REQUEST_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
MAX_REGISTRATION_BYTES = 16 * 1024
ACCESS = "access"
REFRESH = "refresh"
# ChatGPT uses the stable redirect or a callback-specific one; Codex listens on loopback.
CHATGPT_REDIRECT = re.compile(
    r"https://chatgpt\.com/(?:connector_platform_oauth_redirect|connector/oauth/[A-Za-z0-9_-]{1,128})"
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class McpUrls:
    """Public endpoints derived only from configuration, never from request headers."""

    def __init__(self, public_url: AnyHttpUrl) -> None:
        self.origin = str(public_url).rstrip("/")
        self.resource = f"{self.origin}/mcp"
        self.issuer = f"{self.origin}/api/v1/oauth"
        self.consent = f"{self.origin}/oauth/consent"

    def is_resource(self, value: str) -> bool:
        try:
            return str(AnyHttpUrl(value)).rstrip("/") == self.resource
        except ValidationError:
            return False


def allowed_redirect(uri: AnyUrl) -> bool:
    if CHATGPT_REDIRECT.fullmatch(str(uri)):
        return True
    return (
        uri.scheme == "http"
        and uri.host in LOOPBACK_HOSTS
        and not uri.username
        and not uri.password
        and not uri.fragment
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    # SQLite returns naive UTC values.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def granted_scopes(chosen: list[McpScope], *, offline: bool) -> list[str]:
    """Read plus the chosen permissions in a stable order; offline access follows the client."""
    scopes = [scope.value for scope in McpScope if scope == McpScope.READ or scope in chosen]
    return [*scopes, OFFLINE_SCOPE] if offline else scopes


def revoke_grant(db: Session, grant_id: UUID) -> None:
    db.execute(delete(OAuthToken).where(OAuthToken.grant_id == grant_id))
    db.execute(update(OAuthCode).where(OAuthCode.grant_id == grant_id).values(grant_id=None))
    db.execute(delete(OAuthGrant).where(OAuthGrant.id == grant_id))


class DatabaseOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    def __init__(self, engine: Engine, cipher: TokenCipher, urls: McpUrls) -> None:
        self.engine = engine
        self.cipher = cipher
        self.urls = urls

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await anyio.to_thread.run_sync(self._get_client, client_id)

    def _get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with Session(self.engine) as db:
            client = db.get(OAuthClient, client_id[:64])
        if client is None:
            return None
        secret = self.cipher.decrypt(client.encrypted_secret) if client.encrypted_secret else None
        return OAuthClientInformationFull.model_validate(
            {
                **client.registration,
                "client_secret": secret,
                # Registration describes permitted requests, not the user's grant. Old
                # clients may request new consent; their existing tokens keep their scopes.
                "scope": " ".join(CLIENT_SCOPES),
            }
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = client_info.redirect_uris or []
        if not uris or not all(allowed_redirect(uri) for uri in uris):
            raise RegistrationError("invalid_redirect_uri", "Redirect URI is not allowed")
        registration = client_info.model_dump(
            mode="json", exclude={"client_secret"}, exclude_none=True
        )
        # Permit later consent without changing the scopes returned to the registering client.
        registration["scope"] = " ".join(CLIENT_SCOPES)
        if len(json.dumps(registration)) > MAX_REGISTRATION_BYTES:
            raise RegistrationError("invalid_client_metadata", "Client metadata is too large")
        client = OAuthClient(
            id=client_info.client_id,
            name=(client_info.client_name or "MCP client")[:200],
            registration=registration,
            encrypted_secret=self.cipher.encrypt(client_info.client_secret)
            if client_info.client_secret
            else None,
        )
        await anyio.to_thread.run_sync(self._add, client)

    def _add(self, row: OAuthClient) -> None:
        with Session(self.engine) as db:
            db.add(row)
            db.commit()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and not self.urls.is_resource(params.resource):
            raise AuthorizeError("invalid_target", "Unknown resource")
        scopes = params.scopes or (client.scope or SCOPE).split()
        if not set(scopes) <= set(CLIENT_SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scope")
        request = token_urlsafe(32)
        row = OAuthAuthorizationRequest(
            token_hash=token_hash(request),
            client_id=client.client_id or "",
            params={
                "state": params.state,
                "scopes": scopes,
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": self.urls.resource,
            },
            expires_at=_now() + REQUEST_TTL,
        )
        await anyio.to_thread.run_sync(self._add_request, row)
        return construct_redirect_uri(self.urls.consent, request=request)

    def _add_request(self, row: OAuthAuthorizationRequest) -> None:
        with Session(self.engine) as db:
            db.execute(
                delete(OAuthAuthorizationRequest).where(
                    OAuthAuthorizationRequest.expires_at <= _now()
                )
            )
            db.execute(delete(OAuthCode).where(OAuthCode.expires_at <= _now()))
            db.add(row)
            db.commit()

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return await anyio.to_thread.run_sync(self._load_code, client, authorization_code)

    def _load_code(self, client: OAuthClientInformationFull, code: str) -> AuthorizationCode | None:
        with Session(self.engine) as db:
            row = db.get(OAuthCode, token_hash(code))
            if row is None or row.client_id != client.client_id:
                return None
            if row.used_at is not None:
                # A replayed code means it leaked; revoke what it produced (RFC 6749 4.1.2).
                if row.grant_id is not None:
                    revoke_grant(db, row.grant_id)
                    db.commit()
                return None
            params = row.params
            return AuthorizationCode(
                code=code,
                scopes=list(params.get("scopes", [])),
                expires_at=_aware(row.expires_at).timestamp(),
                client_id=row.client_id,
                code_challenge=str(params.get("code_challenge", "")),
                redirect_uri=AnyUrl(str(params.get("redirect_uri", ""))),
                redirect_uri_provided_explicitly=bool(
                    params.get("redirect_uri_provided_explicitly", True)
                ),
                resource=str(params.get("resource", "")),
                subject=str(row.owner_id),
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> IssuedTokens:
        return await anyio.to_thread.run_sync(self._exchange_code, authorization_code)

    def _exchange_code(self, code: AuthorizationCode) -> IssuedTokens:
        code_hash = token_hash(code.code)
        with Session(self.engine) as db:
            claimed = db.scalar(
                update(OAuthCode)
                .where(
                    OAuthCode.token_hash == code_hash,
                    OAuthCode.used_at.is_(None),
                    OAuthCode.expires_at > _now(),
                )
                .values(used_at=_now())
                .returning(OAuthCode.token_hash)
            )
            if claimed is None:
                raise TokenError("invalid_grant", "authorization code is not valid")
            owner_id = UUID(code.subject or "")
            grant = db.scalar(
                select(OAuthGrant).where(
                    OAuthGrant.owner_id == owner_id, OAuthGrant.client_id == code.client_id
                )
            )
            if grant is None:
                grant = OAuthGrant(owner_id=owner_id, client_id=code.client_id)
                db.add(grant)
            # A new consent replaces the permissions of an earlier one for the same client.
            grant.scopes = code.scopes
            db.flush()
            db.execute(
                update(OAuthCode).where(OAuthCode.token_hash == code_hash).values(grant_id=grant.id)
            )
            tokens = self._issue(db, grant, code.scopes, code.resource or self.urls.resource)
            db.commit()
            return tokens

    def _issue(
        self, db: Session, grant: OAuthGrant, requested: list[str], resource: str
    ) -> IssuedTokens:
        now = _now()
        db.execute(
            delete(OAuthToken).where(OAuthToken.grant_id == grant.id, OAuthToken.expires_at <= now)
        )
        access, refresh = token_urlsafe(32), token_urlsafe(32)
        db.add_all(
            [
                OAuthToken(
                    token_hash=token_hash(value),
                    grant_id=grant.id,
                    kind=kind,
                    scopes=requested,
                    resource=resource,
                    expires_at=now + ttl,
                )
                for value, kind, ttl in (
                    (access, ACCESS, ACCESS_TTL),
                    (refresh, REFRESH, REFRESH_TTL),
                )
            ]
        )
        return IssuedTokens(
            access_token=access,
            token_type="Bearer",
            expires_in=int(ACCESS_TTL.total_seconds()),
            scope=" ".join(grant.scopes),
            refresh_token=refresh,
        )

    def _token(self, db: Session, value: str, kind: str) -> tuple[OAuthToken, OAuthGrant] | None:
        row = db.execute(
            select(OAuthToken, OAuthGrant)
            .join(OAuthGrant, OAuthToken.grant_id == OAuthGrant.id)
            .where(
                OAuthToken.token_hash == token_hash(value),
                OAuthToken.kind == kind,
                OAuthToken.expires_at > _now(),
            )
        ).first()
        return None if row is None else (row[0], row[1])

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return await anyio.to_thread.run_sync(self._load_refresh, client, refresh_token)

    def _load_refresh(self, client: OAuthClientInformationFull, value: str) -> RefreshToken | None:
        with Session(self.engine) as db:
            found = self._token(db, value, REFRESH)
        if found is None or found[1].client_id != client.client_id:
            return None
        token, grant = found
        return RefreshToken(
            token=value,
            client_id=grant.client_id,
            # Clients may resend their original scope; issued tokens still carry the grant's.
            scopes=sorted({*token.scopes, *grant.scopes}),
            expires_at=int(_aware(token.expires_at).timestamp()),
            resource=token.resource,
            subject=str(grant.owner_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> IssuedTokens:
        return await anyio.to_thread.run_sync(self._rotate, refresh_token, scopes)

    def _rotate(self, refresh_token: RefreshToken, scopes: list[str]) -> IssuedTokens:
        with Session(self.engine) as db:
            found = self._token(db, refresh_token.token, REFRESH)
            if found is None:
                raise TokenError("invalid_grant", "refresh token is not valid")
            token, grant = found
            # Rotation: only the request that deletes the row may issue new tokens.
            removed = db.scalar(
                delete(OAuthToken)
                .where(OAuthToken.token_hash == token.token_hash)
                .returning(OAuthToken.token_hash)
            )
            if removed is None:
                raise TokenError("invalid_grant", "refresh token is not valid")
            tokens = self._issue(db, grant, scopes, token.resource)
            db.commit()
            return tokens

    async def load_access_token(self, token: str) -> AccessToken | None:
        return await anyio.to_thread.run_sync(self._load_access, token)

    def _load_access(self, value: str) -> AccessToken | None:
        with Session(self.engine) as db:
            found = self._token(db, value, ACCESS)
        if found is None:
            return None
        token, grant = found
        return AccessToken(
            token=value,
            client_id=grant.client_id,
            # The grant is authoritative, so permission changes apply to live tokens at once.
            scopes=grant.scopes,
            expires_at=int(_aware(token.expires_at).timestamp()),
            resource=token.resource,
            subject=str(grant.owner_id),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await anyio.to_thread.run_sync(self._revoke, token.token)

    def _revoke(self, value: str) -> None:
        with Session(self.engine) as db:
            grant_id = db.scalar(
                select(OAuthToken.grant_id).where(OAuthToken.token_hash == token_hash(value))
            )
            if grant_id is not None:
                revoke_grant(db, grant_id)
                db.commit()


def require_mcp(request: Request) -> McpUrls:
    urls: McpUrls | None = getattr(request.app.state, "mcp_urls", None)
    if urls is None:
        raise HTTPException(404, "mcp_disabled")
    return urls


Mcp = Annotated[McpUrls, Depends(require_mcp)]
RequestToken = Annotated[str, Path(min_length=16, max_length=128)]
router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


def _pending(db: Session, request: str) -> tuple[OAuthAuthorizationRequest, OAuthClient]:
    row = db.execute(
        select(OAuthAuthorizationRequest, OAuthClient)
        .join(OAuthClient, OAuthAuthorizationRequest.client_id == OAuthClient.id)
        .where(
            OAuthAuthorizationRequest.token_hash == token_hash(request),
            OAuthAuthorizationRequest.expires_at > _now(),
        )
    ).first()
    if row is None:
        raise HTTPException(404, "authorization_not_found")
    return row[0], row[1]


@router.get("/authorizations/{request}", response_model=McpAuthorizationOutput)
def authorization(
    request: RequestToken, _urls: Mcp, _user: Owner, db: Db
) -> McpAuthorizationOutput:
    pending, client = _pending(db, request)
    params = pending.params
    return McpAuthorizationOutput(
        client_name=client.name,
        redirect_host=urlparse(str(params.get("redirect_uri", ""))).hostname or "",
        scopes=list(params.get("scopes", [])),
    )


@router.post("/authorizations/{request}", response_model=McpConsentOutput)
def decide(
    request: RequestToken, payload: McpConsentInput, _urls: Mcp, user: Owner, db: Db
) -> McpConsentOutput:
    pending, client = _pending(db, request)
    # Single use: only the request that deletes the row continues.
    removed = db.scalar(
        delete(OAuthAuthorizationRequest)
        .where(OAuthAuthorizationRequest.token_hash == pending.token_hash)
        .returning(OAuthAuthorizationRequest.token_hash)
    )
    if removed is None:
        raise HTTPException(404, "authorization_not_found")
    params = pending.params
    redirect_uri = str(params.get("redirect_uri", ""))
    state = params.get("state")
    state = state if isinstance(state, str) else None
    if not payload.approve:
        db.commit()
        return McpConsentOutput(
            redirect_url=construct_redirect_uri(redirect_uri, error="access_denied", state=state)
        )
    requested = params.get("scopes", [])
    scopes = granted_scopes(
        payload.scopes, offline=isinstance(requested, list) and OFFLINE_SCOPE in requested
    )
    code = token_urlsafe(32)
    db.add(
        OAuthCode(
            token_hash=token_hash(code),
            client_id=client.id,
            owner_id=user.id,
            params={**params, "scopes": scopes},
            expires_at=_now() + CODE_TTL,
        )
    )
    db.commit()
    return McpConsentOutput(
        redirect_url=construct_redirect_uri(redirect_uri, code=code, state=state)
    )


def _grant_rows(db: Session, owner_id: UUID, grant_id: UUID | None = None) -> list[McpGrantOutput]:
    # Refresh tokens rotate on every use, so the newest one dates the last activity.
    refreshed = (
        select(OAuthToken.grant_id, func.max(OAuthToken.expires_at).label("expires_at"))
        .where(OAuthToken.kind == REFRESH)
        .group_by(OAuthToken.grant_id)
        .subquery()
    )
    query = (
        select(
            OAuthGrant.id,
            OAuthClient.name,
            OAuthGrant.created_at,
            OAuthGrant.scopes,
            refreshed.c.expires_at,
        )
        .join(OAuthClient, OAuthGrant.client_id == OAuthClient.id)
        .outerjoin(refreshed, refreshed.c.grant_id == OAuthGrant.id)
        .where(OAuthGrant.owner_id == owner_id)
        .order_by(OAuthGrant.created_at.desc(), OAuthGrant.id)
        .limit(100)
    )
    if grant_id is not None:
        query = query.where(OAuthGrant.id == grant_id)
    known = {scope.value for scope in McpScope}
    return [
        McpGrantOutput(
            id=row_id,
            client_name=name,
            created_at=_aware(created_at),
            scopes=[McpScope(scope) for scope in scopes if scope in known],
            last_used_at=None if expires_at is None else _aware(expires_at) - REFRESH_TTL,
        )
        for row_id, name, created_at, scopes, expires_at in db.execute(query)
    ]


@router.get("/grants", response_model=McpGrantsOutput)
def grants(_urls: Mcp, user: Owner, db: Db) -> McpGrantsOutput:
    return McpGrantsOutput(items=_grant_rows(db, user.id))


@router.patch("/grants/{grant_id}", response_model=McpGrantOutput)
def update_grant(
    grant_id: UUID, payload: McpGrantInput, _urls: Mcp, user: Owner, db: Db
) -> McpGrantOutput:
    grant = db.scalar(
        select(OAuthGrant).where(OAuthGrant.id == grant_id, OAuthGrant.owner_id == user.id)
    )
    if grant is None:
        raise HTTPException(404, "grant_not_found")
    grant.scopes = granted_scopes(payload.scopes, offline=OFFLINE_SCOPE in grant.scopes)
    db.commit()
    return _grant_rows(db, user.id, grant_id)[0]


@router.delete("/grants/{grant_id}", status_code=204)
def revoke(grant_id: UUID, _urls: Mcp, user: Owner, db: Db) -> None:
    owned = db.scalar(
        select(OAuthGrant.id).where(OAuthGrant.id == grant_id, OAuthGrant.owner_id == user.id)
    )
    if owned is None:
        raise HTTPException(404, "grant_not_found")
    revoke_grant(db, owned)
    db.commit()
