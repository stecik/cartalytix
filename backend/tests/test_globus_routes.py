"""Globus linking routes and sync; synthetic cookies, e-mail and receipts only."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import application
from test_api import client as client
from test_api import engine as engine
from test_globus_adapter import RECEIPT
from test_globus_session import profile
from test_lidl_routes import HEADERS
from test_lidl_routes import authorized as authorized
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection, Receipt, ReceiptItem, SyncRun
from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.globus.client import GlobusClient, GlobusError
from cartalytix.providers.globus.session import GlobusSession, decode_session
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync

PREFIX = "/api/v1/providers/globus/connections"


def create(client: TestClient) -> str:
    response = client.post(PREFIX, json={"name": "Globus"}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return str(response.json().get("id"))


def test_username_password_login_stores_only_encrypted_session(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    def sign_in(username: str, password: str) -> GlobusSession:
        seen.append((username, password))
        return GlobusSession(
            "private-cookie", "private-session", "42", "Chrome/140", email="real@example.com"
        )

    monkeypatch.setattr("cartalytix.globus_routes.login", sign_in, raising=False)
    identifier = create(authorized)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"username": " user@example.com ", "password": "private-password"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json().get("status") == "connected"
    for secret in ("private-cookie", "private-session", "private-password"):
        assert secret not in response.text
    assert seen == [("user@example.com", "private-password")]
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert (connection.account_subject, connection.account_label) == (
            "42",
            "real@example.com",
        )
        cipher = TokenCipher(application(authorized).state.settings)
        decrypted = cipher.decrypt(connection.encrypted_refresh_token)
        stored = decode_session(decrypted)
        assert stored is not None
        assert (stored.cookie, stored.session_id, stored.subject) == (
            "private-cookie",
            "private-session",
            "42",
        )
        assert "private-password" not in decrypted
        assert "user@example.com" not in decrypted


def test_invalid_rejected_and_foreign_account_credentials(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    url = f"{PREFIX}/{identifier}/login"
    response = authorized.post(url, json={"cookie": "old-workflow"}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "validation_error"

    def rejected(username: str, password: str) -> GlobusSession:
        raise GlobusError("credentials_rejected")

    credentials = {"username": "card-number", "password": "private-password"}
    monkeypatch.setattr("cartalytix.globus_routes.login", rejected, raising=False)
    response = authorized.post(url, json=credentials, headers=HEADERS)
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "credentials_rejected"
    monkeypatch.setattr(
        "cartalytix.globus_routes.login",
        lambda username, password: GlobusSession("c1", "s1", "1", "Chrome/140"),
    )
    assert authorized.post(url, json=credentials, headers=HEADERS).status_code == 200
    with Session(engine) as db:
        original = db.get(ProviderConnection, UUID(identifier))
        assert original is not None
        encrypted = original.encrypted_refresh_token
    monkeypatch.setattr(
        "cartalytix.globus_routes.login",
        lambda username, password: GlobusSession("c2", "s2", "2", "Chrome/140"),
    )
    response = authorized.post(url, json=credentials, headers=HEADERS)
    assert response.json().get("error", {}).get("code") == "account_mismatch"
    with Session(engine) as db:
        unchanged = db.get(ProviderConnection, UUID(identifier))
        assert unchanged is not None
        assert unchanged.encrypted_refresh_token == encrypted


@pytest.mark.parametrize("username", ["", "   ", "x" * 255])
def test_username_validation_precedes_provider_login(
    authorized: TestClient, username: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(username: str, password: str) -> GlobusSession:
        pytest.fail("Invalid input must not reach Globus")

    monkeypatch.setattr("cartalytix.globus_routes.login", unexpected, raising=False)
    response = authorized.post(
        f"{PREFIX}/{create(authorized)}/login",
        json={"username": username, "password": "private-password"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert "private-password" not in response.text


def test_login_preserves_session_when_provider_rejects_reauthentication(
    authorized: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    identifier = create(authorized)
    with Session(engine) as db, db.begin():
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None
        connection.account_subject = "42"
        connection.status = "connected"
        connection.encrypted_refresh_token = b"unchanged-encrypted-session"

    def rejected(username: str, password: str) -> GlobusSession:
        raise GlobusError("captcha_required")

    monkeypatch.setattr("cartalytix.globus_routes.login", rejected, raising=False)
    response = authorized.post(
        f"{PREFIX}/{identifier}/login",
        json={"username": "user@example.com", "password": "private-password"},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json().get("error", {}).get("code") == "captcha_required"
    with Session(engine) as db:
        connection = db.get(ProviderConnection, UUID(identifier))
        assert connection is not None
        assert connection.status == "connected"
        assert connection.encrypted_refresh_token == b"unchanged-encrypted-session"


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


def test_sync_persists_rotated_globus_session_encrypted(
    database: tuple[Engine, Settings, UUID, UUID],
) -> None:
    engine, config, owner, connection = database
    cipher = TokenCipher(config)
    original = GlobusSession("old-cookie", "old-session", "42", "Chrome/140")
    with Session(engine) as db, db.begin():
        row = db.get(ProviderConnection, connection)
        assert row is not None
        row.provider = "globus"
        row.account_subject = "42"
        row.encrypted_refresh_token = cipher.encrypt(original.serialize())

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session/refresh"):
            return httpx.Response(
                200,
                json={"session_id": "new-session"},
                headers={"set-cookie": "oidc_session=new-cookie; Secure; Path=/"},
            )
        if request.url.path.endswith("/auth-status"):
            return httpx.Response(200, json=profile())
        return httpx.Response(200, json={"receipts": [], "totalCount": 0})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:

        def factory(
            credential: str,
            save_refresh: Callable[[str], None],
            *,
            heartbeat: Callable[[], None] | None = None,
        ) -> GlobusClient:
            return GlobusClient(credential, save_refresh, http=http, delay=0, heartbeat=heartbeat)

        run = enqueue_sync(engine, connection, owner)
        run_sync(engine, run.id, config, client_factory=factory)
    with Session(engine) as db:
        result = db.get(SyncRun, run.id)
        stored = db.get(ProviderConnection, connection)
        assert result is not None and result.status == "completed"
        assert stored is not None and stored.encrypted_refresh_token is not None
        renewed = decode_session(cipher.decrypt(stored.encrypted_refresh_token))
        assert renewed is not None and renewed.cookie == "new-cookie"
        assert renewed.session_id == "new-session"
        assert stored.account_subject == "42"
