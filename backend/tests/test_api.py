from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from os import environ
from typing import cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

from cartalytix.main import create_app
from cartalytix.models import Base, ProviderConnection, Receipt, User, UserSession
from cartalytix.search import register_sqlite
from cartalytix.security import hash_password, token_hash
from cartalytix.settings import Settings


@pytest.fixture
def engine() -> Iterator[Engine]:
    url = environ.get("DATABASE_URL") or environ.get("CARTALYTIX_TEST_DATABASE_URL")
    if not url:
        database = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        event.listen(database, "connect", register_sqlite)
        Base.metadata.create_all(database)
        yield database
        database.dispose()
        return
    parsed = make_url(url)
    if (
        environ.get("CARTALYTIX_ENVIRONMENT") != "test"
        or not parsed.drivername.startswith("postgresql")
        or not (parsed.database or "").endswith("_test")
    ):
        pytest.fail(
            "Integration tests require test environment and PostgreSQL database ending _test"
        )
    schema = f"test_{uuid4().hex}"
    database = create_engine(url)
    with database.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = database.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        yield isolated
    finally:
        with database.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        database.dispose()


class UnlimitedLimiter:
    def check(self, address: str, email: str) -> bool:
        return True


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with Session(engine) as db:
        owner = User(
            email="owner@example.com", password_hash=hash_password("Correct-horse-battery")
        )
        other = User(
            email="other@example.com", password_hash=hash_password("Other-secret-password")
        )
        db.add_all([owner, other])
        db.flush()
        own = ProviderConnection(owner_id=owner.id, provider="lidl", name="My Lidl")
        foreign = ProviderConnection(owner_id=other.id, provider="lidl", name="Private Lidl")
        db.add_all([own, foreign])
        db.flush()
        for connection, currency, amount, external_id in [
            (own, "CZK", "100.25", "1"),
            (own, "EUR", "2.50", "2"),
            (foreign, "CZK", "999.00", "3"),
        ]:
            db.add(
                Receipt(
                    connection_id=connection.id,
                    external_id=external_id,
                    purchased_at=datetime.now(UTC),
                    merchant="Lidl",
                    currency=currency,
                    total_amount=Decimal(amount),
                )
            )
        db.commit()
    settings = Settings(
        environment="test",
        database_url="sqlite://",
        cookie_secure=False,
        allowed_origins=["http://testserver"],
        token_key_file=None,
    )
    app = create_app(settings, engine=engine, limiter=UnlimitedLimiter())
    with TestClient(app) as test_client:
        yield test_client


def application(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def login(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Correct-horse-battery"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=86400" in cookie
    assert response.headers.get("cache-control") == "no-store"


def test_session_requires_password_and_origin(client: TestClient, engine: Engine) -> None:
    for endpoint in ["auth/me", "connections", "receipts", "overview"]:
        assert client.get(f"/api/v1/{endpoint}").status_code == 401
    payload = {"email": "owner@example.com", "password": "wrong password"}
    assert client.post("/api/v1/auth/login", json=payload).status_code == 403
    assert (
        client.post(
            "/api/v1/auth/login", json=payload, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/login", json=payload, headers={"Origin": "http://testserver"}
        ).status_code
        == 401
    )
    login(client)
    assert client.get("/api/v1/auth/me").json().get("email") == "owner@example.com"
    raw_token = client.cookies.get("cartalytix_session")
    assert raw_token is not None
    with Session(engine) as db:
        stored = db.scalar(select(UserSession))
        assert stored is not None
        assert stored.token_hash == token_hash(raw_token)
        assert stored.token_hash != raw_token
    response = client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"})
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_expired_session_rejected(client: TestClient, engine: Engine) -> None:
    login(client)
    with Session(engine) as db:
        stored = db.scalar(select(UserSession))
        assert stored is not None
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rotates_session(client: TestClient, engine: Engine) -> None:
    login(client)
    old = client.cookies.get("cartalytix_session")
    login(client)
    assert old != client.cookies.get("cartalytix_session")
    assert old is not None
    with Session(engine) as db:
        assert db.get(UserSession, token_hash(old)) is None
        assert db.scalar(select(func.count()).select_from(UserSession)) == 1


def test_owner_scope_and_currency_aggregates(client: TestClient, engine: Engine) -> None:
    login(client)
    connections = client.get("/api/v1/connections").json().get("items")
    assert len(connections) == 1
    receipts = client.get("/api/v1/receipts?limit=1&offset=0").json()
    assert receipts.get("total") == 2
    assert len(receipts.get("items")) == 1
    overview = client.get("/api/v1/overview").json()
    assert overview.get("receipt_count") == 2
    assert overview.get("connection_count") == 1
    assert overview.get("totals") == [
        {"currency": "CZK", "amount": "100.25"},
        {"currency": "EUR", "amount": "2.50"},
    ]
    assert len(overview.get("latest_receipts")) == 2
    with Session(engine) as db:
        foreign = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "Private Lidl")
        )
    private = client.get(f"/api/v1/receipts?connection_id={foreign}").json()
    assert private.get("items") == []
    assert private.get("total") == 0
    assert client.get("/api/v1/receipts?limit=101").status_code == 422


def test_bounded_queries(client: TestClient, engine: Engine) -> None:
    login(client)
    queries: list[str] = []

    def track(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", track)
    assert client.get("/api/v1/overview").status_code == 200
    small = len(queries)
    assert small <= 6
    event.remove(engine, "before_cursor_execute", track)
    with Session(engine) as db:
        connection_id = db.scalar(
            select(ProviderConnection.id).where(ProviderConnection.name == "My Lidl")
        )
        for number in range(30):
            db.add(
                Receipt(
                    connection_id=connection_id,
                    external_id=f"more-{number}",
                    purchased_at=datetime.now(UTC),
                    merchant="Lidl",
                    currency="CZK",
                    total_amount=Decimal("1.00"),
                )
            )
        db.commit()
    queries.clear()
    event.listen(engine, "before_cursor_execute", track)
    response = client.get("/api/v1/overview")
    assert response.status_code == 200
    assert len(response.json().get("latest_receipts")) == 5
    assert len(queries) == small
    queries.clear()
    assert client.get("/api/v1/receipts?limit=30").status_code == 200
    assert len(queries) <= 3
    event.remove(engine, "before_cursor_execute", track)


class DeniedLimiter:
    def check(self, address: str, email: str) -> bool:
        return False


class UnavailableLimiter:
    def check(self, address: str, email: str) -> bool:
        raise RedisConnectionError("unavailable")


@pytest.mark.parametrize("limiter,status", [(DeniedLimiter(), 429), (UnavailableLimiter(), 503)])
def test_login_fails_closed(client: TestClient, limiter: object, status: int) -> None:
    application(client).state.limiter = limiter
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Correct-horse-battery"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == status
    assert not client.cookies.get("cartalytix_session")


def test_validation_and_translation(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers={"Accept-Language": "cs-CZ"})
    assert response.json().get("error") == {
        "code": "authentication_required",
        "message": "Je vyžadováno přihlášení",
        "params": {},
    }
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "x" * 1025},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert "xxxxx" not in response.text


def test_health_and_disabled_providers(client: TestClient) -> None:
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}
    providers = client.get("/api/v1/providers").json().get("items")
    assert len(providers) == 11
    assert all(provider.get("status") == "not_implemented" for provider in providers)
