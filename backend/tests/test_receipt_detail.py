from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem


def test_receipt_detail_is_owner_scoped_and_keeps_item_prices(
    client: TestClient, engine: Engine
) -> None:
    with Session(engine) as db:
        own = db.scalar(
            select(Receipt).join(ProviderConnection).where(ProviderConnection.name == "My Lidl")
        )
        foreign = db.scalar(
            select(Receipt)
            .join(ProviderConnection)
            .where(ProviderConnection.name == "Private Lidl")
        )
        assert own is not None and foreign is not None
        own_id, foreign_id = own.id, foreign.id
        item = ReceiptItem(
            receipt_id=own.id,
            position=0,
            name="Milk",
            quantity=Decimal(2),
            unit="pcs",
            unit_price=Decimal(5),
            total_amount=Decimal(10),
            original_amount=Decimal(10),
            printed_net_amount=Decimal(8),
            source_code="ARTICLE-001",
        )
        db.add(item)
        db.flush()
        db.add(Discount(receipt_id=own.id, item_id=item.id, name="Coupon", amount=Decimal(2)))
        db.commit()
    login(client)
    assert client.get(f"/api/v1/receipts/{foreign_id}").status_code == 404
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
        response = client.get(f"/api/v1/receipts/{own_id}")
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-store"
        result = response.json()
        assert result.get("items")[0].get("original_amount") == "10.00"
        assert result.get("items")[0].get("quantity") == "2.000000"
        assert result.get("discounts")[0].get("amount") == "2.00"
        assert len(queries) == 4
    finally:
        event.remove(engine, "before_cursor_execute", track)
