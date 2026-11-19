"""Albert imports use the shared sync engine, including complete archive history."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from test_lidl_sync import database as database

from cartalytix.models import ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.common import ProviderPayload
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync


class History:
    def __init__(self) -> None:
        self.detail_calls: list[str] = []

    def factory(
        self,
        credential: str,
        save_refresh: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> History:
        return self

    def page(self, number: int) -> dict[str, Any]:
        rows = [
            {"receiptNumber": f"r{index}", "receiptSum": "20.00"}
            for index in range((number - 1) * 20, min(number * 20, 23))
        ]
        return {"receipts": rows, "totalCount": 23}

    def detail(self, identifier: str) -> dict[str, Any]:
        self.detail_calls.append(identifier)
        detail = {
            "receiptNumber": identifier,
            "receiptCreationTime": "2026-01-02T10:00:00+01:00",
            "receiptSum": "20.00",
            "purchaseStatus": "",
            "purchasedItems": [
                {
                    "desc": "Milk",
                    "quantity": 2,
                    "pricePerItem": "10",
                    "totalPrice": "20",
                    "goldId": "product-1",
                }
            ],
            "discounts": [],
        }
        return ProviderPayload(detail, b'{"synthetic":true}')

    def close(self) -> None:
        pass


def test_full_archive_import_and_repeat_sync_preserve_existing_receipts(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "albert"
    history = History()
    first = enqueue_sync(engine, connection, owner)
    run_sync(engine, first.id, config, client_factory=history.factory)
    with Session(engine) as db:
        run = db.get(SyncRun, first.id)
        assert run is not None and run.status == "completed", run.error_code if run else None
        assert (run.receipts_seen, run.receipts_imported, run.pages_count) == (23, 23, 2)
        assert db.scalar(select(func.count()).select_from(Receipt)) == 23
        assert db.scalar(select(func.count()).select_from(ReceiptItem)) == 23
        receipt = db.scalar(select(Receipt).where(Receipt.external_id == "r22"))
        assert receipt is not None and receipt.merchant == "Albert"
        assert receipt.total_amount == Decimal("20.00")
        assert receipt.currency == "CZK"
    assert len(history.detail_calls) == 23
    second = enqueue_sync(engine, connection, owner)
    run_sync(engine, second.id, config, client_factory=history.factory)
    with Session(engine) as db:
        run = db.get(SyncRun, second.id)
        assert run is not None and run.status == "completed"
        assert (run.receipts_imported, run.receipts_skipped) == (0, 23)
        assert db.scalar(select(func.count()).select_from(Receipt)) == 23
    assert len(history.detail_calls) == 23
