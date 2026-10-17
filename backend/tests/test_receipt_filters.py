from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import ProviderConnection, Receipt, ReceiptItem


def seed(engine: Engine) -> None:
    with Session(engine) as db:
        own = db.scalar(select(ProviderConnection).where(ProviderConnection.name == "My Lidl"))
        assert own is not None
        db.execute(delete(Receipt).where(Receipt.connection_id == own.id))
        receipts = [
            Receipt(
                connection_id=own.id,
                external_id=external_id,
                purchased_at=datetime(2026, month, day, 12, tzinfo=UTC),
                merchant=merchant,
                currency="CZK",
                total_amount=Decimal(amount),
            )
            for external_id, month, day, merchant, amount in [
                ("a", 1, 10, "Lidl Praha", "120.00"),
                ("b", 2, 15, "Albert 100%", "45.50"),
                ("c", 3, 20, "Billa", "300.00"),
            ]
        ]
        db.add_all(receipts)
        db.flush()
        db.add(
            ReceiptItem(
                receipt_id=receipts[2].id,
                position=0,
                name="Mléko polotučné",
                quantity=Decimal(1),
                unit_price=Decimal(30),
                total_amount=Decimal(30),
            )
        )
        db.commit()


def merchants(client: TestClient, query: str) -> list[str]:
    response = client.get(f"/api/v1/receipts?{query}")
    assert response.status_code == 200
    return [item.get("merchant") for item in response.json().get("items")]


def test_receipts_sort_by_date_and_amount(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    assert merchants(client, "") == ["Billa", "Albert 100%", "Lidl Praha"]
    assert merchants(client, "sort=purchased_at_asc") == ["Lidl Praha", "Albert 100%", "Billa"]
    assert merchants(client, "sort=total_amount_desc") == ["Billa", "Lidl Praha", "Albert 100%"]
    assert merchants(client, "sort=total_amount_asc") == ["Albert 100%", "Lidl Praha", "Billa"]
    assert client.get("/api/v1/receipts?sort=merchant").status_code == 422


def test_receipts_search_matches_merchant_or_item_name(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    assert merchants(client, "q=PRAHA") == ["Lidl Praha"]
    assert merchants(client, "q=mléko") == ["Billa"]
    assert merchants(client, "q=100%25") == ["Albert 100%"]
    assert merchants(client, "q=%25") == ["Albert 100%"]
    assert merchants(client, "q=_") == []
    assert client.get(f"/api/v1/receipts?q={'x' * 101}").status_code == 422


def test_receipts_filter_by_date_and_amount_range(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    response = client.get(
        "/api/v1/receipts",
        params={
            "purchased_from": "2026-02-01T00:00:00+01:00",
            "purchased_to": "2026-03-21T00:00:00+01:00",
        },
    )
    result = response.json()
    assert [item.get("merchant") for item in result.get("items")] == ["Billa", "Albert 100%"]
    assert result.get("total") == 2
    assert merchants(client, "amount_min=100&amount_max=200") == ["Lidl Praha"]
    assert merchants(client, "amount_min=45.50&amount_max=45.50") == ["Albert 100%"]


def test_receipts_filters_use_two_queries(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    queries: list[str] = []

    def track(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    try:
        assert merchants(client, "q=a&amount_min=1&sort=total_amount_asc")
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert len([query for query in queries if "receipts" in query]) == 2
