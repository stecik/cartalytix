"""Mixit linking routes and sync; synthetic credentials and provider data only."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.mixit.client import MixitAccount, MixitError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/mixit/connections"
CREDENTIALS = {"email": "owner@example.com", "password": "synthetic-password"}


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Mixit"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert response.json().get("provider") == "mixit"
    return str(response.json().get("id"))


def accepted(subject: str = "42") -> Callable[[str, str], MixitAccount]:
    def sign_in(email: str, password: str) -> MixitAccount:
        assert (email, password) == ("owner@example.com", "synthetic-password")
        return MixitAccount(token="private-mixit-token", subject=subject)

    return sign_in


def test_login_encrypts_token_without_exposing_it(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.mixit_routes.sign_in", accepted())
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/login", json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "private-mixit-token" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.account_subject == "42"
        assert connection.encrypted_refresh_token is not None
        settings = application(authorized).state.settings
        assert TokenCipher(settings).decrypt(connection.encrypted_refresh_token) == (
            "private-mixit-token"
        )
    providers = authorized.get("/api/v1/providers").json().get("items")
    assert next(p for p in providers if p.get("code") == "mixit").get("status") == "available"


def test_login_rejects_other_account_and_bad_credentials(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.mixit_routes.sign_in", accepted())
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    assert authorized.post(url, json=CREDENTIALS, headers=HEADERS).status_code == 200
    monkeypatch.setattr("cartalytix.mixit_routes.sign_in", accepted("other"))
    response = authorized.post(url, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "account_mismatch"

    def rejected(email: str, password: str) -> MixitAccount:
        raise MixitError("credentials_rejected")

    monkeypatch.setattr("cartalytix.mixit_routes.sign_in", rejected)
    response = authorized.post(url, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "credentials_rejected"


def test_lidl_routes_do_not_expose_mixit_connections(authorized: TestClient) -> None:
    identifier = create(authorized)
    response = authorized.patch(
        f"/api/v1/providers/lidl/connections/{identifier}", json={"name": "x"}, headers=HEADERS
    )
    assert response.status_code == 404
    assert (
        authorized.post(
            PREFIX.replace("mixit", "unknown"), json={"name": "x"}, headers=HEADERS
        ).status_code
        == 422
    )


def mixit_order(identifier: str, state: str = "shipped", total: str = "120.00") -> dict[str, Any]:
    return {
        "order": {
            "hash": identifier,
            "state": state,
            "createdAt": "02. 01. 2026",
            "currency": "Kč",
            "totalPrice": total,
            "orderProducts": [
                {"productID": 1, "name": "Oats", "quantity": 2, "price": "50", "totalPrice": "100"}
            ],
            "orderMixes": [],
        }
    }


class Orders:
    def __init__(self, details: dict[str, dict[str, Any]]) -> None:
        self.details = details

    def factory(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> Orders:
        assert refresh_token == "old"
        return self

    def page(self, number: int) -> dict[str, Any]:
        hashes = list(self.details)
        orders = [{"hash": value} for value in hashes] if number == 1 else []
        return {"orders": orders, "totalRecords": len(hashes)}

    def detail(self, identifier: str) -> dict[str, Any]:
        return self.details[identifier]

    def close(self) -> None:
        pass


def test_sync_imports_final_orders_with_adjustments(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "mixit"
    orders = Orders(
        {
            "shipped": mixit_order("shipped", total="179.00"),
            "credited": mixit_order("credited", state="completed", total="80.00"),
            "pending": mixit_order("pending", state="new"),
        }
    )
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=orders.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert (stored.receipts_imported, stored.receipts_skipped) == (2, 1)
        receipts = {r.external_id: r for r in db.scalars(select(Receipt))}
        assert set(receipts) == {"shipped", "credited"}
        assert receipts["shipped"].merchant == "Mixit"
        assert receipts["shipped"].total_amount == Decimal("179.00")
        shipping = db.scalars(
            select(ReceiptItem.total_amount).where(ReceiptItem.receipt_id == receipts["shipped"].id)
        ).all()
        assert sorted(shipping) == [Decimal("79.00"), Decimal("100")]
        discount = db.scalar(select(Discount).where(Discount.receipt_id == receipts["credited"].id))
        assert discount is not None and discount.item_id is None
        assert discount.amount == Decimal("20.00")
