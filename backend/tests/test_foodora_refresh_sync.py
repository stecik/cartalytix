"""Encrypted Foodora session rotation survives independent sync failures."""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_foodora_adapter import order, session_credential
from test_lidl_sync import database as database

from cartalytix.credentials import TokenCipher
from cartalytix.models import ProviderConnection, Receipt, SyncRun
from cartalytix.providers.foodora import client as foodora_client
from cartalytix.providers.foodora.client import FoodoraClient, FoodoraError
from cartalytix.settings import Settings
from cartalytix.sync import enqueue_sync, run_sync


@pytest.mark.parametrize("fail_history", [False, True])
def test_rotated_full_session_commits_before_history_failure(
    database: tuple[Engine, Settings, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    fail_history: bool,
) -> None:
    engine, config, owner_id, connection_id = database
    cipher = TokenCipher(config)
    old = session_credential(expires=1)
    fresh = json.loads(session_credential())
    fresh["refresh_token"] = "rotated-refresh"
    fresh["device_token"] = "rotated-device"
    with Session(engine) as db, db.begin():
        connection = db.get(ProviderConnection, connection_id)
        assert connection is not None
        connection.provider = "foodora"
        connection.account_subject = "42"
        connection.encrypted_refresh_token = cipher.encrypt(old)

    def refresh(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload.get("command") == "refresh"
        assert payload.get("session") == json.loads(old)
        return {"session": fresh, "report": {"authenticated": True}}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == f"Bearer {fresh.get('access_token')}"
        if fail_history:
            return httpx.Response(503, json={})
        offset = int(request.url.params.get("offset", "0"))
        return httpx.Response(200, json={"data": {"items": [order()] if offset == 0 else []}})

    monkeypatch.setattr(foodora_client, "execute", refresh)
    monkeypatch.setattr(foodora_client.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:

        def factory(
            credential: str,
            save_refresh: Callable[[str], None],
            *,
            heartbeat: Callable[[], None] | None = None,
        ) -> FoodoraClient:
            return FoodoraClient(credential, save_refresh, http=http, delay=0, heartbeat=heartbeat)

        run = enqueue_sync(engine, connection_id, owner_id)
        run_sync(engine, run.id, config, client_factory=factory)
    with Session(engine) as db:
        connection = db.get(ProviderConnection, connection_id)
        stored = db.get(SyncRun, run.id)
        assert connection is not None and connection.encrypted_refresh_token is not None
        assert json.loads(cipher.decrypt(connection.encrypted_refresh_token)) == fresh
        assert b"rotated-refresh" not in connection.encrypted_refresh_token
        assert connection.account_subject == "42"
        assert stored is not None
        assert stored.status == ("failed" if fail_history else "completed")
        assert stored.error_code == ("provider_unavailable" if fail_history else None)
        assert connection.status == ("error" if fail_history else "connected")
        assert len(list(db.scalars(select(Receipt)))) == (0 if fail_history else 1)


def test_refresh_challenge_is_recoverable_without_reauthentication_status(
    database: tuple[Engine, Settings, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, config, owner_id, connection_id = database
    old = session_credential(expires=1)
    cipher = TokenCipher(config)
    with Session(engine) as db, db.begin():
        connection = db.get(ProviderConnection, connection_id)
        assert connection is not None
        connection.provider = "foodora"
        connection.account_subject = "42"
        connection.encrypted_refresh_token = cipher.encrypt(old)

    def refresh(_payload: dict[str, Any]) -> dict[str, Any]:
        raise FoodoraError("challenge_required")

    monkeypatch.setattr(foodora_client, "execute", refresh)
    run = enqueue_sync(engine, connection_id, owner_id)
    run_sync(engine, run.id, config)
    with Session(engine) as db:
        connection = db.get(ProviderConnection, connection_id)
        stored = db.get(SyncRun, run.id)
        assert connection is not None and connection.status == "error"
        assert connection.encrypted_refresh_token is not None
        assert cipher.decrypt(connection.encrypted_refresh_token) == old
        assert stored is not None and stored.error_code == "challenge_required"
    assert enqueue_sync(engine, connection_id, owner_id).status == "pending"
