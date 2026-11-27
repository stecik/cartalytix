import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_api import UnlimitedLimiter
from test_api import client as client
from test_api import engine as engine

from cartalytix.admin_routes import BOOTSTRAP_EMAIL, bootstrap_admin
from cartalytix.main import create_app
from cartalytix.models import Base, ProviderConnection, Receipt, User, UserSession
from cartalytix.security import hash_password, verify_password
from cartalytix.settings import Settings

ORIGIN = {"Origin": "http://testserver"}
ADMIN_PASSWORD = "Admin-secret-pass"


def sign_in(client: TestClient, email: str, password: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200
    return response.json()


def add_admin(engine: Engine) -> None:
    with Session(engine) as db:
        db.add(
            User(
                email="root@example.com",
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
        )
        db.commit()


def test_bootstrap_admin_only_on_empty_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARTALYTIX_BOOTSTRAP_PASSWORD", ADMIN_PASSWORD)
    database = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(database)
    try:
        settings = Settings(
            environment="test",
            database_url="sqlite://",
            cookie_secure=False,
            allowed_origins=["http://testserver"],
            token_key_file=None,
        )
        with TestClient(create_app(settings, engine=database, limiter=UnlimitedLimiter())):
            pass
        bootstrap_admin(database, None)
        with Session(database) as db:
            admin = db.scalar(select(User))
            assert admin is not None
            assert db.scalar(select(func.count()).select_from(User)) == 1
            assert admin.email == BOOTSTRAP_EMAIL
            assert admin.is_admin
            assert admin.must_change_password
            assert verify_password(admin.password_hash, ADMIN_PASSWORD)
    finally:
        database.dispose()


@pytest.mark.parametrize("password", [None, "", "too-weak"])
def test_empty_database_requires_bootstrap_password(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, password: str | None
) -> None:
    if password is None:
        monkeypatch.delenv("CARTALYTIX_BOOTSTRAP_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("CARTALYTIX_BOOTSTRAP_PASSWORD", password)

    settings = Settings(environment="test")
    app = create_app(settings, engine=engine, limiter=UnlimitedLimiter())
    with pytest.raises(RuntimeError, match="CARTALYTIX_BOOTSTRAP_PASSWORD"), TestClient(app):
        pass

    with Session(engine) as db:
        assert db.scalar(select(User.id).limit(1)) is None


def test_bootstrap_admin_skips_existing_accounts(client: TestClient, engine: Engine) -> None:
    bootstrap_admin(engine, None)
    with Session(engine) as db:
        assert db.scalar(select(User.id).where(User.is_admin.is_(True))) is None


def test_first_login_requires_new_email_and_password(client: TestClient, engine: Engine) -> None:
    with Session(engine) as db:
        db.add(
            User(
                email=BOOTSTRAP_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
                must_change_password=True,
            )
        )
        db.commit()
    user = sign_in(client, BOOTSTRAP_EMAIL, ADMIN_PASSWORD)
    assert user.get("is_admin") is True
    assert user.get("must_change_password") is True
    blocked = client.get("/api/v1/admin/users")
    assert blocked.status_code == 403
    assert blocked.json().get("error", {}).get("code") == "password_change_required"
    assert client.patch("/api/v1/auth/me", json={"locale": "cs"}, headers=ORIGIN).status_code == 403
    # The forced change follows a fresh sign-in, so the current password is not asked again.
    change = {"new_password": "New-admin-pass"}
    same_email = client.post("/api/v1/auth/credentials", json=change, headers=ORIGIN)
    assert same_email.json().get("error", {}).get("code") == "email_change_required"
    # The forced change must replace the issued password, not just the e-mail.
    email_only = {"email": "boss@example.com"}
    no_password = client.post("/api/v1/auth/credentials", json=email_only, headers=ORIGIN)
    assert no_password.json().get("error", {}).get("code") == "weak_password"
    for weak in ["Sh0rt!", "no-uppercase", "NO-LOWERCASE", "NoSpecial1"]:
        rejected = client.post(
            "/api/v1/auth/credentials",
            json={"new_password": weak, "email": "boss@example.com"},
            headers=ORIGIN,
        )
        assert rejected.json().get("error", {}).get("code") == "weak_password"
    taken = {**change, "email": "owner@example.com"}
    assert client.post("/api/v1/auth/credentials", json=taken, headers=ORIGIN).status_code == 409
    done = client.post(
        "/api/v1/auth/credentials", json={**change, "email": "Boss@Example.com"}, headers=ORIGIN
    )
    assert done.status_code == 200
    assert done.json().get("email") == "boss@example.com"
    assert done.json().get("must_change_password") is False
    assert client.get("/api/v1/admin/users").status_code == 200
    forbidden = client.get("/api/v1/receipts")
    assert forbidden.status_code == 403
    assert forbidden.json().get("error", {}).get("code") == "admin_forbidden"
    assert client.get("/api/v1/connections").status_code == 403
    assert (
        client.patch("/api/v1/auth/me", json={"theme": "dark"}, headers=ORIGIN).status_code == 200
    )
    client.post("/api/v1/auth/logout", headers=ORIGIN)
    sign_in(client, "boss@example.com", "New-admin-pass")


def test_admin_creates_user_who_must_change_password(client: TestClient, engine: Engine) -> None:
    add_admin(engine)
    sign_in(client, "owner@example.com", "Correct-horse-battery")
    regular = client.get("/api/v1/admin/users")
    assert regular.json().get("error", {}).get("code") == "admin_required"
    sign_in(client, "root@example.com", ADMIN_PASSWORD)
    listed = client.get("/api/v1/admin/users").json().get("items", [])
    assert [item.get("email") for item in listed] == [
        "root@example.com",
        "other@example.com",
        "owner@example.com",
    ]
    created = client.post("/api/v1/admin/users", json={"email": "New@Example.com"}, headers=ORIGIN)
    assert created.status_code == 201
    password = created.json().get("password")
    assert isinstance(password, str)
    assert len(password) >= 20
    assert created.json().get("user", {}).get("must_change_password") is True
    duplicate = client.post(
        "/api/v1/admin/users", json={"email": "new@example.com"}, headers=ORIGIN
    )
    assert duplicate.status_code == 409
    user = sign_in(client, "new@example.com", password)
    assert user.get("must_change_password") is True
    assert client.get("/api/v1/receipts").status_code == 403
    unchanged = client.post(
        "/api/v1/auth/credentials", json={"new_password": password}, headers=ORIGIN
    )
    assert unchanged.json().get("error", {}).get("code") == "password_unchanged"
    change = {"new_password": "My-own-pass1"}
    assert client.post("/api/v1/auth/credentials", json=change, headers=ORIGIN).status_code == 200
    assert client.get("/api/v1/receipts").status_code == 200
    # A later, voluntary change must prove the current password.
    again = {"new_password": "Another-pass2"}
    for attempt in [again, {**again, "current_password": "Wrong-pass1"}]:
        denied = client.post("/api/v1/auth/credentials", json=attempt, headers=ORIGIN)
        assert denied.json().get("error", {}).get("code") == "invalid_current_password"
    again["current_password"] = "My-own-pass1"
    assert client.post("/api/v1/auth/credentials", json=again, headers=ORIGIN).status_code == 200
    # The e-mail alone can change too, but only with the current password and to a new value.
    current = {"current_password": "Another-pass2"}
    for attempt, code in [
        ({"email": "renamed@example.com"}, "invalid_current_password"),
        ({**current, "email": "new@example.com"}, "credentials_unchanged"),
        ({**current, "email": "owner@example.com"}, "email_taken"),
    ]:
        denied = client.post("/api/v1/auth/credentials", json=attempt, headers=ORIGIN)
        assert denied.json().get("error", {}).get("code") == code
    renamed = client.post(
        "/api/v1/auth/credentials",
        json={**current, "email": "Renamed@Example.com"},
        headers=ORIGIN,
    )
    assert renamed.json().get("email") == "renamed@example.com"
    client.post("/api/v1/auth/logout", headers=ORIGIN)
    sign_in(client, "renamed@example.com", "Another-pass2")


def test_admin_resets_disables_and_deletes_users(client: TestClient, engine: Engine) -> None:
    add_admin(engine)
    sign_in(client, "owner@example.com", "Correct-horse-battery")
    owner_cookie = client.cookies.get("cartalytix_session")
    sign_in(client, "root@example.com", ADMIN_PASSWORD)
    admin_cookie = client.cookies.get("cartalytix_session")
    with Session(engine) as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        root = db.scalar(select(User).where(User.email == "root@example.com"))
        assert owner is not None
        assert root is not None
        owner_id, root_id = owner.id, root.id
    assert client.delete(f"/api/v1/admin/users/{root_id}", headers=ORIGIN).status_code == 403
    reset = client.post(f"/api/v1/admin/users/{owner_id}/reset-password", headers=ORIGIN)
    assert reset.status_code == 200
    with Session(engine) as db:
        assert db.scalar(select(UserSession).where(UserSession.owner_id == owner_id)) is None
    disabled = client.patch(
        f"/api/v1/admin/users/{owner_id}", json={"is_disabled": True}, headers=ORIGIN
    )
    assert disabled.json().get("is_disabled") is True
    rejected = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": reset.json().get("password")},
        headers=ORIGIN,
    )
    assert rejected.json().get("error", {}).get("code") == "account_disabled"
    client.cookies.set("cartalytix_session", owner_cookie or "")
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set("cartalytix_session", admin_cookie or "")
    enabled = client.patch(
        f"/api/v1/admin/users/{owner_id}", json={"is_disabled": False}, headers=ORIGIN
    )
    assert enabled.json().get("is_disabled") is False
    assert client.delete(f"/api/v1/admin/users/{owner_id}", headers=ORIGIN).status_code == 204
    assert client.delete(f"/api/v1/admin/users/{owner_id}", headers=ORIGIN).status_code == 404
    with Session(engine) as db:
        assert db.get(User, owner_id) is None
        assert (
            db.scalar(select(ProviderConnection).where(ProviderConnection.owner_id == owner_id))
            is None
        )
        assert db.scalar(select(func.count()).select_from(Receipt)) == 1
        assert db.scalar(select(func.count()).select_from(ProviderConnection)) == 1
