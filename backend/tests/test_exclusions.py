from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_admin import ADMIN_PASSWORD, add_admin, sign_in
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import (
    Discount,
    ProductExclusion,
    ProviderConnection,
    Receipt,
    ReceiptItem,
    User,
)

ORIGIN = {"Origin": "http://testserver"}


def basket(engine: Engine) -> tuple[str, str, str, str]:
    with Session(engine) as db:
        receipt = db.scalar(
            select(Receipt)
            .join(ProviderConnection)
            .where(ProviderConnection.name == "My Lidl", Receipt.currency == "CZK")
        )
        foreign = db.scalar(
            select(Receipt.id)
            .join(ProviderConnection)
            .where(ProviderConnection.name == "Private Lidl")
        )
        assert receipt is not None and foreign is not None
        receipt.total_amount = Decimal("81.00")
        food = ReceiptItem(
            receipt_id=receipt.id,
            position=1,
            name="Bread",
            quantity=Decimal(1),
            unit_price=Decimal(60),
            total_amount=Decimal(60),
        )
        tool = ReceiptItem(
            receipt_id=receipt.id,
            position=2,
            name="Spatula",
            quantity=Decimal(1),
            unit_price=Decimal(40),
            total_amount=Decimal(40),
        )
        db.add_all([food, tool])
        db.flush()
        db.add_all(
            [
                Discount(receipt_id=receipt.id, item_id=food.id, name="Coupon", amount=Decimal(10)),
                Discount(receipt_id=receipt.id, name="Basket", amount=Decimal(9)),
            ]
        )
        db.commit()
        return str(receipt.id), str(food.id), str(tool.id), str(foreign)


def test_occurrence_exclusion_allocates_discount_and_is_reversible(
    client: TestClient, engine: Engine
) -> None:
    receipt_id, food_id, tool_id, _ = basket(engine)
    login(client)
    url = f"/api/v1/receipts/{receipt_id}"
    result = client.patch(
        f"{url}/items/{tool_id}/exclusion", json={"excluded": True}, headers=ORIGIN
    )
    assert result.status_code == 200
    detail = client.get(url).json()
    assert detail.get("total_amount") == "81.00"
    assert detail.get("included_amount") == "45.00"
    food, tool = detail.get("items")
    assert food.get("id") == food_id and food.get("included_amount") == "45.00"
    assert tool.get("is_excluded") is True and tool.get("exclusion_reason") == "item"
    assert tool.get("included_amount") == "0.00"
    assert client.get("/api/v1/items").json().get("total") == 1
    assert client.get("/api/v1/items?exclusion=excluded").json().get("total") == 1
    overview = client.get("/api/v1/overview").json()
    assert {row.get("currency"): row.get("amount") for row in overview.get("totals")} == {
        "CZK": "45.00",
        "EUR": "2.50",
    }
    assert (
        client.patch(
            f"{url}/items/{tool_id}/exclusion", json={"excluded": False}, headers=ORIGIN
        ).status_code
        == 200
    )
    assert client.get(url).json().get("included_amount") == "81.00"


def test_global_and_receipt_exclusions_are_independent_owner_scoped(
    client: TestClient, engine: Engine
) -> None:
    receipt_id, _, tool_id, foreign_id = basket(engine)
    login(client)
    url = f"/api/v1/receipts/{receipt_id}"
    product = {"name": "Spatula", "currency": "CZK", "excluded": True}
    assert client.patch("/api/v1/items/exclusion", json=product, headers=ORIGIN).status_code == 200
    detail = client.get(url).json()
    assert detail.get("items")[1].get("exclusion_reason") == "product"
    assert client.get("/api/v1/items/detail", params=product).status_code == 200
    assert (
        client.patch(f"{url}/exclusion", json={"excluded": True}, headers=ORIGIN).status_code == 200
    )
    assert client.get(url).json().get("included_amount") == "0.00"
    assert client.get("/api/v1/receipts?exclusion=excluded").json().get("total") == 1
    assert (
        client.patch(f"{url}/exclusion", json={"excluded": False}, headers=ORIGIN).status_code
        == 200
    )
    assert client.get(url).json().get("included_amount") == "45.00"
    assert (
        client.patch(
            f"/api/v1/receipts/{foreign_id}/exclusion", json={"excluded": True}, headers=ORIGIN
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/receipts/{foreign_id}/items/{tool_id}/exclusion",
            json={"excluded": True},
            headers=ORIGIN,
        ).status_code
        == 404
    )


def test_global_exclusion_applies_to_future_receipts_exact_name_and_currency(
    client: TestClient, engine: Engine
) -> None:
    basket(engine)
    login(client)
    body = {"name": "Spatula", "currency": "CZK", "excluded": True}
    assert client.patch("/api/v1/items/exclusion", json=body, headers=ORIGIN).status_code == 200
    assert client.patch("/api/v1/items/exclusion", json=body, headers=ORIGIN).status_code == 200
    with Session(engine) as db:
        connection = db.scalar(
            select(ProviderConnection).where(ProviderConnection.name == "My Lidl")
        )
        assert connection is not None
        receipt = Receipt(
            connection_id=connection.id,
            external_id="future",
            merchant="Other shop",
            purchased_at=datetime.now(UTC),
            currency="CZK",
            total_amount=Decimal(15),
        )
        db.add(receipt)
        db.flush()
        db.add_all(
            [
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=1,
                    name="Spatula",
                    quantity=Decimal(1),
                    total_amount=Decimal(10),
                ),
                ReceiptItem(
                    receipt_id=receipt.id,
                    position=2,
                    name="spatula",
                    quantity=Decimal(1),
                    total_amount=Decimal(5),
                ),
            ]
        )
        db.commit()
        receipt_id = receipt.id
    detail = client.get(f"/api/v1/receipts/{receipt_id}").json()
    assert detail.get("included_amount") == "5.00"
    assert [item.get("exclusion_reason") for item in detail.get("items")] == ["product", None]
    stats = client.get("/api/v1/overview/stats?period=all").json()
    czk = next(row for row in stats.get("totals") if row.get("currency") == "CZK")
    assert czk.get("amount") == "50.00" and czk.get("receipt_count") == 2
    body["excluded"] = False
    assert client.patch("/api/v1/items/exclusion", json=body, headers=ORIGIN).status_code == 200
    assert client.get(f"/api/v1/receipts/{receipt_id}").json().get("included_amount") == "15.00"


def test_owner_deletion_cascades_product_exclusions(client: TestClient, engine: Engine) -> None:
    if engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    basket(engine)
    login(client)
    assert (
        client.patch(
            "/api/v1/items/exclusion",
            json={"name": "Spatula", "currency": "CZK", "excluded": True},
            headers=ORIGIN,
        ).status_code
        == 200
    )
    with Session(engine) as db:
        owner_id = db.scalar(select(User.id).where(User.email == "owner@example.com"))
        assert db.scalar(select(ProductExclusion.id).where(ProductExclusion.owner_id == owner_id))
    add_admin(engine)
    sign_in(client, "root@example.com", ADMIN_PASSWORD)
    assert client.delete(f"/api/v1/admin/users/{owner_id}", headers=ORIGIN).status_code == 204
    with Session(engine) as db:
        assert (
            db.scalar(select(ProductExclusion.id).where(ProductExclusion.owner_id == owner_id))
            is None
        )
