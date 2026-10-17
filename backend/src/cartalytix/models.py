from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    connections: Mapped[list[ProviderConnection]] = relationship(lazy="raise")


class UserSession(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
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
    currency: Mapped[str] = mapped_column(String(3))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
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
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    receipt_id: Mapped[UUID] = mapped_column(ForeignKey("receipts.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
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
    item_id: Mapped[UUID | None] = mapped_column(ForeignKey("receipt_items.id"))
    name: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
