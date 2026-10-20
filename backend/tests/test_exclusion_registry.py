from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login
from test_items import seed

from cartalytix.models import Discount, ProductExclusion, ProviderConnection, Receipt, ReceiptItem

URL = "/api/v1/items/exclusions"
ORIGIN = {"Origin": "http://testserver"}


def exclusions(engine: Engine) -> dict[str, str]:
    seed(engine)
    identifiers: dict[str, str] = {}
    with Session(engine) as db:
        rows = db.execute(
            select(ReceiptItem, Receipt, ProviderConnection)
            .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
            .join(ProviderConnection, Receipt.connection_id == ProviderConnection.id)
        ).all()
        for item, receipt, connection in rows:
            if item.name == "Rohlík":
                if receipt.external_id in {"items-0", "items-4"}:
                    db.add(
                        ProductExclusion(
                            owner_id=connection.owner_id, name=item.name, currency=receipt.currency
                        )
                    )
                if receipt.external_id in {"items-0", "items-1", "items-4"}:
                    item.is_excluded = True
            if receipt.external_id == "items-0":
                receipt.is_excluded = True
                if item.name == "Rohlík":
                    identifiers["first_item"] = str(item.id)
                    identifiers["first_receipt"] = str(receipt.id)
                    db.add(
                        Discount(
                            receipt_id=receipt.id, item_id=item.id, name="Coupon", amount=Decimal(1)
                        )
                    )
            if receipt.external_id == "items-1":
                identifiers["second_item"] = str(item.id)
            if item.name == "Káva":
                item.is_excluded = True
            identifiers[connection.name] = str(connection.id)
        db.commit()
    return identifiers


def test_registry_requires_session_and_validates(client: TestClient) -> None:
    assert client.get(URL).status_code == 401
    login(client)
    for query in ("scope=receipt", "sort=receipt_count_desc", "limit=101", "price_min=-1"):
        assert client.get(f"{URL}?{query}").status_code == 422
    assert client.get(URL).json() == {"items": [], "total": 0, "limit": 30, "offset": 0}


def test_registry_independent_flags_and_original_amounts(
    client: TestClient, engine: Engine
) -> None:
    identifiers = exclusions(engine)
    login(client)
    response = client.get(URL)
    assert response.status_code == 200
    page = response.json()
    assert page.get("total") == 4
    rows = page.get("items")
    assert [row.get("kind") for row in rows] == ["item", "product", "item", "item"]
    product = next(row for row in rows if row.get("kind") == "product")
    assert product.get("name") == "Rohlík"
    assert product.get("currency") == "CZK"
    assert product.get("total_amount") == "43.00"
    assert product.get("blocked_by") == ["receipt", "item"]
    assert product.get("receipt_id") is None and product.get("item_id") is None
    assert product.get("merchant") is None
    assert product.get("purchased_at").startswith("2026-03-03T10:00:00")
    first = next(row for row in rows if row.get("item_id") == identifiers.get("first_item"))
    assert first.get("receipt_id") == identifiers.get("first_receipt")
    assert first.get("merchant") == "Lidl Praha"
    assert first.get("total_amount") == "20.00"
    assert first.get("blocked_by") == ["receipt", "product"]
    assert all(row.get("name") != "Sýr" for row in rows)
    assert client.get(f"{URL}?scope=product").json().get("total") == 1
    assert client.get(f"{URL}?scope=item").json().get("total") == 3
    assert (
        client.get(URL, params={"connection_id": identifiers.get("Private Lidl")})
        .json()
        .get("total")
        == 0
    )
    assert (
        client.patch(
            "/api/v1/items/exclusion",
            json={"name": "Rohlík", "currency": "CZK", "excluded": False},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    rows = client.get(f"{URL}?scope=item").json().get("items")
    assert next(row for row in rows if row.get("item_id") == identifiers.get("first_item")).get(
        "blocked_by"
    ) == ["receipt"]
    assert (
        client.patch(
            f"/api/v1/receipts/{identifiers.get('first_receipt')}/items/"
            f"{identifiers.get('first_item')}/exclusion",
            json={"excluded": False},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    assert client.get(URL).json().get("total") == 2


def test_registry_filters_group_price_and_occurrence_price(
    client: TestClient, engine: Engine
) -> None:
    identifiers = exclusions(engine)
    login(client)
    query: dict[str, Any] = {"q": "ROHLIK", "price_min": "2.50", "price_max": "3.50"}
    rows = client.get(URL, params=query).json().get("items")
    assert [(row.get("kind"), row.get("total_amount")) for row in rows] == [
        ("product", "43.00"),
        ("item", "15.00"),
    ]
    query = {"purchased_from": "2026-03-02T00:00:00Z", "purchased_to": "2026-03-03T00:00:00Z"}
    rows = client.get(URL, params=query).json().get("items")
    assert len(rows) == 2
    assert {row.get("total_amount") for row in rows} == {"15.00"}
    assert next(row for row in rows if row.get("kind") == "product").get("blocked_by") == [
        "receipt",
        "item",
    ]
    assert (
        client.get(URL, params={"connection_id": identifiers.get("My Lidl")}).json().get("total")
        == 4
    )
    page = client.get(f"{URL}?sort=total_amount_desc&limit=1&offset=1").json()
    assert page.get("total") == 4 and page.get("limit") == 1 and page.get("offset") == 1
    assert [row.get("total_amount") for row in page.get("items")] == ["20.00"]
    assert client.get(f"{URL}?offset=20").json().get("total") == 4
    assert client.get(f"{URL}?offset=20").json().get("items") == []


def test_registry_finds_old_occurrences_and_queries_stay_constant(
    client: TestClient, engine: Engine
) -> None:
    with Session(engine) as db:
        connection_id = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "My Lidl")
        )
        assert connection_id is not None
        oldest_item = None
        for index in range(205):
            receipt = Receipt(
                connection_id=connection_id,
                external_id=f"history-{index}",
                purchased_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=index),
                merchant="Shop",
                currency="CZK",
                total_amount=Decimal(10),
            )
            db.add(receipt)
            db.flush()
            item = ReceiptItem(
                receipt_id=receipt.id,
                position=0,
                name="Old product",
                quantity=Decimal(1),
                total_amount=Decimal(10),
                is_excluded=index == 0 or index >= 200,
            )
            db.add(item)
            db.flush()
            if index == 0:
                oldest_item = str(item.id)
        db.commit()
    login(client)
    queries: list[str] = []

    def collect(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", collect)
    try:
        small = client.get(f"{URL}?limit=1").json()
        small_count = len(queries)
        queries.clear()
        large = client.get(f"{URL}?limit=100").json()
        assert len(queries) == small_count
        assert small_count <= 4
    finally:
        event.remove(engine, "before_cursor_execute", collect)
    assert small.get("total") == large.get("total") == 6
    assert any(row.get("item_id") == oldest_item for row in large.get("items"))
    page = client.get(f"{URL}?limit=1&offset=5").json()
    assert page.get("items")[0].get("item_id") == oldest_item


def test_registry_suggestions_match_explicit_scope(client: TestClient, engine: Engine) -> None:
    exclusions(engine)
    login(client)
    url = "/api/v1/receipts/suggestions"
    query = {"q": "syr", "kind": "item", "exclusion": "excluded"}
    assert client.get(url, params=query).json().get("items")[0].get("text") == "Sýr"
    assert client.get(url, params={**query, "scope": "all"}).json().get("items") == []
    query["q"] = "rohlik"
    for scope, count in (("all", 3), ("product", 3), ("item", 2)):
        rows = client.get(url, params={**query, "scope": scope}).json().get("items")
        assert [(row.get("text"), row.get("receipt_count")) for row in rows] == [("Rohlík", count)]


def test_global_blockers_survive_date_and_connection_filters(
    client: TestClient, engine: Engine
) -> None:
    identifiers = exclusions(engine)
    with Session(engine) as db:
        connection = db.scalar(
            select(ProviderConnection).where(ProviderConnection.name == "My Lidl")
        )
        receipt = db.scalar(select(Receipt).where(Receipt.external_id == "items-0"))
        assert connection is not None and receipt is not None
        other = ProviderConnection(
            owner_id=connection.owner_id, provider="lidl", name="Other own shop"
        )
        db.add(other)
        db.flush()
        receipt.connection_id = other.id
        db.commit()
    login(client)
    response = client.get(
        URL,
        params={
            "scope": "product",
            "q": "rohlik",
            "connection_id": identifiers.get("My Lidl"),
            "purchased_from": "2026-03-03T00:00:00Z",
        },
    )
    assert response.status_code == 200
    rows = response.json().get("items")
    assert len(rows) == 1
    assert rows[0].get("total_amount") == "8.00"
    assert rows[0].get("blocked_by") == ["receipt", "item"]
