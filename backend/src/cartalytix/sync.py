"""Durable provider jobs, serialized token rotation and per-receipt imports."""

import hashlib
import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Engine, delete, or_, select, text, true, update
from sqlalchemy.orm import Session

from cartalytix.credentials import CredentialError, TokenCipher
from cartalytix.models import (
    Discount,
    ProviderConnection,
    RawDocument,
    Receipt,
    ReceiptItem,
    SyncRun,
)
from cartalytix.providers.bolt.client import BoltClient
from cartalytix.providers.bolt.normalization import PARSER_VERSION as BOLT_PARSER_VERSION
from cartalytix.providers.bolt.normalization import validate_order as validate_bolt_order
from cartalytix.providers.common import ProviderError, ProviderPayload
from cartalytix.providers.kaufland.client import KauflandClient
from cartalytix.providers.kaufland.normalization import PARSER_VERSION as KAUFLAND_PARSER_VERSION
from cartalytix.providers.kaufland.normalization import (
    validate_receipt as validate_kaufland_receipt,
)
from cartalytix.providers.lidl.client import LidlClient
from cartalytix.providers.lidl.normalization import PARSER_VERSION as LIDL_PARSER_VERSION
from cartalytix.providers.lidl.normalization import validate_receipt
from cartalytix.providers.mixit.client import MixitClient
from cartalytix.providers.mixit.normalization import PARSER_VERSION as MIXIT_PARSER_VERSION
from cartalytix.providers.mixit.normalization import validate_order
from cartalytix.providers.shoptet.client import client_factory as shoptet_client
from cartalytix.providers.shoptet.normalization import PARSER_VERSION as SHOPTET_PARSER_VERSION
from cartalytix.providers.shoptet.normalization import validate_order as validate_shoptet_order
from cartalytix.providers.wolt.client import WoltClient
from cartalytix.providers.wolt.normalization import PARSER_VERSION as WOLT_PARSER_VERSION
from cartalytix.providers.wolt.normalization import validate_order as validate_wolt_order
from cartalytix.settings import Settings

_ACTIVE = ("pending", "running")
_locks: dict[tuple[str, UUID], Lock] = {}
_locks_guard = Lock()


class SyncError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderClient(Protocol):
    def page(self, number: int) -> dict[str, Any]: ...
    def detail(self, receipt_id: str, /) -> dict[str, Any]: ...
    def close(self) -> None: ...


class ClientFactory(Protocol):
    def __call__(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> ProviderClient: ...


@dataclass(frozen=True)
class ProviderSpec:
    merchant: str
    parser_version: str
    client: ClientFactory
    list_key: str
    total_key: str
    id_key: str
    detail_id: Callable[[dict[str, Any]], object]
    # None means "not importable yet" (e.g. an unshipped order), not a failure.
    normalize: Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any] | None]


def _mixit_detail_id(detail: dict[str, Any]) -> object:
    order = detail.get("order")
    return order.get("hash") if isinstance(order, dict) else None


PROVIDERS: dict[str, ProviderSpec] = {
    "lidl": ProviderSpec(
        merchant="Lidl",
        parser_version=LIDL_PARSER_VERSION,
        client=LidlClient,
        list_key="tickets",
        total_key="totalCount",
        id_key="id",
        detail_id=lambda detail: detail.get("id"),
        normalize=lambda summary, detail, identifier: validate_receipt(
            {**summary, **detail}, identifier
        ),
    ),
    "mixit": ProviderSpec(
        merchant="Mixit",
        parser_version=MIXIT_PARSER_VERSION,
        client=MixitClient,
        list_key="orders",
        total_key="totalRecords",
        id_key="hash",
        detail_id=_mixit_detail_id,
        normalize=validate_order,
    ),
    **{
        shop: ProviderSpec(
            merchant=merchant,
            parser_version=SHOPTET_PARSER_VERSION,
            client=shoptet_client(shop),
            list_key="orders",
            total_key="totalRecords",
            id_key="code",
            detail_id=lambda detail: detail.get("id"),
            normalize=validate_shoptet_order,
        )
        for shop, merchant in (("fermato", "Fermato"), ("nejkafe", "Nejkafe"))
    },
    "bolt": ProviderSpec(
        merchant="Bolt Food",
        parser_version=BOLT_PARSER_VERSION,
        client=BoltClient,
        list_key="orders",
        total_key="totalRecords",
        id_key="order_id",
        detail_id=lambda detail: str(detail.get("order_id")),
        normalize=validate_bolt_order,
    ),
    "wolt": ProviderSpec(
        merchant="Wolt",
        parser_version=WOLT_PARSER_VERSION,
        client=WoltClient,
        list_key="orders",
        total_key="totalRecords",
        id_key="purchase_id",
        detail_id=lambda detail: detail.get("order_id"),
        normalize=validate_wolt_order,
    ),
    "kaufland": ProviderSpec(
        merchant="Kaufland",
        parser_version=KAUFLAND_PARSER_VERSION,
        client=KauflandClient,
        list_key="transactions",
        total_key="totalRecords",
        id_key="id",
        detail_id=lambda detail: detail.get("id"),
        normalize=validate_kaufland_receipt,
    ),
}


@contextmanager
def connection_lock(engine: Engine, connection_id: UUID) -> Generator[bool]:
    """Session advisory lock stays outside all receipt/network transactions."""
    if engine.dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(connection_id.bytes).digest()[:8], "big", signed=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = bool(
                connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            )
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
        return
    with _locks_guard:
        lock = _locks.setdefault((str(engine.url), connection_id), Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def _enqueue(
    db: Session, connection: ProviderConnection, refresh_existing: bool
) -> tuple[SyncRun, bool]:
    existing = db.scalar(
        select(SyncRun).where(SyncRun.connection_id == connection.id, SyncRun.status.in_(_ACTIVE))
    )
    if existing is not None:
        return existing, False
    run = SyncRun(connection_id=connection.id, refresh_existing=refresh_existing)
    db.add(run)
    db.flush()
    return run, True


def enqueue_sync(
    engine: Engine, connection_id: UUID, owner_id: UUID, refresh_existing: bool = False
) -> SyncRun:
    with Session(engine, expire_on_commit=False) as db, db.begin():
        connection = db.scalar(
            select(ProviderConnection)
            .where(ProviderConnection.id == connection_id, ProviderConnection.owner_id == owner_id)
            .with_for_update()
        )
        if connection is None:
            raise SyncError("connection_not_found")
        if connection.provider not in PROVIDERS:
            raise SyncError("connector_unavailable")
        if not connection.encrypted_refresh_token or connection.status in {
            "not_connected",
            "reauth_required",
        }:
            raise SyncError("auth_required")
        run, _ = _enqueue(db, connection, refresh_existing)
        return run


def schedule_due(engine: Engine) -> int:
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        connections = list(
            db.scalars(
                select(ProviderConnection)
                .where(
                    ProviderConnection.provider.in_(tuple(PROVIDERS)),
                    ProviderConnection.status.in_(("connected", "error")),
                    ProviderConnection.encrypted_refresh_token.is_not(None),
                    ProviderConnection.sync_interval_minutes.is_not(None),
                    ProviderConnection.next_sync_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        )
        active = set(db.scalars(select(SyncRun.connection_id).where(SyncRun.status.in_(_ACTIVE))))
        count = 0
        for connection in connections:
            if connection.id not in active:
                db.add(SyncRun(connection_id=connection.id, refresh_existing=False))
                count += 1
            connection.next_sync_at = now + timedelta(
                minutes=connection.sync_interval_minutes or 60
            )
        return count


def publish_pending(
    engine: Engine, sender: Callable[[str], None], *, run_id: UUID | None = None
) -> None:
    now = datetime.now(UTC)
    retry_before = now - timedelta(seconds=60)
    stale_before = now - timedelta(minutes=5)
    with Session(engine) as db:
        candidates = list(
            db.execute(
                select(SyncRun.id, SyncRun.connection_id).where(
                    SyncRun.status.in_(_ACTIVE),
                    SyncRun.id == run_id if run_id is not None else true(),
                    or_(
                        SyncRun.last_dispatched_at.is_(None),
                        SyncRun.last_dispatched_at <= retry_before,
                    ),
                )
            )
        )
    for run_id, connection_id in candidates:
        dispatch = False
        with connection_lock(engine, connection_id) as acquired:
            if not acquired:
                continue
            with Session(engine) as db, db.begin():
                run = db.scalar(
                    select(SyncRun)
                    .where(
                        SyncRun.id == run_id,
                        SyncRun.status.in_(_ACTIVE),
                        or_(
                            SyncRun.last_dispatched_at.is_(None),
                            SyncRun.last_dispatched_at <= retry_before,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                )
                if run is None:
                    continue
                heartbeat = run.heartbeat_at or run.started_at or run.created_at
                if run.status == "running" and _aware(heartbeat) > stale_before:
                    continue
                run.status = "pending"
                run.last_dispatched_at = now
                dispatch = True
        if dispatch:
            try:
                sender(str(run_id))
            except Exception:
                # DB outbox retries after the lease; broker failures never erase jobs.
                continue


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _heartbeat(engine: Engine, run_id: UUID) -> None:
    with Session(engine) as db, db.begin():
        db.execute(
            update(SyncRun).where(SyncRun.id == run_id).values(heartbeat_at=datetime.now(UTC))
        )


def _raw(
    engine: Engine,
    connection_id: UUID,
    external_id: str,
    value: dict[str, Any],
    parser_version: str = LIDL_PARSER_VERSION,
) -> UUID:
    payload = (
        value.raw
        if isinstance(value, ProviderPayload)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )
    checksum = hashlib.sha256(payload).hexdigest()
    with Session(engine) as db, db.begin():
        document = db.scalar(
            select(RawDocument).where(
                RawDocument.connection_id == connection_id,
                RawDocument.external_id == external_id,
                RawDocument.checksum == checksum,
            )
        )
        if document is None:
            document = RawDocument(
                connection_id=connection_id,
                external_id=external_id,
                payload=payload,
                checksum=checksum,
                parser_version=parser_version,
            )
            db.add(document)
            db.flush()
        return document.id


def _page(
    data: dict[str, Any], spec: ProviderSpec = PROVIDERS["lidl"]
) -> tuple[list[dict[str, Any]], int | None]:
    tickets = data.get(spec.list_key)
    total = data.get(spec.total_key)
    if not isinstance(tickets, list) or (
        total is not None and (type(total) is not int or total < 0)
    ):
        raise SyncError("history_incomplete")
    if any(
        not isinstance(ticket, dict) or not _valid_id(ticket.get(spec.id_key)) for ticket in tickets
    ):
        raise SyncError("history_incomplete")
    return tickets, total


def _valid_id(value: object) -> bool:
    # Bolt uses numeric order ids; they are stored as their decimal string form.
    if isinstance(value, int) and not isinstance(value, bool):
        return value >= 0
    return isinstance(value, str) and 0 < len(value) <= 256


def _history(
    engine: Engine,
    run_id: UUID,
    connection_id: UUID,
    client: ProviderClient,
    spec: ProviderSpec = PROVIDERS["lidl"],
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    total: int | None = None
    first_signature: tuple[list[str], int | None] | None = None
    for number in range(1, 10001):
        data = client.page(number)
        _raw(engine, connection_id, f"list:{run_id}:{number}", data, spec.parser_version)
        tickets, page_total = _page(data, spec)
        if page_total is not None:
            if total is not None and total != page_total:
                raise SyncError("history_incomplete")
            total = page_total
        ids = [str(ticket.get(spec.id_key)) for ticket in tickets]
        if first_signature is None:
            first_signature = (ids, page_total)
        for ticket in tickets:
            identifier = str(ticket.get(spec.id_key))
            if identifier in summaries:
                raise SyncError("history_incomplete")
            summaries[identifier] = ticket
        with Session(engine) as db, db.begin():
            run = db.get(SyncRun, run_id)
            if run is not None:
                run.pages_count = number
                run.receipts_seen = len(summaries)
                run.heartbeat_at = datetime.now(UTC)
        if total is not None and len(summaries) > total:
            raise SyncError("history_incomplete")
        if not tickets or (total is not None and len(summaries) == total):
            if total is not None and len(summaries) != total:
                raise SyncError("history_incomplete")
            break
    else:
        raise SyncError("history_incomplete")
    check = client.page(1)
    _raw(engine, connection_id, f"list:{run_id}:check", check, spec.parser_version)
    tickets, check_total = _page(check, spec)
    if ([str(ticket.get(spec.id_key)) for ticket in tickets], check_total) != first_signature:
        raise SyncError("history_incomplete")
    return summaries


def _decimal(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise SyncError("quality_error")
    result = Decimal(str(value))
    if not result.is_finite():
        raise SyncError("quality_error")
    return result


def _save_receipt(
    engine: Engine,
    connection_id: UUID,
    external_id: str,
    raw_id: UUID,
    normalized: dict[str, Any],
    merchant: str = "Lidl",
) -> None:
    date = datetime.fromisoformat(str(normalized.get("date")))
    if date.tzinfo is None:
        date = date.replace(tzinfo=ZoneInfo("Europe/Prague"))
    items = normalized.get("items", [])
    if not items or not normalized.get("currency"):
        raise SyncError("quality_error")
    with Session(engine) as db, db.begin():
        receipt = db.scalar(
            select(Receipt).where(
                Receipt.connection_id == connection_id, Receipt.external_id == external_id
            )
        )
        if receipt is None:
            receipt = Receipt(connection_id=connection_id, external_id=external_id)
            db.add(receipt)
        else:
            db.execute(delete(Discount).where(Discount.receipt_id == receipt.id))
            db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id))
        receipt.raw_document_id = raw_id
        receipt.purchased_at = date.astimezone(UTC)
        receipt.merchant = merchant
        receipt.currency = str(normalized.get("currency"))
        receipt.total_amount = _decimal(normalized.get("total"))
        receipt.rounding_amount = _decimal(normalized.get("rounding") or "0")
        receipt.quality_issues = normalized.get("issues", [])
        receipt.source_format = normalized.get("source_format", "itemsLine")
        db.flush()
        discounts: list[Discount] = []
        for position, item in enumerate(items, 1):
            row = ReceiptItem(
                id=uuid4(),
                receipt_id=receipt.id,
                position=position,
                name=item.get("name"),
                quantity=_decimal(item.get("quantity")),
                unit=item.get("unit"),
                unit_price=_decimal(item.get("unit_price")),
                total_amount=_decimal(item.get("original_amount")),
                original_amount=_decimal(item.get("original_amount")),
                source_code=str(item.get("code")) if item.get("code") is not None else None,
                is_weight=bool(item.get("is_weight")),
                deposit_amount=_decimal(item.get("deposit_amount"))
                if item.get("deposit_amount") is not None
                else None,
                printed_net_amount=_decimal(item.get("printed_net_amount"))
                if item.get("printed_net_amount") is not None
                else None,
                warranty=item.get("warranty"),
            )
            db.add(row)
            for discount in item.get("discounts", []):
                discounts.append(
                    Discount(
                        receipt_id=receipt.id,
                        item_id=row.id,
                        name=str(discount.get("description") or discount.get("name") or "Discount"),
                        amount=_decimal(discount.get("amount")),
                    )
                )
        discounts.extend(
            Discount(
                receipt_id=receipt.id,
                item_id=None,
                name=str(discount.get("name") or "Discount"),
                amount=_decimal(discount.get("amount")),
            )
            for discount in normalized.get("discounts", [])
        )
        db.flush()
        db.add_all(discounts)


def run_sync(
    engine: Engine,
    run_id: UUID,
    config: Settings | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> None:
    with Session(engine) as db:
        run = db.get(SyncRun, run_id)
        if run is None or run.status not in _ACTIVE:
            return
        connection_id = run.connection_id
    with connection_lock(engine, connection_id) as acquired:
        if not acquired:
            return
        client: ProviderClient | None = None
        try:
            with Session(engine) as db, db.begin():
                run = db.get(SyncRun, run_id)
                connection = db.get(ProviderConnection, connection_id)
                if run is None or run.status not in _ACTIVE:
                    return
                if connection is None or connection.provider not in PROVIDERS:
                    raise SyncError("connection_not_found")
                spec = PROVIDERS[connection.provider]
                encrypted = connection.encrypted_refresh_token
                if not encrypted or connection.status in {"not_connected", "reauth_required"}:
                    raise SyncError("auth_required")
                refresh_existing = run.refresh_existing
                run.status = "running"
                run.started_at = datetime.now(UTC)
                run.heartbeat_at = run.started_at
                run.receipts_seen = run.receipts_imported = run.receipts_skipped = (
                    run.pages_count
                ) = 0
                run.error_code = None
            cipher = TokenCipher(config or Settings())

            def save_refresh(value: str) -> None:
                with Session(engine) as db, db.begin():
                    connection = db.get(ProviderConnection, connection_id)
                    if connection is None:
                        raise SyncError("connection_not_found")
                    connection.encrypted_refresh_token = cipher.encrypt(value)

            client = (client_factory or spec.client)(
                cipher.decrypt(encrypted),
                save_refresh,
                heartbeat=lambda: _heartbeat(engine, run_id),
            )
            summaries = _history(engine, run_id, connection_id, client, spec)
            with Session(engine) as db:
                existing = set(
                    db.scalars(
                        select(Receipt.external_id).where(Receipt.connection_id == connection_id)
                    )
                )
            with Session(engine) as db, db.begin():
                db.execute(
                    update(SyncRun)
                    .where(SyncRun.id == run_id)
                    .values(
                        receipts_skipped=sum(identifier in existing for identifier in summaries)
                        if not refresh_existing
                        else 0,
                        heartbeat_at=datetime.now(UTC),
                    )
                )
            quality_failed = False
            for identifier, summary in summaries.items():
                if identifier in existing and not refresh_existing:
                    continue
                detail = client.detail(identifier)
                raw_id = _raw(engine, connection_id, identifier, detail, spec.parser_version)
                if spec.detail_id(detail) != identifier:
                    raise SyncError("receipt_identity_mismatch")
                normalized = spec.normalize(summary, detail, identifier)
                if normalized is None:
                    with Session(engine) as db, db.begin():
                        run = db.get(SyncRun, run_id)
                        if run is not None:
                            run.receipts_skipped += 1
                    continue
                if normalized.get("issues"):
                    quality_failed = True
                    continue
                _save_receipt(
                    engine,
                    connection_id,
                    identifier,
                    raw_id,
                    normalized,
                    normalized.get("merchant") or spec.merchant,
                )
                with Session(engine) as db, db.begin():
                    run = db.get(SyncRun, run_id)
                    if run is not None:
                        run.receipts_imported += 1
                        run.heartbeat_at = datetime.now(UTC)
            if quality_failed:
                raise SyncError("quality_error")
            with Session(engine) as db, db.begin():
                run = db.get(SyncRun, run_id)
                connection = db.get(ProviderConnection, connection_id)
                if run is not None:
                    run.status = "completed"
                    run.finished_at = datetime.now(UTC)
                    run.heartbeat_at = run.finished_at
                if connection is not None:
                    connection.status = "connected"
                    connection.last_synced_at = datetime.now(UTC)
                    if connection.sync_interval_minutes:
                        connection.next_sync_at = datetime.now(UTC) + timedelta(
                            minutes=connection.sync_interval_minutes
                        )
        except Exception as error:
            if isinstance(error, (ProviderError, SyncError)):
                code = error.code
            elif isinstance(error, CredentialError):
                code = "credentials_unavailable"
            elif isinstance(error, SoftTimeLimitExceeded):
                code = "sync_timeout"
            else:
                code = "sync_failed"
            with Session(engine) as db, db.begin():
                run = db.get(SyncRun, run_id)
                connection = db.get(ProviderConnection, connection_id)
                if run is not None:
                    run.status = "failed"
                    run.error_code = code
                    run.finished_at = datetime.now(UTC)
                if connection is not None:
                    connection.status = "reauth_required" if code == "auth_required" else "error"
        finally:
            if client is not None:
                client.close()
