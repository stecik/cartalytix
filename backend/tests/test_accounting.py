from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session

from cartalytix.models import Base, Discount, ProviderConnection, Receipt, ReceiptItem, User
from cartalytix.search import register_sqlite


@pytest.fixture
def engine() -> Iterator[Engine]:
    database = create_engine("sqlite://")
    event.listen(database, "connect", register_sqlite)
    Base.metadata.create_all(database)
    yield database
    database.dispose()


def basket(db: Session, *, total: str = "20.00") -> tuple[UUID, Receipt, list[ReceiptItem]]:
    owner = User(email="accounting@example.com", password_hash="unused")
    db.add(owner)
    db.flush()
    connection = ProviderConnection(owner_id=owner.id, provider="lidl", name="Test")
    db.add(connection)
    db.flush()
    receipt = Receipt(
        connection_id=connection.id,
        external_id="accounting",
        purchased_at=datetime.now(UTC),
        merchant="Lidl",
        currency="CZK",
        total_amount=Decimal(total),
    )
    db.add(receipt)
    db.flush()
    items = [
        ReceiptItem(
            receipt_id=receipt.id, position=position, name=name,
            quantity=Decimal(1), total_amount=Decimal(amount),
        )
        for position, (name, amount) in enumerate([("Food", "12.00"), ("Spatula", "10.00")])
    ]
    db.add_all(items)
    db.flush()
    db.add_all([
        Discount(receipt_id=receipt.id, item_id=items[0].id, name="Food offer", amount=Decimal(2)),
        Discount(receipt_id=receipt.id, name="Basket offer", amount=Decimal(2)),
    ])
    db.flush()
    return owner.id, receipt, items


def test_fixed_discount_share_and_receipt_reconciliation(engine: Engine) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="18.01")
        item_amounts, receipt_amounts = accounting(owner_id)
        rows = db.execute(select(item_amounts).order_by(item_amounts.c.position)).mappings().all()
        assert [row.get("net_amount") for row in rows] == [Decimal("9.00"), Decimal("9.00")]
        assert [row.get("included_amount") for row in rows] == [Decimal("9.01"), Decimal("9.00")]
        assert [row.get("included_discount_amount") for row in rows] == [Decimal(3), Decimal(1)]
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal("18.01")
        items[1].is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal("9.01")
        assert db.scalar(select(receipt_amounts.c.included_discount_amount)) == Decimal(3)
        assert db.scalar(select(func.sum(item_amounts.c.included_amount))) == Decimal("9.01")
        items[1].is_excluded = False
        receipt.is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(0)
        assert db.scalar(select(receipt_amounts.c.is_included)) is False


def test_global_exclusion_exact_name_currency_owner_and_restore(engine: Engine) -> None:
    from cartalytix.accounting import accounting
    from cartalytix.models import ProductExclusion

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="18.00")
        rule = ProductExclusion(owner_id=owner_id, name="Food", currency="EUR")
        db.add(rule)
        db.flush()
        item_amounts, receipt_amounts = accounting(owner_id)
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(18)
        rule.currency = "CZK"
        rule.name = "food"
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(18)
        rule.name = "Food"
        other = User(email="other@example.com", password_hash="unused")
        db.add(other)
        db.flush()
        rule.owner_id = other.id
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(18)
        rule.owner_id = owner_id
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(9)
        assert db.scalar(
            select(item_amounts.c.is_included).where(item_amounts.c.id == items[0].id)
        ) is False
        db.delete(rule)
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(18)
        items[0].is_excluded = True
        items[1].is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.is_included)) is False
        receipt.is_excluded = False


def test_no_item_receipt_keeps_original_amount_and_discount(engine: Engine) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="18.00")
        for discount in db.scalars(select(Discount)):
            db.delete(discount)
        for item in items:
            db.delete(item)
        db.add(Discount(receipt_id=receipt.id, name="Offer", amount=Decimal("2.50")))
        db.flush()
        _, receipt_amounts = accounting(owner_id)
        row = db.execute(select(receipt_amounts)).mappings().one()
        assert row.get("included_amount") == Decimal(18)
        assert row.get("included_discount_amount") == Decimal("2.50")
        assert row.get("is_included") is True
        receipt.is_excluded = True
        db.flush()
        row = db.execute(select(receipt_amounts)).mappings().one()
        assert row.get("included_amount") == Decimal(0)
        assert row.get("included_discount_amount") == Decimal(0)
        assert row.get("is_included") is False


def test_overview_uses_included_amount_counts_and_discounts(engine: Engine) -> None:
    from cartalytix.stats_routes import overview_stats

    with Session(engine) as db:
        owner_id, _receipt, items = basket(db, total="18.00")
        items[1].is_excluded = True
        db.flush()
        owner = db.get(User, owner_id)
        assert owner is not None
        stats = overview_stats(owner, db)
        assert stats.totals[0].amount == "9.00"
        assert stats.totals[0].discount_amount == "3.00"
        assert stats.totals[0].receipt_count == 1
        assert [(item.name, item.amount) for item in stats.top_spend_items] == [("Food", "9.00")]
        assert sum(sum(day) for day in stats.weekly_hours) == 1
        items[0].is_excluded = True
        db.flush()
        stats = overview_stats(owner, db)
        assert stats.totals == []
        assert stats.top_spend_items == []
        assert sum(sum(day) for day in stats.weekly_hours) == 0


@pytest.mark.parametrize("amount", ["0.00", "0.01", "-1.00"])
def test_zero_and_refund_allocations_stay_finite(engine: Engine, amount: str) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total=amount)
        for discount in db.scalars(select(Discount)):
            db.delete(discount)
        for item in items:
            item.total_amount = Decimal(amount)
        db.flush()
        item_amounts, receipt_amounts = accounting(owner_id)
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(amount)
        assert db.scalar(select(receipt_amounts.c.is_included)) is True
        items[0].is_excluded = True
        db.flush()
        remaining = db.scalar(select(receipt_amounts.c.included_amount))
        assert remaining is not None
        assert remaining.is_finite()
        assert db.scalar(
            select(item_amounts.c.included_amount).where(item_amounts.c.id == items[0].id)
        ) == Decimal(0)
        assert receipt.is_excluded is False


def test_cent_discount_allocation_conserves_original_basket(engine: Engine) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="2.99")
        for discount in db.scalars(select(Discount)):
            db.delete(discount)
        for item in items:
            item.total_amount = Decimal(1)
        db.add(
            ReceiptItem(
                receipt_id=receipt.id, position=2, name="Third", quantity=Decimal(1),
                total_amount=Decimal(1),
            )
        )
        db.add(Discount(receipt_id=receipt.id, name="Cent", amount=Decimal("0.01")))
        db.flush()
        item_amounts, receipt_amounts = accounting(owner_id)
        rows = db.execute(select(item_amounts).order_by(item_amounts.c.position)).mappings().all()
        assert [row.get("net_amount") for row in rows] == [
            Decimal("1.00"), Decimal("0.99"), Decimal("1.00")
        ]
        items[1].is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(2)
        assert db.scalar(select(receipt_amounts.c.included_discount_amount)) == Decimal(0)


def test_printed_net_is_metadata_and_deposit_tracks_its_item(engine: Engine) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="21.00")
        items[0].printed_net_amount = Decimal("10.00")
        items[0].deposit_amount = Decimal("3.00")
        db.flush()
        item_amounts, receipt_amounts = accounting(owner_id, [receipt.id])
        assert db.scalar(
            select(item_amounts.c.net_amount).where(item_amounts.c.id == items[0].id)
        ) == Decimal(12)
        items[0].is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal(9)
        _, empty = accounting(owner_id, [])
        assert db.scalar(select(empty.c.receipt_id)) is None

def test_refund_discount_keeps_proportional_original_shares(engine: Engine) -> None:
    from cartalytix.accounting import accounting

    with Session(engine) as db:
        owner_id, receipt, items = basket(db, total="-27.00")
        for discount in db.scalars(select(Discount)):
            db.delete(discount)
        items[0].total_amount = Decimal("-10.00")
        items[1].total_amount = Decimal("-20.00")
        db.add(
            Discount(receipt_id=receipt.id, name="Returned basket offer", amount=Decimal("-3.00"))
        )
        db.flush()
        item_amounts, receipt_amounts = accounting(owner_id)
        rows = db.execute(select(item_amounts).order_by(item_amounts.c.position)).mappings().all()
        assert [row.get("net_amount") for row in rows] == [Decimal("-9.00"), Decimal("-18.00")]
        items[1].is_excluded = True
        db.flush()
        assert db.scalar(select(receipt_amounts.c.included_amount)) == Decimal("-9.00")
        assert receipt.total_amount == Decimal("-27.00")
