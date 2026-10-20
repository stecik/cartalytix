from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import (
    Category,
    CategoryMembership,
    CategoryProduct,
    Discount,
    ProviderConnection,
    Receipt,
    ReceiptItem,
)

PRAGUE = ZoneInfo("Europe/Prague")
URL = "/api/v1/overview/stats"


def connection_id(engine: Engine, name: str = "My Lidl") -> UUID:
    with Session(engine) as db:
        identifier = db.scalar(select(ProviderConnection.id).where(ProviderConnection.name == name))
    assert identifier is not None
    return identifier


def add_receipt(
    engine: Engine,
    external_id: str,
    purchased_at: datetime,
    amount: str,
    *,
    items: list[tuple[str, str]] | None = None,
    discount: str | None = None,
    merchant: str = "Lidl",
    connection: str = "My Lidl",
) -> None:
    with Session(engine) as db:
        receipt = Receipt(
            connection_id=connection_id(engine, connection),
            external_id=external_id,
            purchased_at=purchased_at,
            merchant=merchant,
            currency="CZK",
            total_amount=Decimal(amount),
        )
        db.add(receipt)
        db.flush()
        for position, (name, total) in enumerate(items or []):
            db.add(
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=position,
                    name=name,
                    quantity=Decimal(1),
                    total_amount=Decimal(total),
                )
            )
        if discount is not None:
            db.add(
                Discount(receipt_id=receipt.id, name="Lidl Plus sleva", amount=Decimal(discount))
            )
        db.commit()


def totals(stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {total.get("currency"): total for total in stats.get("totals", [])}


def test_stats_require_session_and_valid_filters(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    login(client)
    assert client.get(f"{URL}?period=week").status_code == 422
    assert client.get(f"{URL}?bucket=2026-13").status_code == 422
    assert client.get(f"{URL}?bucket=26-01").status_code == 422


def test_period_totals_trend_and_discounts(client: TestClient, engine: Engine) -> None:
    now = datetime.now(UTC)
    add_receipt(engine, "recent", now - timedelta(days=1), "50.00", discount="7.50")
    add_receipt(engine, "previous", now - timedelta(days=400), "80.00")
    add_receipt(engine, "old", now - timedelta(days=1200), "300.00")
    add_receipt(engine, "foreign", now, "999.00", connection="Private Lidl")
    login(client)

    stats = client.get(URL).json()
    assert stats.get("period") == "12m"
    assert stats.get("currency") == "CZK"
    czk = totals(stats).get("CZK", {})
    assert czk.get("amount") == "150.25"
    assert czk.get("receipt_count") == 2
    assert czk.get("average") == "75.13"
    assert czk.get("previous_amount") == "80.00"
    assert czk.get("previous_receipt_count") == 1
    assert czk.get("discount_amount") == "7.50"
    assert totals(stats).get("EUR", {}).get("amount") == "2.50"

    everything = client.get(f"{URL}?period=all").json()
    czk = totals(everything).get("CZK", {})
    assert czk.get("amount") == "530.25"
    assert czk.get("receipt_count") == 4
    assert czk.get("previous_amount") is None


def test_monthly_series_and_month_filter(client: TestClient, engine: Engine) -> None:
    now = datetime.now(PRAGUE)
    current = f"{now.year:04d}-{now.month:02d}"
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_start = (month_start - timedelta(days=1)).replace(day=1)
    add_receipt(engine, "last-month", previous_start + timedelta(minutes=30), "40.00")
    login(client)

    stats = client.get(URL).json()
    months = stats.get("buckets", [])
    assert len(months) == 12
    assert months[-1] == {
        "key": current,
        "amount": "100.25",
        "receipt_count": 1,
        "merchants": [{"merchant": "Lidl", "amount": "100.25", "receipt_count": 1}],
    }
    assert sum(Decimal(month.get("amount")) for month in months) == Decimal("140.25")

    filtered = client.get(f"{URL}?bucket={current}").json()
    assert filtered.get("bucket") == current
    assert totals(filtered).get("CZK", {}).get("amount") == "100.25"
    assert totals(filtered).get("CZK", {}).get("previous_amount") == "40.00"
    assert filtered.get("buckets") == months


def test_prague_time_buckets(client: TestClient, engine: Engine) -> None:
    add_receipt(engine, "new-year", datetime(2025, 12, 31, 23, 30, tzinfo=UTC), "10.00")
    login(client)

    stats = client.get(f"{URL}?period=all&bucket=2026-01").json()
    assert totals(stats).get("CZK", {}).get("amount") == "10.00"
    january = [month for month in stats.get("buckets", []) if month.get("key") == "2026-01"]
    assert january == [
        {
            "key": "2026-01",
            "amount": "10.00",
            "receipt_count": 1,
            "merchants": [{"merchant": "Lidl", "amount": "10.00", "receipt_count": 1}],
        }
    ]
    weekly = stats.get("weekly_hours", [])
    assert len(weekly) == 7
    assert all(len(day) == 24 for day in weekly)
    assert weekly[3][0] == 1
    assert sum(sum(day) for day in weekly) == 1


def test_custom_range_daily_buckets_and_comparison(client: TestClient, engine: Engine) -> None:
    add_receipt(engine, "before", datetime(2026, 3, 2, 10, tzinfo=PRAGUE).astimezone(UTC), "5.00")
    add_receipt(
        engine, "first", datetime(2026, 3, 5, 0, 30, tzinfo=PRAGUE).astimezone(UTC), "20.00"
    )
    add_receipt(engine, "last", datetime(2026, 3, 7, 23, 30, tzinfo=PRAGUE).astimezone(UTC), "7.00")
    add_receipt(
        engine, "after", datetime(2026, 3, 8, 0, 30, tzinfo=PRAGUE).astimezone(UTC), "90.00"
    )
    login(client)

    custom = f"{URL}?period=custom&date_from=2026-03-05&date_to=2026-03-07"
    stats = client.get(custom).json()
    assert stats.get("granularity") == "day"
    buckets = stats.get("buckets", [])
    assert [bucket.get("key") for bucket in buckets] == ["2026-03-05", "2026-03-06", "2026-03-07"]
    assert [bucket.get("amount") for bucket in buckets] == ["20.00", "0.00", "7.00"]
    czk = totals(stats).get("CZK", {})
    assert czk.get("amount") == "27.00"
    assert czk.get("previous_amount") == "5.00"

    day = client.get(f"{custom}&bucket=2026-03-05").json()
    assert day.get("bucket") == "2026-03-05"
    assert totals(day).get("CZK", {}).get("amount") == "20.00"
    assert totals(day).get("CZK", {}).get("previous_amount") == "0.00"
    assert day.get("buckets") == buckets


def test_long_ranges_use_month_buckets(client: TestClient, engine: Engine) -> None:
    add_receipt(engine, "early", datetime(2026, 1, 10, 12, tzinfo=PRAGUE).astimezone(UTC), "3.00")
    add_receipt(engine, "inside", datetime(2026, 1, 20, 12, tzinfo=PRAGUE).astimezone(UTC), "4.00")
    login(client)

    stats = client.get(f"{URL}?period=custom&date_from=2026-01-15&date_to=2026-04-30").json()
    assert stats.get("granularity") == "month"
    keys = [bucket.get("key") for bucket in stats.get("buckets", [])]
    assert keys == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert totals(stats).get("CZK", {}).get("amount") == "4.00"

    today = client.get(f"{URL}?period=today").json()
    assert today.get("granularity") == "day"
    assert [bucket.get("key") for bucket in today.get("buckets", [])] == [
        datetime.now(PRAGUE).date().isoformat()
    ]


def test_custom_range_and_bucket_validation(client: TestClient) -> None:
    login(client)
    assert client.get(f"{URL}?period=custom").status_code == 422
    assert client.get(f"{URL}?period=custom&date_from=2026-03-02").status_code == 422
    range_url = f"{URL}?period=custom&date_from=2026-03-05&date_to=2026-03-01"
    assert client.get(range_url).status_code == 422
    assert client.get(f"{URL}?bucket=2026-02-30").status_code == 422


def test_merchants_and_top_items(client: TestClient, engine: Engine) -> None:
    now = datetime.now(UTC)
    add_receipt(engine, "a", now, "30.00", items=[("Rohlík", "5.00"), ("Sýr", "25.00")])
    add_receipt(engine, "b", now, "35.00", items=[("Rohlík", "5.00"), ("Víno", "30.00")])
    add_receipt(engine, "c", now, "60.00", merchant="Albert", items=[("Rohlík", "60.00")])
    add_receipt(engine, "d", now, "500.00", items=[("Kaviár", "500.00")], connection="Private Lidl")
    login(client)

    stats = client.get(URL).json()
    assert stats.get("merchants") == [
        {"merchant": "Lidl", "amount": "165.25", "receipt_count": 3},
        {"merchant": "Albert", "amount": "60.00", "receipt_count": 1},
    ]
    frequent = stats.get("frequent_items", [])
    assert frequent[0] == {"name": "Rohlík", "receipt_count": 3, "amount": "70.00"}
    spend = [item.get("name") for item in stats.get("top_spend_items", [])]
    assert spend == ["Rohlík", "Víno", "Sýr"]
    assert "Kaviár" not in {item.get("name") for item in frequent}


def categorize(engine: Engine, category: str, products: list[str], connection: str) -> None:
    with Session(engine) as db:
        owner_id = db.scalar(
            select(ProviderConnection.owner_id).where(ProviderConnection.name == connection)
        )
        assert owner_id is not None
        row = Category(owner_id=owner_id, name=category, name_key=category.casefold())
        db.add(row)
        for product in products:
            identity = CategoryProduct(
                connection_id=connection_id(engine, connection),
                name=product,
                name_hash=sha256(product.encode()).hexdigest(),
                currency="CZK",
                assignment_source="manual",
            )
            db.add(identity)
            db.flush()
            db.add(CategoryMembership(category_id=row.id, product_id=identity.id))
        db.commit()


def test_category_shares_by_purchases_and_spending(client: TestClient, engine: Engine) -> None:
    now = datetime.now(UTC)
    add_receipt(engine, "a", now, "30.00", items=[("Rohlík", "5.00"), ("Sýr", "25.00")])
    add_receipt(engine, "b", now, "35.00", items=[("Rohlík", "5.00"), ("Víno", "30.00")])
    add_receipt(engine, "old", now - timedelta(days=400), "9.00", items=[("Rohlík", "9.00")])
    add_receipt(engine, "d", now, "500.00", items=[("Kaviár", "500.00")], connection="Private Lidl")
    login(client)
    assert client.get(URL).json().get("categories") == []

    categorize(engine, "Pečivo", ["Rohlík"], "My Lidl")
    categorize(engine, "Nápoje", ["Víno"], "My Lidl")
    categorize(engine, "Luxus", ["Kaviár"], "Private Lidl")
    categories = client.get(URL).json().get("categories", [])
    by_name = {category.get("name"): category for category in categories}
    assert set(by_name) == {"Nápoje", "Pečivo", None}
    assert {name: row.get("purchase_count") for name, row in by_name.items()} == {
        "Nápoje": 1,
        "Pečivo": 2,
        None: 1,
    }
    assert by_name.get("Nápoje", {}).get("amount") == "30.00"
    assert by_name.get("Pečivo", {}).get("amount") == "10.00"
    assert by_name.get(None, {}).get("category_id") is None
    everything = client.get(f"{URL}?period=all").json().get("categories", [])
    pecivo = next(row for row in everything if row.get("name") == "Pečivo")
    assert pecivo.get("purchase_count") == 3


def test_stats_query_count_is_bounded(client: TestClient, engine: Engine) -> None:
    login(client)
    queries: list[str] = []

    def track(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    assert client.get(URL).status_code == 200
    small = len(queries)
    # User lookup plus fixed aggregates; receipts are never loaded row by row.
    assert small <= 9
    event.remove(engine, "before_cursor_execute", track)
    now = datetime.now(UTC)
    for number in range(20):
        add_receipt(engine, f"more-{number}", now, "1.00", items=[(f"Item {number}", "1.00")])
    queries.clear()
    event.listen(engine, "before_cursor_execute", track)
    assert client.get(URL).status_code == 200
    assert len(queries) == small
    event.remove(engine, "before_cursor_execute", track)
