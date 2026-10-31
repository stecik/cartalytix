from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from cartalytix.search import SEARCH_FUNCTION_DDL


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


for statement in (
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS unaccent",
    SEARCH_FUNCTION_DDL,
):
    event.listen(Base.metadata, "before_create", DDL(statement).execute_if(dialect="postgresql"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    locale: Mapped[str] = mapped_column(String(2), default="en")
    receipt_view: Mapped[str] = mapped_column(
        String(16), default="rendered", server_default="rendered"
    )
    theme: Mapped[str] = mapped_column(String(16), default="system", server_default="system")
    dashboard_period: Mapped[str] = mapped_column(String(16), default="12m", server_default="12m")
    # Segments of the dashboard category charts, the last one folding the remaining categories.
    dashboard_category_slices: Mapped[int] = mapped_column(Integer, default=9, server_default="9")
    # Interval given to new connections; NULL means manual synchronization only.
    default_sync_interval_minutes: Mapped[int | None] = mapped_column(
        Integer, default=360, server_default="360"
    )
    # Administrators only manage accounts; they never own connections or receipts.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    connections: Mapped[list[ProviderConnection]] = relationship(lazy="raise")


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthClient(Base):
    """Dynamically registered MCP client (RFC 7591); the secret is encrypted at rest."""

    __tablename__ = "oauth_clients"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    registration: Mapped[dict[str, Any]] = mapped_column(JSON)
    encrypted_secret: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthAuthorizationRequest(Base):
    """Validated /authorize request waiting for the user's login and consent."""

    __tablename__ = "oauth_authorization_requests"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthGrant(Base):
    """A user's consent for one MCP client; revoking it removes every token issued under it."""

    __tablename__ = "oauth_grants"
    __table_args__ = (UniqueConstraint("owner_id", "client_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), index=True)
    # Authoritative permissions, editable by the owner; token rows keep what the client requested.
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthCode(Base):
    __tablename__ = "oauth_codes"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="SET NULL"), index=True
    )


class OAuthToken(Base):
    """Opaque access or refresh token, stored only as a SHA-256 hash."""

    __tablename__ = "oauth_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[UUID] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list[str]] = mapped_column(JSON)
    resource: Mapped[str] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    country: Mapped[str] = mapped_column(String(2), default="CZ")
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="not_connected")
    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(LargeBinary)
    account_subject: Mapped[str | None] = mapped_column(String(256))
    account_label: Mapped[str | None] = mapped_column(String(254))
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    receipts: Mapped[list[Receipt]] = relationship(lazy="raise")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        Index("ix_sync_runs_connection_created", "connection_id", "created_at"),
        Index(
            "ix_sync_runs_active_connection",
            "connection_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("provider_connections.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipts_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    receipts_imported: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    receipts_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    refresh_existing: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"
    __table_args__ = (Index("ix_diagnostic_events_owner_created", "owner_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    component: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (UniqueConstraint("connection_id", "external_id", "checksum"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("provider_connections.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(256))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    checksum: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id"),
        Index("ix_receipts_connection_purchased", "connection_id", "purchased_at"),
        Index(
            "ix_receipts_merchant_search",
            text("cartalytix_search_text(merchant) gin_trgm_ops"),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("provider_connections.id"))
    raw_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("raw_documents.id"))
    external_id: Mapped[str] = mapped_column(String(256))
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    merchant: Mapped[str] = mapped_column(String(256))
    # Provider whose shop a manually added receipt belongs to; synced receipts keep NULL.
    shop_provider: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    rounding_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=Decimal(0), server_default="0"
    )
    quality_issues: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    source_format: Mapped[str] = mapped_column(
        String(40), default="unknown", server_default="unknown"
    )
    items: Mapped[list[ReceiptItem]] = relationship(lazy="raise")
    discounts: Mapped[list[Discount]] = relationship(lazy="raise")


class ReceiptItem(Base):
    __tablename__ = "receipt_items"
    __table_args__ = (
        UniqueConstraint("receipt_id", "position"),
        Index(
            "ix_receipt_items_name_search",
            text("cartalytix_search_text(name) gin_trgm_ops"),
            postgresql_using="gin",
        ).ddl_if(dialect="postgresql"),
        Index("ix_receipt_items_name_receipt", "name", "receipt_id"),
        CheckConstraint(
            "included_quantity IS NULL"
            " OR (included_quantity >= 0 AND included_quantity <= quantity)",
            name="ck_receipt_items_included_quantity",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(ForeignKey("receipts.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    # Part of the quantity counted in statistics; NULL counts the whole line.
    included_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    source_code: Mapped[str | None] = mapped_column(String(256))
    is_weight: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    printed_net_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    warranty: Mapped[str | None] = mapped_column(Text)


class Discount(Base):
    __tablename__ = "discounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(ForeignKey("receipts.id"), index=True)
    item_id: Mapped[UUID | None] = mapped_column(ForeignKey("receipt_items.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("owner_id", "name_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(300))


class CategoryProduct(Base):
    """A durable product identity, independent of replaceable receipt line IDs."""

    __tablename__ = "category_products"
    __table_args__ = (UniqueConstraint("connection_id", "name_hash", "currency"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    name_hash: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    assignment_source: Mapped[str] = mapped_column(String(6))


class CategoryMembership(Base):
    __tablename__ = "category_memberships"
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("category_products.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class ChangeBatch(Base):
    """One applied set of spending changes; a repeated idempotency key replays its result."""

    __tablename__ = "change_batches"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key"),
        Index("ix_change_batches_owner_created", "owner_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Who made the change: the owner in the app ("user") or an MCP client acting for them.
    source: Mapped[str] = mapped_column(String(8), default="user", server_default="user")
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("oauth_clients.id", ondelete="SET NULL")
    )
    undo_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("change_batches.id", ondelete="SET NULL"), index=True
    )


class ChangeEntry(Base):
    """One changed value of a batch, kept with its previous value for history and undo."""

    __tablename__ = "change_entries"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("change_batches.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    receipt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="SET NULL"), index=True
    )
    # No foreign key: a refresh replaces line rows, re-inserting unchanged lines under their ID.
    item_id: Mapped[UUID | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
    before: Mapped[Any] = mapped_column(JSON, nullable=True)
    after: Mapped[Any] = mapped_column(JSON, nullable=True)
    undone_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("change_batches.id", ondelete="SET NULL")
    )


class ProductExclusion(Base):
    __tablename__ = "product_exclusions"
    __table_args__ = (UniqueConstraint("owner_id", "name", "currency"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3))
