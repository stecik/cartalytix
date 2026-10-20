"""foodora token linking and sync; synthetic tokens and orders only."""

import json
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
from test_foodora_adapter import order
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.foodora.client import FoodoraAccount, FoodoraError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/foodora/connections"
TOKENS = {"access_token": " browser-token "}


def accepted(subject: str = "42") -> Callable[[str], FoodoraAccount]:
    def link(access_token: str) -> FoodoraAccount:
        assert access_token == "browser-token"
        return FoodoraAccount(json.dumps({"access_token": access_token}), subject)

    return link


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def test_authorize_stores_only_encrypted_token(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.foodora_routes.link", accepted())
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/authorize", json=TOKENS, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "browser-token" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.account_subject == "42"
        assert connection.encrypted_refresh_token is not None
        stored = TokenCipher(application(authorized).state.settings).decrypt(
            connection.encrypted_refresh_token
        )
        assert json.loads(stored) == {"access_token": "browser-token"}
        assert b"browser-token" not in connection.encrypted_refresh_token
    providers = authorized.get("/api/v1/providers").json().get("items")
    assert next(p for p in providers if p.get("code") == "foodora").get("status") == "available"


def test_authorize_rejects_other_account_and_expired_tokens(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.foodora_routes.link", accepted())
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/authorize"
    assert authorized.post(url, json=TOKENS, headers=HEADERS).status_code == 200
    monkeypatch.setattr("cartalytix.foodora_routes.link", accepted("other"))
    response = authorized.post(url, json=TOKENS, headers=HEADERS)
    assert (response.status_code, response.json().get("error", {}).get("code")) == (
        409,
        "account_mismatch",
    )

    def expired(access_token: str) -> FoodoraAccount:
        raise FoodoraError("auth_required")

    monkeypatch.setattr("cartalytix.foodora_routes.link", expired)
    response = authorized.post(url, json=TOKENS, headers=HEADERS)
    assert (response.status_code, response.json().get("error", {}).get("code")) == (
        422,
        "auth_required",
    )
    assert authorized.post(url, json={"refresh_token": "x"}, headers=HEADERS).status_code == 422


class Orders:
    def __init__(self, orders: list[dict[str, Any]]) -> None:
        self.orders = {item["order_code"]: item for item in orders}

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
        return {"orders": list(self.orders.values()) if number == 1 else []}

    def detail(self, identifier: str) -> dict[str, Any]:
        return self.orders[identifier]

    def close(self) -> None:
        pass


def test_sync_imports_delivered_orders_with_fees_and_discounts(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "foodora"
    orders = Orders([order("done"), order("legacy", total=135), order("active", delivered=False)])
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=orders.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert (stored.receipts_imported, stored.receipts_skipped) == (2, 1)
        receipts = {r.external_id: r for r in db.scalars(select(Receipt))}
        assert set(receipts) == {"done", "legacy"}
        assert receipts["done"].merchant == "Foodora"
        assert receipts["done"].total_amount == Decimal("235")
        names = db.scalars(
            select(ReceiptItem.name).where(ReceiptItem.receipt_id == receipts["done"].id)
        ).all()
        assert sorted(names) == ["Burger", "Polévka", "container_charges", "service_fee"]
        discounts = db.scalars(
            select(Discount.name).where(Discount.receipt_id == receipts["legacy"].id)
        ).all()
        assert sorted(discounts) == ["discount", "discounts_and_credits", "voucher"]
