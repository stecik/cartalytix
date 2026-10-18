"""Wolt linking routes and sync; synthetic e-mail, links and provider data only."""

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
from test_wolt_adapter import LINK, purchase

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.wolt.client import WoltAccount, WoltError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/wolt/connections"


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Wolt"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def test_pasted_link_login_stores_only_encrypted_refresh(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cartalytix.wolt_routes.exchange_magic_link",
        lambda token: (
            WoltAccount("private-refresh", "user-1", "real@example.com")
            if token == "magic-1"
            else None
        ),
    )
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/login", json={"link": LINK}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "private-refresh" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert (
            connection.account_subject == "user-1"
            and connection.account_label == "real@example.com"
        )
        cipher = TokenCipher(application(authorized).state.settings)
        assert cipher.decrypt(connection.encrypted_refresh_token) == "private-refresh"


def test_invalid_rejected_and_foreign_account_links(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    foreign = {"link": "https://evil.example/me/magic_login?email=a%40example.com&token=x"}
    response = authorized.post(url, json=foreign, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "magic_link_invalid"

    def rejected(token: str) -> str:
        raise WoltError("magic_link_rejected")

    monkeypatch.setattr("cartalytix.wolt_routes.exchange_magic_link", rejected)
    response = authorized.post(url, json={"link": LINK}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "magic_link_rejected"
    monkeypatch.setattr(
        "cartalytix.wolt_routes.exchange_magic_link", lambda token: WoltAccount("r", "user-1", None)
    )
    assert authorized.post(url, json={"link": LINK}, headers=HEADERS).status_code == 200
    monkeypatch.setattr(
        "cartalytix.wolt_routes.exchange_magic_link", lambda token: WoltAccount("r", "user-2", None)
    )
    response = authorized.post(url, json={"link": LINK}, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "account_mismatch"


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
        return self

    def page(self, number: int) -> dict[str, Any]:
        rows = [{"purchase_id": key} for key in self.details] if number == 1 else []
        return {"orders": rows}

    def detail(self, identifier: str) -> dict[str, Any]:
        return ProviderPayload(self.details[identifier], b"{}")

    def close(self) -> None:
        pass


def test_sync_imports_delivered_purchases_under_venue_name(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "wolt"
    rejected = {**purchase(status="rejected"), "order_id": "p2"}
    orders = Orders({"p1": purchase(), "p2": rejected})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=orders.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert (stored.receipts_imported, stored.receipts_skipped) == (1, 1)
        receipt = db.scalar(select(Receipt))
        assert receipt is not None and receipt.merchant == "Synthetic Venue"
        assert receipt.total_amount == Decimal("239")
        names = db.scalars(
            select(ReceiptItem.name)
            .where(ReceiptItem.receipt_id == receipt.id)
            .order_by(ReceiptItem.position)
        ).all()
        assert names == ["Pizza", "Delivery"]
        assert sorted(db.scalars(select(Discount.amount)).all()) == [Decimal("8"), Decimal("20")]
