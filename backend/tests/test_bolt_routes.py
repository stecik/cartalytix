"""Bolt Food linking routes and sync; synthetic phone, codes and provider data only."""

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
from test_bolt_adapter import order
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import Discount, ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.bolt.client import BoltAccount, BoltError, account_subject
from cartalytix.providers.common import ProviderPayload
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/bolt/connections"
PHONE = "+420000000000"


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Bolt"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "cartalytix.bolt_routes.start_verification",
        lambda phone, device: calls.append((phone, device)),
    )
    return calls


def confirmed(subject_phone: str = PHONE) -> Callable[[str, str, str], BoltAccount]:
    def confirm(phone: str, device: str, code: str) -> BoltAccount:
        assert (phone, code) == (PHONE, "1234")
        credential = json.dumps({"refresh_token": "private-refresh", "device_id": device})
        return BoltAccount(credential=credential, subject=account_subject(subject_phone))

    return confirm


def test_phone_and_code_login_stores_only_encrypted_refresh(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = started(monkeypatch)
    monkeypatch.setattr("cartalytix.bolt_routes.confirm_verification", confirmed())
    identifier = create(authorized)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login", json={"phone": PHONE}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    flow_id = response.json().get("flow_id")
    assert PHONE not in flow_id and calls and calls[0][0] == PHONE
    response = authorized.post(
        f"{PREFIX}/{identifier}/login/verify",
        json={"flow_id": flow_id, "code": "1234"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    assert "private-refresh" not in response.text
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert connection.account_subject == account_subject(PHONE)
        stored = TokenCipher(application(authorized).state.settings).decrypt(
            connection.encrypted_refresh_token
        )
        assert json.loads(stored) == {"refresh_token": "private-refresh", "device_id": calls[0][1]}


def test_flow_is_bound_to_connection_and_account(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started(monkeypatch)
    monkeypatch.setattr("cartalytix.bolt_routes.confirm_verification", confirmed())
    first, second = create(authorized), create(authorized)
    flow_id = (
        authorized.post(f"{PREFIX}/{first}/login", json={"phone": PHONE}, headers=HEADERS)
        .json()
        .get("flow_id")
    )
    body = {"flow_id": flow_id, "code": "1234"}
    response = authorized.post(f"{PREFIX}/{second}/login/verify", json=body, headers=HEADERS)
    assert response.status_code == 409
    assert response.json().get("error", {}).get("code") == "login_expired"
    assert (
        authorized.post(f"{PREFIX}/{first}/login/verify", json=body, headers=HEADERS).status_code
        == 200
    )
    monkeypatch.setattr("cartalytix.bolt_routes.confirm_verification", confirmed("+420111111111"))
    response = authorized.post(f"{PREFIX}/{first}/login/verify", json=body, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "account_mismatch"


def test_invalid_phone_and_rejected_code(
    authorized: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    started(monkeypatch)
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    assert authorized.post(url, json={"phone": "777123"}, headers=HEADERS).status_code == 422

    def rejected(phone: str, device: str, code: str) -> BoltAccount:
        raise BoltError("otp_rejected")

    monkeypatch.setattr("cartalytix.bolt_routes.confirm_verification", rejected)
    flow_id = authorized.post(url, json={"phone": PHONE}, headers=HEADERS).json().get("flow_id")
    response = authorized.post(
        f"{url}/verify", json={"flow_id": flow_id, "code": "0000"}, headers=HEADERS
    )
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "otp_rejected"


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
        orders = [{"order_id": int(key), "city_id": 3} for key in self.details]
        return {"orders": orders if number == 1 else []}

    def detail(self, identifier: str) -> dict[str, Any]:
        # Like BoltClient: decoded Decimal data plus the original bytes for archiving.
        return ProviderPayload(self.details[identifier], b'{"code":0}')

    def close(self) -> None:
        pass


def test_sync_imports_delivered_orders_under_restaurant_name(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "bolt"
    rejected = {**order(state="rejected"), "order_id": 8}
    orders = Orders({"7": order(), "8": rejected})
    run = enqueue_sync(engine, connection, owner)
    run_sync(engine, run.id, config, client_factory=orders.factory)
    with Session(engine) as db:
        stored = db.get(SyncRun, run.id)
        assert stored is not None
        assert stored.status == "completed", stored.error_code
        assert (stored.receipts_imported, stored.receipts_skipped) == (1, 1)
        receipt = db.scalar(select(Receipt))
        assert receipt is not None and receipt.external_id == "7"
        assert receipt.merchant == "Synthetic Bistro"
        assert receipt.total_amount == Decimal("202.70")
        names = db.scalars(
            select(ReceiptItem.name)
            .where(ReceiptItem.receipt_id == receipt.id)
            .order_by(ReceiptItem.position)
        ).all()
        assert names == ["Noodles", "Poplatek za služby", "Poplatek za doručení"]
        amounts = sorted(db.scalars(select(Discount.amount)).all())
        assert amounts == [Decimal("4.20"), Decimal("10"), Decimal("65")]
