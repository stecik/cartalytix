from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import ProviderConnection, Receipt, ReceiptItem

ORIGIN = {"Origin": "http://testserver"}
COFFEE = {
    "merchant": "Globus",
    "provider": "globus",
    "purchased_at": "2026-10-29T10:15:00",
    "items": [
        {"name": "Káva zrnková", "quantity": "5", "total_amount": "1249.50"},
        {"name": "Taška", "total_amount": "5.00"},
    ],
}


def test_manual_receipts_join_their_shop_and_hide_their_connection(
    client: TestClient, engine: Engine
) -> None:
    login(client)
    created = client.post(
        "/api/v1/receipts/manual",
        json={"receipts": [COFFEE, {**COFFEE, "provider": None, "merchant": "Pekárna"}]},
        headers=ORIGIN,
    )
    assert created.status_code == 201, created.text
    receipts = created.json().get("items", [])
    globus = next(row for row in receipts if row.get("merchant") == "Globus")
    assert globus.get("provider") == "globus"
    assert globus.get("is_manual") is True
    assert globus.get("total_amount") == "1254.50"
    assert globus.get("included_amount") == "1254.50"
    bakery = next(row for row in receipts if row.get("merchant") == "Pekárna")
    assert bakery.get("provider") == "manual"
    detail = client.get(f"/api/v1/receipts/{globus.get('id')}").json()
    assert detail.get("purchased_at", "").startswith("2026-10-29T")
    assert [item.get("unit_price") for item in detail.get("items", [])] == [
        "249.900000",
        "5.000000",
    ]
    assert all(
        connection.get("provider") != "manual"
        for connection in client.get("/api/v1/connections").json().get("items", [])
    )
    assert client.get("/api/v1/overview").json().get("connection_count") == 1
    shops = client.get("/api/v1/shops").json().get("items", [])
    assert {(shop.get("merchant"), shop.get("provider")) for shop in shops} == {
        ("Lidl", "lidl"),
        ("Globus", "globus"),
        ("Pekárna", None),
    }
    client.post("/api/v1/receipts/manual", json={"receipts": [COFFEE]}, headers=ORIGIN)
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProviderConnection)
                .where(ProviderConnection.provider == "manual")
            )
            == 1
        )


def test_only_own_manual_receipts_can_be_deleted(client: TestClient, engine: Engine) -> None:
    login(client)
    created = client.post("/api/v1/receipts/manual", json={"receipts": [COFFEE]}, headers=ORIGIN)
    manual_id = created.json().get("items", [])[0].get("id")
    with Session(engine) as db:
        synced = db.scalar(
            select(Receipt.id).join(ProviderConnection).where(ProviderConnection.name == "My Lidl")
        )
        foreign = db.scalar(
            select(Receipt.id)
            .join(ProviderConnection)
            .where(ProviderConnection.name == "Private Lidl")
        )
    assert client.delete(f"/api/v1/receipts/{synced}", headers=ORIGIN).status_code == 409
    assert client.delete(f"/api/v1/receipts/{foreign}", headers=ORIGIN).status_code == 404
    assert client.delete(f"/api/v1/receipts/{manual_id}", headers=ORIGIN).status_code == 204
    assert client.get(f"/api/v1/receipts/{manual_id}").status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(ReceiptItem)) == 0


def test_manual_receipt_validation(client: TestClient) -> None:
    login(client)
    for receipt in [
        {**COFFEE, "items": []},
        {**COFFEE, "merchant": "  "},
        {**COFFEE, "provider": "unknown"},
        {**COFFEE, "items": [{"name": "Káva", "quantity": "0", "total_amount": "1"}]},
    ]:
        response = client.post(
            "/api/v1/receipts/manual", json={"receipts": [receipt]}, headers=ORIGIN
        )
        assert response.status_code == 422, receipt
