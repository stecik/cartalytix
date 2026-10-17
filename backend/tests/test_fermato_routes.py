"""Fermato linking routes and sync; synthetic credentials and provider data only."""

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
from test_fermato_adapter import detail_html
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.fermato.client import FermatoError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/fermato/connections"
CREDENTIALS = {"email": "owner@example.com", "password": "synthetic-password"}


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Fermato"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert response.json().get("provider") == "fermato"
    return str(response.json().get("id"))


def test_account_name_is_optional(authorized: TestClient) -> None:
    response = authorized.post(PREFIX, json={}, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert response.json().get("name") == ""
    identifier = response.json().get("id")
    response = authorized.patch(f"{PREFIX}/{identifier}", json={"name": "  "}, headers=HEADERS)
    assert (response.status_code, response.json().get("name")) == (200, "")


def accepted(subject: str = "account-hash") -> Callable[[str, str], str]:
    def sign_in(email: str, password: str) -> str:
        assert (email, password) == ("owner@example.com", "synthetic-password")
        return subject

    return sign_in


def test_login_encrypts_credentials_without_exposing_them(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.fermato_routes.sign_in", accepted())
    identifier = create(authorized)
    response = authorized.post(f"{PREFIX}/{identifier}/login", json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "synthetic-password" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.account_subject == "account-hash"
        assert connection.account_label == "owner@example.com"
        assert connection.encrypted_refresh_token is not None
        assert b"synthetic-password" not in connection.encrypted_refresh_token
        settings = application(authorized).state.settings
        stored = TokenCipher(settings).decrypt(connection.encrypted_refresh_token)
        assert json.loads(stored) == CREDENTIALS
    providers = authorized.get("/api/v1/providers").json().get("items")
    assert next(p for p in providers if p.get("code") == "fermato").get("status") == "available"


def test_login_rejects_other_account_and_bad_credentials(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cartalytix.fermato_routes.sign_in", accepted())
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    assert authorized.post(url, json=CREDENTIALS, headers=HEADERS).status_code == 200
    monkeypatch.setattr("cartalytix.fermato_routes.sign_in", accepted("other"))
    response = authorized.post(url, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "account_mismatch"

    def rejected(email: str, password: str) -> str:
        raise FermatoError("credentials_rejected")

    monkeypatch.setattr("cartalytix.fermato_routes.sign_in", rejected)
    response = authorized.post(url, json=CREDENTIALS, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "credentials_rejected"


class Orders:
    def __init__(self, details: dict[str, str]) -> None:
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
        orders = [
            {"code": code, "date": "2026-07-12T21:03:00", "total": "838"} for code in self.details
        ]
        return {"orders": orders if number == 1 else [], "totalRecords": None}

    def detail(self, identifier: str) -> dict[str, Any]:
        html = self.details[identifier].replace("č. 100", f"č. {identifier}")
        return {"id": identifier, "html": html}

    def close(self) -> None:
        pass


def test_sync_imports_orders_with_discounts_fees_and_rounding(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "fermato"
    orders = Orders({"100": detail_html(), "200": detail_html("0 Kč")})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=orders.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert stored.receipts_imported == 2
        receipts = {r.external_id: r for r in db.scalars(select(Receipt))}
        assert receipts["100"].merchant == "Fermato"
        assert receipts["100"].total_amount == Decimal("838")
        assert receipts["100"].rounding_amount == Decimal("0.05")
        assert receipts["200"].total_amount == Decimal("837.95")
        items = db.scalars(
            select(ReceiptItem.name).where(ReceiptItem.receipt_id == receipts["100"].id)
        ).all()
        assert sorted(items) == ["Olej 500 ml", "PPL", "Zálivka"]
        discounts = db.execute(
            select(Discount.name, Discount.amount, Discount.item_id).where(
                Discount.receipt_id == receipts["100"].id
            )
        ).all()
        assert {(name, amount) for name, amount, _ in discounts} == {
            ("Sleva 15 %", Decimal("58.05")),
            ("Kupon", Decimal("134")),
        }
        assert sum(item_id is None for _, _, item_id in discounts) == 1
