from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem

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
    assert client.get(f"{URL}?month=2026-13").status_code == 422
    assert client.get(f"{URL}?month=26-01").status_code == 422


def test_period_totals_trend_and_discounts(client: TestClient, engine: Engine) -> None:
    now = datetime.now(UTC)
    add_receipt(engine, "recent", now - timedelta(days=1), "50.00", discount="-7.50")
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
    months = stats.get("months", [])
    assert len(months) == 12
    assert months[-1] == {
        "month": current,
        "amount": "100.25",
        "receipt_count": 1,
        "merchants": [{"merchant": "Lidl", "amount": "100.25", "receipt_count": 1}],
    }
    assert sum(Decimal(month.get("amount")) for month in months) == Decimal("140.25")

    filtered = client.get(f"{URL}?month={current}").json()
    assert filtered.get("month") == current
    assert totals(filtered).get("CZK", {}).get("amount") == "100.25"
    assert totals(filtered).get("CZK", {}).get("previous_amount") == "40.00"
    assert filtered.get("months") == months


def test_prague_time_buckets(client: TestClient, engine: Engine) -> None:
    add_receipt(engine, "new-year", datetime(2025, 12, 31, 23, 30, tzinfo=UTC), "10.00")
    login(client)

    stats = client.get(f"{URL}?period=all&month=2026-01").json()
    assert totals(stats).get("CZK", {}).get("amount") == "10.00"
    january = [month for month in stats.get("months", []) if month.get("month") == "2026-01"]
    assert january == [
        {
            "month": "2026-01",
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
    assert small <= 6
    event.remove(engine, "before_cursor_execute", track)
    now = datetime.now(UTC)
    for number in range(20):
        add_receipt(engine, f"more-{number}", now, "1.00", items=[(f"Item {number}", "1.00")])
    queries.clear()
    event.listen(engine, "before_cursor_execute", track)
    assert client.get(URL).status_code == 200
    assert len(queries) == small
    event.remove(engine, "before_cursor_execute", track)
