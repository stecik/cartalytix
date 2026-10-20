"""Receipt refresh keeps local exclusions attached to matching source items."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from cartalytix.models import Base, ProviderConnection, RawDocument, Receipt, ReceiptItem, User
from cartalytix.sync import _save_receipt


@pytest.fixture
def database() -> Iterator[tuple[Engine, UUID]]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db, db.begin():
        owner = User(email="exclusion-sync@example.com", password_hash="unused")
        db.add(owner)
        db.flush()
        connection = ProviderConnection(owner_id=owner.id, provider="lidl", name="Test")
        db.add(connection)
        db.flush()
        connection_id = connection.id
        db.add(
            RawDocument(
                id=connection_id,
                connection_id=connection_id,
                external_id="receipt-1",
                payload=b"{}",
                checksum="synthetic",
                parser_version="test",
            )
        )
    try:
        yield engine, connection_id
    finally:
        engine.dispose()


def item(name: str, code: str = "1", amount: str = "10") -> dict[str, Any]:
    return {
        "name": name,
        "code": code,
        "quantity": "1",
        "unit_price": amount,
        "original_amount": amount,
        "discounts": [],
    }


def save(engine: Engine, connection_id: UUID, items: list[dict[str, Any]]) -> None:
    _save_receipt(
        engine,
        connection_id,
        "receipt-1",
        connection_id,
        {
            "date": "2026-10-19T10:00:00+02:00",
            "currency": "CZK",
            "total": "100",
            "items": items,
        },
    )


def test_refresh_preserves_receipt_and_reordered_item_exclusions(
    database: tuple[Engine, UUID],
) -> None:
    engine, connection_id = database
    milk, spatula = item("Milk"), item("Spatula", "2")
    save(engine, connection_id, [milk, spatula])
    with Session(engine) as db, db.begin():
        receipt = db.scalar(select(Receipt))
        assert receipt is not None
        receipt.is_excluded = True
        excluded = db.scalar(select(ReceiptItem).where(ReceiptItem.name == "Spatula"))
        assert excluded is not None
        excluded.is_excluded = True

    save(engine, connection_id, [spatula, milk, item("Bread", "3")])

    with Session(engine) as db:
        assert db.scalar(select(Receipt.is_excluded)) is True
        assert list(
            db.execute(
                select(ReceiptItem.name, ReceiptItem.is_excluded).order_by(ReceiptItem.position)
            )
        ) == [("Spatula", True), ("Milk", False), ("Bread", False)]


def test_refresh_preserves_duplicate_exclusion_count_without_excluding_new_duplicate(
    database: tuple[Engine, UUID],
) -> None:
    engine, connection_id = database
    milk = item("Milk")
    save(engine, connection_id, [milk, milk])
    with Session(engine) as db, db.begin():
        excluded = db.scalar(select(ReceiptItem).where(ReceiptItem.position == 2))
        assert excluded is not None
        excluded.is_excluded = True

    save(engine, connection_id, [milk, milk, milk])

    with Session(engine) as db:
        assert list(
            db.scalars(select(ReceiptItem.is_excluded).order_by(ReceiptItem.position))
        ) == [True, False, False]


def test_refresh_does_not_transfer_exclusion_to_changed_source_item(
    database: tuple[Engine, UUID],
) -> None:
    engine, connection_id = database
    save(engine, connection_id, [item("Milk")])
    with Session(engine) as db, db.begin():
        excluded = db.scalar(select(ReceiptItem))
        assert excluded is not None
        excluded.is_excluded = True

    save(engine, connection_id, [item("Bread"), item("Milk", "2"), item("Milk", amount="20")])

    with Session(engine) as db:
        assert list(db.scalars(select(ReceiptItem.is_excluded))) == [False, False, False]


def test_refresh_matches_duplicate_names_by_item_discounts(
    database: tuple[Engine, UUID],
) -> None:
    engine, connection_id = database
    full_price = item("Milk")
    discounted = {**full_price, "discounts": [{"description": "Coupon", "amount": "2"}]}
    save(engine, connection_id, [full_price, discounted])
    with Session(engine) as db, db.begin():
        excluded = db.scalar(select(ReceiptItem).where(ReceiptItem.position == 2))
        assert excluded is not None
        excluded.is_excluded = True

    save(engine, connection_id, [discounted, full_price])

    with Session(engine) as db:
        assert list(
            db.scalars(select(ReceiptItem.is_excluded).order_by(ReceiptItem.position))
        ) == [True, False]
