"""Durable receipt synchronization regressions; only synthetic provider data."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from os import environ
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from cartalytix.credentials import TokenCipher
from cartalytix.models import (
    Base,
    Discount,
    ProviderConnection,
    RawDocument,
    Receipt,
    ReceiptItem,
    SyncRun,
    User,
)
from cartalytix.providers.lidl.client import LidlClient
from cartalytix.settings import Settings
from cartalytix.sync import (
    SyncError,
    connection_lock,
    enqueue_sync,
    publish_pending,
    run_sync,
    schedule_due,
)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[tuple[Engine, Settings, UUID, UUID]]:
    key = tmp_path / "token.key"
    key.write_bytes(Fernet.generate_key())
    url = environ.get("CARTALYTIX_TEST_DATABASE_URL") or environ.get("DATABASE_URL")
    if url:
        parsed = make_url(url)
        if (
            environ.get("CARTALYTIX_ENVIRONMENT") != "test"
            or not parsed.drivername.startswith("postgresql")
            or not (parsed.database or "").endswith("_test")
        ):
            pytest.fail("PostgreSQL sync tests require test environment and _test database")
    config = Settings(
        environment="test",
        database_url=url or f"sqlite:///{tmp_path / 'sync.db'}",
        token_key_file=str(key),
    )
    database_engine = create_engine(config.database_url)
    schema = f"sync_{uuid4().hex}" if url else None
    engine = database_engine
    if schema:
        with database_engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = database_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="sync@example.com", password_hash="unused")
        db.add(owner)
        db.flush()
        connection = ProviderConnection(
            owner_id=owner.id,
            provider="lidl",
            name="Synthetic",
            status="connected",
            encrypted_refresh_token=TokenCipher(config).encrypt("old"),
        )
        db.add(connection)
        db.commit()
        owner_id, connection_id = owner.id, connection.id
    try:
        yield engine, config, owner_id, connection_id
    finally:
        if schema:
            with database_engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        database_engine.dispose()


def detail(identifier: str, *, amount: str = "20.00") -> dict[str, Any]:
    return {
        "id": identifier,
        "date": "2026-01-01T10:00:00",
        "currency": {"code": "CZK"},
        "totalAmount": amount,
        "itemsLine": [
            {
                "name": "Milk",
                "quantity": "2",
                "currentUnitPrice": "12",
                "originalAmount": "24",
                "isWeight": False,
                "discounts": [{"description": "Coupon", "amount": "4"}],
            }
        ],
    }


class Provider:
    def __init__(
        self, pages: dict[int, dict[str, Any]], details: dict[str, dict[str, Any] | Exception]
    ) -> None:
        self.pages = pages
        self.details = details
        self.detail_calls: list[str] = []
        self.page_calls: list[int] = []
        self.closed = False

    def factory(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> Provider:
        assert refresh_token in {"old", "rotated"}
        save_refresh("rotated")
        return self

    def page(self, number: int) -> dict[str, Any]:
        self.page_calls.append(number)
        return self.pages.get(number, {"tickets": [], "totalCount": 2})

    def detail(self, identifier: str) -> dict[str, Any]:
        self.detail_calls.append(identifier)
        result = self.details.get(identifier)
        if isinstance(result, Exception):
            raise result
        assert result is not None
        return result

    def close(self) -> None:
        self.closed = True


def test_sync_keeps_decimal_rows_rotation_and_is_idempotent(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {
            1: {"tickets": [{"id": "one"}], "totalCount": 2, "size": 50},
            2: {"tickets": [{"id": "two"}], "totalCount": 2},
        },
        {"one": detail("one"), "two": detail("two")},
    )
    run = enqueue_sync(engine, connection, owner)
    assert enqueue_sync(engine, connection, owner).id == run.id
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "completed"
        assert (stored.receipts_seen, stored.receipts_imported, stored.pages_count) == (2, 2, 2)
        rows = list(db.scalars(select(Receipt).order_by(Receipt.external_id)))
        assert len(rows) == 2 and rows[0].total_amount == Decimal("20.00")
        assert rows[0].purchased_at.replace(tzinfo=UTC) == datetime(2026, 1, 1, 9, tzinfo=UTC)
        item = db.scalar(select(ReceiptItem))
        assert (
            item is not None
            and item.quantity == Decimal("2")
            and item.total_amount == Decimal("24")
        )
        discount = db.scalar(select(Discount))
        assert discount is not None and discount.amount == Decimal("4")
        linked = db.get(ProviderConnection, connection)
        assert linked is not None and linked.encrypted_refresh_token is not None
        assert TokenCipher(config).decrypt(linked.encrypted_refresh_token) == "rotated"
    assert provider.page_calls == [1, 2, 1] and provider.closed
    again = enqueue_sync(engine, connection, owner)
    run_sync(engine, again.id, config, client_factory=provider.factory)
    assert provider.detail_calls == ["one", "two"]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Receipt)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.external_id == "one")
            )
            == 1
        )


@pytest.mark.parametrize(
    "pages",
    [
        {1: {"tickets": [{"id": "one"}], "totalCount": 2}, 2: {"tickets": [], "totalCount": 2}},
        {
            1: {"tickets": [{"id": "one"}], "totalCount": 2},
            2: {"tickets": [{"id": "one"}], "totalCount": 2},
        },
        {
            1: {"tickets": [{"id": "one"}], "totalCount": 2},
            2: {"tickets": [{"id": "two"}], "totalCount": 3},
        },
    ],
)
def test_incomplete_history_never_claims_success(
    database: tuple[Engine, Settings, UUID, UUID], pages: dict[int, dict[str, Any]]
) -> None:
    engine, config, owner, connection = database
    provider = Provider(pages, {})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert (
            stored is not None
            and stored.status == "failed"
            and stored.error_code == "history_incomplete"
        )
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0


def test_invalid_detail_raw_survives_without_receipt(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {1: {"tickets": [{"id": "one"}], "totalCount": 1}},
        {"one": {"id": "wrong", "secret": "private"}},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert (
            stored is not None
            and stored.status == "failed"
            and stored.error_code == "receipt_identity_mismatch"
        )
        raw = db.scalar(select(RawDocument).where(RawDocument.external_id == "one"))
        assert raw is not None and b'"id":"wrong"' in raw.payload and raw.parser_version
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0


def test_partial_failure_preserves_committed_receipt_and_sanitizes_error(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {1: {"tickets": [{"id": "one"}, {"id": "two"}], "totalCount": 2}},
        {"one": detail("one"), "two": RuntimeError("private-token")},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert (
            stored is not None and stored.status == "failed" and stored.error_code == "sync_failed"
        )
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1


def test_owner_and_lock_prevent_unauthorized_or_parallel_execution(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with pytest.raises(SyncError, match="connection_not_found"):
        enqueue_sync(engine, connection, uuid4())
    run = enqueue_sync(engine, connection, owner)
    provider = Provider({}, {})
    with connection_lock(engine, connection) as locked:
        assert locked
        with connection_lock(engine, connection) as second:
            assert not second
        run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "pending"
    assert not provider.page_calls


def test_outbox_replays_lost_publish_and_recovers_only_unlocked_stale_run(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, _, owner, connection = database
    run = enqueue_sync(engine, connection, owner)

    def offline(identifier: str) -> None:
        raise OSError("broker unavailable")

    publish_pending(engine, offline)
    sent: list[str] = []
    publish_pending(engine, sent.append)
    assert sent == []
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        stored.last_dispatched_at = datetime.now(UTC) - timedelta(minutes=2)
        stored.status = "running"
        stored.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()
    with connection_lock(engine, connection):
        publish_pending(engine, sent.append)
    assert sent == []
    publish_pending(engine, sent.append)
    assert sent == [str(run.id)]
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "pending"


def test_scheduler_deduplicates_due_and_keeps_manual_connections_manual(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, _, _, connection = database
    assert schedule_due(engine) == 0
    with Session(engine) as db:
        linked = db.get(ProviderConnection, connection)
        assert linked is not None
        linked.sync_interval_minutes = 60
        linked.next_sync_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    assert schedule_due(engine) == 1
    assert schedule_due(engine) == 0


def test_quality_refresh_preserves_previous_good_receipt(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    pages = {1: {"tickets": [{"id": "one"}], "totalCount": 1}}
    valid = Provider(pages, {"one": detail("one")})
    first = enqueue_sync(engine, connection, owner)
    run_sync(engine, first.id, config, client_factory=valid.factory)
    invalid = Provider(pages, {"one": detail("one", amount="999")})
    refresh = enqueue_sync(engine, connection, owner, refresh_existing=True)
    run_sync(engine, refresh.id, config, client_factory=invalid.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, refresh.id)
        assert (
            stored is not None
            and stored.status == "failed"
            and stored.error_code == "quality_error"
        )
        receipt = db.scalar(select(Receipt))
        assert receipt is not None and receipt.total_amount == Decimal("20")
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.external_id == "one")
            )
            == 2
        )


def test_quality_error_skips_bad_document_but_imports_later_good_one(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    provider = Provider(
        {1: {"tickets": [{"id": "bad"}, {"id": "good"}], "totalCount": 2}},
        {"bad": detail("bad", amount="999"), "good": detail("good")},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert (
            stored is not None
            and stored.error_code == "quality_error"
            and stored.receipts_imported == 1
        )
        assert list(db.scalars(select(Receipt.external_id))) == ["good"]
        assert (
            db.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.external_id == "bad")
            )
            == 1
        )


def test_missing_quantity_is_never_defaulted_to_one(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    payload = detail("one")
    payload.get("itemsLine", [])[0].pop("quantity")
    provider = Provider({1: {"tickets": [{"id": "one"}], "totalCount": 1}}, {"one": payload})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.error_code == "quality_error"
        assert db.scalar(select(func.count()).select_from(ReceiptItem)) == 0


def test_repeated_source_lines_keep_distinct_positions_and_total_discount_is_not_duplicated(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    payload = detail("one", amount="40")
    payload.get("itemsLine", []).append(dict(payload.get("itemsLine", [])[0]))
    payload["totalDiscount"] = "8"
    provider = Provider({1: {"tickets": [{"id": "one"}], "totalCount": 1}}, {"one": payload})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "completed"
        assert list(db.scalars(select(ReceiptItem.position).order_by(ReceiptItem.position))) == [
            1,
            2,
        ]
        assert db.scalar(select(func.sum(Discount.amount))) == Decimal("8")


def test_changed_first_page_is_incomplete(database: tuple[Engine, Settings, UUID, UUID]) -> None:
    class ChangingProvider(Provider):
        def page(self, number: int) -> dict[str, Any]:
            result = super().page(number)
            if len(self.page_calls) > 1:
                return {"tickets": [{"id": "changed"}], "totalCount": 1}
            return result

    engine, config, owner, connection = database
    provider = ChangingProvider({1: {"tickets": [{"id": "one"}], "totalCount": 1}}, {})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.error_code == "history_incomplete"
        assert db.scalar(select(func.count()).select_from(Receipt)) == 0


def test_printed_grams_are_stored_as_grams(database: tuple[Engine, Settings, UUID, UUID]) -> None:
    engine, config, owner, connection = database
    payload = {
        "id": "grams",
        "date": "2026-01-01T10:00:00",
        "currency": "CZK",
        "totalAmount": "25",
        "htmlPrintedReceipt": '<span id="purchase_list_line_1">Sample 25,00 B</span>'
        '<span id="purchase_list_line_2">250 g x 0,10</span>',
    }
    provider = Provider({1: {"tickets": [{"id": "grams"}], "totalCount": 1}}, {"grams": payload})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "completed"
        item = db.scalar(select(ReceiptItem))
        assert item is not None and item.unit == "g" and item.quantity == Decimal("250")
        assert item.unit_price == Decimal("0.10")


def test_excess_precision_stays_raw_and_does_not_round_into_ledger(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    bad = detail("bad", amount="0.10")
    bad["itemsLine"] = [
        {
            "name": "Sample",
            "quantity": "10000000",
            "currentUnitPrice": "0.00000001",
            "originalAmount": "0.10",
        }
    ]
    provider = Provider(
        {1: {"tickets": [{"id": "bad"}, {"id": "good"}], "totalCount": 2}},
        {"bad": bad, "good": detail("good")},
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=provider.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.error_code == "quality_error"
        assert list(db.scalars(select(Receipt.external_id))) == ["good"]
        assert db.scalar(select(func.count()).select_from(ReceiptItem)) == 1
        raw = db.scalar(select(RawDocument).where(RawDocument.external_id == "bad"))
        assert raw is not None and b'"0.00000001"' in raw.payload


def test_original_response_bytes_are_archived_before_normalization(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    raw = (
        '{ "id": "one", "date": "2026-01-01", "currency": "CZK", "totalAmount": 0.10,\n'
        ' "itemsLine": [{"name": "Mléko", "quantity": 1, "isWeight": false,'
        ' "currentUnitPrice": 0.10, "originalAmount": 0.10}] }\n'
    ).encode()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json={"access_token": "access", "refresh_token": "rotated", "expires_in": 3600}
            )
        if request.url.path.endswith("tickets"):
            return httpx.Response(200, json={"tickets": [{"id": "one"}], "totalCount": 1})
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:

        def factory(
            refresh_token: str,
            save_refresh: Callable[[str], None],
            *,
            heartbeat: Callable[[], None] | None = None,
        ) -> LidlClient:
            return LidlClient(refresh_token, save_refresh, http=http, heartbeat=heartbeat, delay=0)

        run = enqueue_sync(engine, connection, owner)
        run_sync(engine, run.id, config, client_factory=factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None and stored.status == "completed"
        document = db.scalar(select(RawDocument).where(RawDocument.external_id == "one"))
        assert document is not None and document.payload == raw
