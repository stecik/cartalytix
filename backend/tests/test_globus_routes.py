"""Globus linking routes and sync; synthetic cookies, e-mail and receipts only."""

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
from test_globus_adapter import RECEIPT
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.globus.client import GlobusAccount, GlobusError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/globus/connections"


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Globus"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def test_pasted_cookie_login_stores_only_encrypted_cookie(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def identity(cookie: str) -> GlobusAccount:
        seen.append(cookie)
        return GlobusAccount("42", "real@example.com")

    monkeypatch.setattr("cartalytix.globus_routes.fetch_identity", identity)
    identifier = create(authorized)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"cookie": " oidc_session=private-cookie "},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "private-cookie" not in response.text
    assert seen == ["private-cookie"]
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert (connection.account_subject, connection.account_label) == (
            "42",
            "real@example.com",
        )
        cipher = TokenCipher(application(authorized).state.settings)
        assert cipher.decrypt(connection.encrypted_refresh_token) == "private-cookie"


def test_invalid_rejected_and_foreign_account_cookies(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    response = authorized.post(url, json={"cookie": "a;b"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "session_cookie_invalid"

    def rejected(cookie: str) -> GlobusAccount:
        raise GlobusError("session_cookie_rejected")

    monkeypatch.setattr("cartalytix.globus_routes.fetch_identity", rejected)
    response = authorized.post(url, json={"cookie": "c1"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "session_cookie_rejected"
    monkeypatch.setattr(
        "cartalytix.globus_routes.fetch_identity", lambda cookie: GlobusAccount("1", None)
    )
    assert authorized.post(url, json={"cookie": "c1"}, headers=HEADERS).status_code == 200
    monkeypatch.setattr(
        "cartalytix.globus_routes.fetch_identity", lambda cookie: GlobusAccount("2", None)
    )
    response = authorized.post(url, json={"cookie": "c2"}, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "account_mismatch"


def test_globus_is_listed_as_available(authorized: TestClient) -> None:
    providers = authorized.get("/api/v1/providers", headers=HEADERS).json().get("items", [])
    globus = next(p for p in providers if p.get("code") == "globus")
    assert globus.get("status") == "available"


class Receipts:
    def __init__(self, details: dict[str, str]) -> None:
        self.details = details

    def factory(
        self,
        cookie: str,
        save_refresh: Callable[[str], None],
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> Receipts:
        return self

    def page(self, number: int) -> dict[str, Any]:
        rows = [{"id": key, "total": 87} for key in self.details] if number == 1 else []
        return {"receipts": rows, "totalCount": len(self.details)}

    def detail(self, identifier: str) -> dict[str, Any]:
        return ProviderPayload({"id": identifier, "text": self.details[identifier]}, b"%PDF")

    def close(self) -> None:
        pass


def test_sync_imports_parsed_pdf_receipts(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "globus"
    receipts = Receipts({"r1": RECEIPT})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=receipts.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        receipt = db.scalar(select(Receipt))
        assert receipt is not None and receipt.merchant == "Globus"
        assert receipt.total_amount == Decimal("86.90")
        assert receipt.source_format == "pdf_text"
        items = db.execute(
            select(ReceiptItem.name, ReceiptItem.quantity, ReceiptItem.total_amount)
            .where(ReceiptItem.receipt_id == receipt.id)
            .order_by(ReceiptItem.position)
        ).all()
        assert [(name, quantity, amount) for name, quantity, amount in items] == [
            ("SYNTETICKÝ CHLÉB 500G", Decimal("2"), Decimal("21.00")),
            ("TESTOVACÍ VODA 1,5L", Decimal("1"), Decimal("15.90")),
            ("ŠUNKA VÁŽENÁ", Decimal("0.25"), Decimal("50.00")),
        ]
