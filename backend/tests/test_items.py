from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import ProviderConnection, Receipt, ReceiptItem

URL = "/api/v1/items"


def seed(engine: Engine) -> dict[str, str]:
    """Rohlík: 3 purchases (2, 3, 4 CZK), Sýr: 1 purchase, Káva in EUR; foreign Rohlík hidden."""
    with Session(engine) as db:
        connections = {
            connection.name: connection.id for connection in db.scalars(select(ProviderConnection))
        }
        baskets = [
            (
                "My Lidl",
                "Lidl Praha",
                1,
                "CZK",
                [("Rohlík", "10", "2.00", None), ("Sýr", "1", "89.90", None)],
            ),
            ("My Lidl", "Lidl Praha", 2, "CZK", [("Rohlík", "5", "3.00", None)]),
            ("My Lidl", "Billa", 3, "CZK", [("Rohlík", "2", "4.00", None)]),
            ("My Lidl", "Lidl Wien", 4, "EUR", [("Káva", "0.5", "12.00", "kg")]),
            ("Private Lidl", "Lidl Brno", 5, "CZK", [("Rohlík", "100", "1.00", None)]),
        ]
        receipt_ids: dict[str, str] = {}
        for number, (connection, merchant, day, currency, items) in enumerate(baskets):
            receipt = Receipt(
                connection_id=connections.get(connection),
                external_id=f"items-{number}",
                purchased_at=datetime(2026, 3, day, 10, tzinfo=UTC),
                merchant=merchant,
                currency=currency,
                total_amount=Decimal(100),
            )
            db.add(receipt)
            db.flush()
            receipt_ids[f"{merchant}-{day}"] = str(receipt.id)
            db.add_all(
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=position,
                    name=name,
                    quantity=Decimal(quantity),
                    unit=unit,
                    unit_price=Decimal(price),
                    total_amount=Decimal(quantity) * Decimal(price),
                    is_weight=unit == "kg",
                )
                for position, (name, quantity, price, unit) in enumerate(items)
            )
        db.commit()
    return receipt_ids


def names(client: TestClient, query: str = "") -> list[str]:
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 200
    return [item.get("name") for item in response.json().get("items")]


def test_items_require_session_and_validate(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    login(client)
    assert client.get(f"{URL}?sort=price").status_code == 422
    assert client.get(f"{URL}?limit=101").status_code == 422
    assert client.get(f"{URL}?price_min=-1").status_code == 422


def test_items_group_purchases_per_product(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    page = client.get(URL).json()
    assert page.get("total") == 3
    rohlik = next(item for item in page.get("items") if item.get("name") == "Rohlík")
    assert rohlik == {
        "name": "Rohlík",
        "currency": "CZK",
        "unit": None,
        "receipt_count": 3,
        "quantity": "17",
        "total_amount": "43.00",
        "average_price": "3.00",
        "min_price": "2.00",
        "max_price": "4.00",
        "last_purchased_at": rohlik.get("last_purchased_at"),
    }
    assert rohlik.get("last_purchased_at", "").startswith("2026-03-03T10:00:00")


def test_items_filter_and_sort(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    assert names(client) == ["Káva", "Rohlík", "Sýr"]
    assert names(client, "sort=total_amount_desc") == ["Sýr", "Rohlík", "Káva"]
    assert names(client, "sort=receipt_count_desc") == ["Rohlík", "Káva", "Sýr"]
    assert names(client, "sort=average_price_asc") == ["Rohlík", "Káva", "Sýr"]
    assert names(client, "sort=last_purchased_desc") == ["Káva", "Rohlík", "Sýr"]
    assert names(client, "q=ROHLIK") == ["Rohlík"]
    assert names(client, "q=billa") == []
    assert names(client, "price_min=10&price_max=50") == ["Káva"]
    assert names(
        client, "purchased_from=2026-03-02T00:00:00Z&purchased_to=2026-03-03T00:00:00Z"
    ) == ["Rohlík"]
    single = client.get(f"{URL}?purchased_from=2026-03-02T00:00:00Z").json().get("items")
    assert next(item for item in single if item.get("name") == "Rohlík").get("receipt_count") == 2
    with Session(engine) as db:
        foreign = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    assert names(client, f"connection_id={foreign}") == []
    page = client.get(f"{URL}?limit=1&offset=1").json()
    assert page.get("total") == 3
    assert [item.get("name") for item in page.get("items")] == ["Rohlík"]


def test_item_detail_lists_purchase_history(client: TestClient, engine: Engine) -> None:
    receipt_ids = seed(engine)
    login(client)
    response = client.get(f"{URL}/detail", params={"name": "Rohlík", "currency": "CZK"})
    assert response.status_code == 200
    detail: dict[str, Any] = response.json()
    assert detail.get("receipt_count") == 3
    purchases = detail.get("purchases", [])
    assert [purchase.get("merchant") for purchase in purchases] == [
        "Billa",
        "Lidl Praha",
        "Lidl Praha",
    ]
    assert purchases[0].get("receipt_id") == receipt_ids.get("Billa-3")
    assert purchases[0].get("unit_price") == "4.00"
    assert (
        client.get(f"{URL}/detail", params={"name": "Kaviár", "currency": "CZK"}).status_code == 404
    )
    assert client.get(f"{URL}/detail", params={"name": "Rohlík"}).status_code == 422


def test_item_queries_are_bounded(client: TestClient, engine: Engine) -> None:
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
    event.remove(engine, "before_cursor_execute", track)
    seed(engine)
    queries.clear()
    event.listen(engine, "before_cursor_execute", track)
    assert client.get(URL).status_code == 200
    assert len(queries) == small
    queries.clear()
    assert (
        client.get(f"{URL}/detail", params={"name": "Rohlík", "currency": "CZK"}).status_code == 200
    )
    assert len(queries) <= 3
    event.remove(engine, "before_cursor_execute", track)
