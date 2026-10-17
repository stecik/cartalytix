from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import ProviderConnection, Receipt, ReceiptItem

URL = "/api/v1/receipts/suggestions"


def seed(engine: Engine) -> None:
    with Session(engine) as db:
        connections = {
            connection.name: connection.id for connection in db.scalars(select(ProviderConnection))
        }
        baskets = [
            ("My Lidl", "Lidl Praha", ["Rohlík tukový", "Kornbageta"]),
            ("My Lidl", "Lidl Praha", ["Rohlík tukový", "Bageta francouzská"]),
            ("My Lidl", "Albert 100%", ["Rohlík tukový", "Rohlíky celozrnné"]),
            ("Private Lidl", "Lidl Brno", ["Rohlík tajný", "Rohlík tajný"]),
        ]
        for number, (connection, merchant, names) in enumerate(baskets):
            receipt = Receipt(
                connection_id=connections.get(connection),
                external_id=f"suggest-{number}",
                purchased_at=datetime(2026, 1, number + 1, tzinfo=UTC),
                merchant=merchant,
                currency="CZK",
                total_amount=Decimal(10),
            )
            db.add(receipt)
            db.flush()
            db.add_all(
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=position,
                    name=name,
                    quantity=Decimal(1),
                    total_amount=Decimal(5),
                )
                for position, name in enumerate(names)
            )
        db.commit()


def suggestions(client: TestClient, query: str) -> list[dict[str, Any]]:
    response = client.get(URL, params={"q": query})
    assert response.status_code == 200
    return response.json().get("items")


def test_suggestions_require_session_and_three_characters(client: TestClient) -> None:
    assert client.get(URL, params={"q": "rohlik"}).status_code == 401
    login(client)
    assert client.get(URL, params={"q": "ro"}).status_code == 422
    assert client.get(URL, params={"q": "x" * 101}).status_code == 422
    assert suggestions(client, "  ro  ") == []


def test_suggestions_ignore_diacritics_and_stay_owner_scoped(
    client: TestClient, engine: Engine
) -> None:
    seed(engine)
    login(client)
    assert suggestions(client, "ROHLIK") == [
        {"kind": "item", "text": "Rohlík tukový", "receipt_count": 3},
        {"kind": "item", "text": "Rohlíky celozrnné", "receipt_count": 1},
    ]
    assert suggestions(client, "brno") == []


def test_suggestions_rank_prefix_then_word_then_substring(
    client: TestClient, engine: Engine
) -> None:
    seed(engine)
    login(client)
    assert [item.get("text") for item in suggestions(client, "bage")] == [
        "Bageta francouzská",
        "Kornbageta",
    ]
    assert suggestions(client, "praha") == [
        {"kind": "merchant", "text": "Lidl Praha", "receipt_count": 2}
    ]
    assert suggestions(client, "100%") == [
        {"kind": "merchant", "text": "Albert 100%", "receipt_count": 1}
    ]


def test_receipt_filter_ignores_diacritics(client: TestClient, engine: Engine) -> None:
    seed(engine)
    login(client)
    response = client.get("/api/v1/receipts", params={"q": "rohliky"})
    assert response.json().get("total") == 1
    assert client.get("/api/v1/receipts", params={"q": "bageta"}).json().get("total") == 2


def test_fuzzy_suggestions_tolerate_typos(client: TestClient, engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("Typo tolerance relies on PostgreSQL pg_trgm")
    seed(engine)
    login(client)
    assert [item.get("text") for item in suggestions(client, "kornbagta")] == ["Kornbageta"]


def test_suggestion_queries_are_bounded(client: TestClient, engine: Engine) -> None:
    seed(engine)
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
    assert suggestions(client, "rohlik")
    event.remove(engine, "before_cursor_execute", track)
    assert len(queries) <= 4
