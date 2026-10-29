from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import ProviderConnection, User
from cartalytix.receipt_routes import MANUAL_PROVIDER

ORIGIN = {"Origin": "http://testserver"}


def test_settings_require_session_and_trusted_origin(client: TestClient) -> None:
    assert client.patch("/api/v1/auth/me", json={"locale": "cs"}, headers=ORIGIN).status_code == 401
    login(client)
    assert client.patch("/api/v1/auth/me", json={"locale": "cs"}).status_code == 403


def test_settings_defaults_and_persist_per_user(client: TestClient, engine: Engine) -> None:
    login(client)
    me = client.get("/api/v1/auth/me").json()
    assert me.get("locale") == "en"
    assert me.get("receipt_view") == "rendered"
    response = client.patch(
        "/api/v1/auth/me", json={"locale": "cs", "receipt_view": "detailed"}, headers=ORIGIN
    )
    assert response.status_code == 200
    assert response.json().get("locale") == "cs"
    assert response.json().get("receipt_view") == "detailed"
    response = client.patch("/api/v1/auth/me", json={"locale": "en"}, headers=ORIGIN)
    assert response.json().get("receipt_view") == "detailed"
    with Session(engine) as db:
        settings = {
            email: (locale, view)
            for email, locale, view in db.execute(
                select(User.email, User.locale, User.receipt_view)
            )
        }
    assert settings.get("owner@example.com") == ("en", "detailed")
    assert settings.get("other@example.com") == ("en", "rendered")


def test_settings_reject_unknown_values(client: TestClient) -> None:
    login(client)
    for payload in [{"locale": "de"}, {"receipt_view": "raw"}, {"email": "x@example.com"}]:
        assert client.patch("/api/v1/auth/me", json=payload, headers=ORIGIN).status_code == 422


def test_theme_defaults_to_system(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "Correct-horse-battery"},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    assert response.json().get("theme") == "system"
    assert client.get("/api/v1/auth/me").json().get("theme") == "system"


@pytest.mark.parametrize("theme", ["dark", "light", "system"])
def test_theme_persists_per_user(client: TestClient, engine: Engine, theme: str) -> None:
    login(client)
    assert (
        client.patch("/api/v1/auth/me", json={"theme": "dark"}, headers=ORIGIN).status_code == 200
    )
    response = client.patch("/api/v1/auth/me", json={"theme": theme}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json().get("theme") == theme
    assert client.get("/api/v1/auth/me").json().get("theme") == theme
    response = client.patch("/api/v1/auth/me", json={"locale": "cs"}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json().get("theme") == theme
    with Session(engine) as db:
        themes = dict(db.execute(select(User.email, User.theme)).all())
    assert themes.get("owner@example.com") == theme
    assert themes.get("other@example.com") == "system"
    login(client)
    assert client.get("/api/v1/auth/me").json().get("theme") == theme


@pytest.mark.parametrize("theme", ["sepia", "", None])
def test_theme_rejects_invalid_values(client: TestClient, theme: str | None) -> None:
    login(client)
    response = client.patch("/api/v1/auth/me", json={"theme": "dark"}, headers=ORIGIN)
    assert response.status_code == 200
    response = client.patch("/api/v1/auth/me", json={"theme": theme}, headers=ORIGIN)
    assert response.status_code == 422
    assert client.get("/api/v1/auth/me").json().get("theme") == "dark"


def test_dashboard_period_defaults_to_last_12_months(client: TestClient, engine: Engine) -> None:
    login(client)
    assert client.get("/api/v1/auth/me").json().get("dashboard_period") == "12m"
    response = client.patch("/api/v1/auth/me", json={"dashboard_period": "30d"}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json().get("dashboard_period") == "30d"
    with Session(engine) as db:
        periods = dict(db.execute(select(User.email, User.dashboard_period)).all())
    assert periods.get("owner@example.com") == "30d"
    assert periods.get("other@example.com") == "12m"


def test_dashboard_category_slices_default_and_bounds(client: TestClient, engine: Engine) -> None:
    login(client)
    assert client.get("/api/v1/auth/me").json().get("dashboard_category_slices") == 9
    for invalid in [2, 10, "many"]:
        response = client.patch(
            "/api/v1/auth/me", json={"dashboard_category_slices": invalid}, headers=ORIGIN
        )
        assert response.status_code == 422
    response = client.patch(
        "/api/v1/auth/me", json={"dashboard_category_slices": 5}, headers=ORIGIN
    )
    assert response.json().get("dashboard_category_slices") == 5
    with Session(engine) as db:
        slices = dict(db.execute(select(User.email, User.dashboard_category_slices)).all())
    assert slices.get("owner@example.com") == 5
    assert slices.get("other@example.com") == 9


def test_default_sync_interval_presets_new_and_existing_connections(
    client: TestClient, engine: Engine
) -> None:
    login(client)
    assert client.get("/api/v1/auth/me").json().get("default_sync_interval_minutes") == 360
    path = "/api/v1/providers/lidl/connections"
    preset = client.post(path, json={"name": "Preset"}, headers=ORIGIN).json()
    assert preset.get("sync_interval_minutes") == 360
    manual = client.post(
        path, json={"name": "Manual", "sync_interval_minutes": None}, headers=ORIGIN
    ).json()
    assert manual.get("sync_interval_minutes") is None
    for invalid in [59, 10081, "hourly"]:
        response = client.patch(
            "/api/v1/auth/me", json={"default_sync_interval_minutes": invalid}, headers=ORIGIN
        )
        assert response.status_code == 422
    response = client.patch(
        "/api/v1/auth/me", json={"default_sync_interval_minutes": 60}, headers=ORIGIN
    )
    assert response.json().get("default_sync_interval_minutes") == 60
    response = client.patch("/api/v1/auth/me", json={"locale": "cs"}, headers=ORIGIN)
    assert response.json().get("default_sync_interval_minutes") == 60
    assert client.post("/api/v1/connections/apply-default-sync").status_code == 403
    response = client.post("/api/v1/connections/apply-default-sync", headers=ORIGIN)
    assert response.status_code == 204
    owner_id = UUID(client.get("/api/v1/auth/me").json().get("id"))
    query = select(ProviderConnection.sync_interval_minutes, ProviderConnection.next_sync_at)
    owned = query.where(
        ProviderConnection.owner_id == owner_id, ProviderConnection.provider != MANUAL_PROVIDER
    )
    foreign = query.where(ProviderConnection.owner_id != owner_id).order_by(ProviderConnection.id)
    with Session(engine) as db:
        rows = db.execute(owned).all()
        others = db.execute(foreign).all()
    assert len(rows) >= 2
    assert all(interval == 60 and next_sync is not None for interval, next_sync in rows)
    response = client.patch(
        "/api/v1/auth/me", json={"default_sync_interval_minutes": None}, headers=ORIGIN
    )
    assert response.json().get("default_sync_interval_minutes") is None
    later = client.post(path, json={"name": "Later"}, headers=ORIGIN).json()
    assert later.get("sync_interval_minutes") is None
    client.post("/api/v1/connections/apply-default-sync", headers=ORIGIN)
    with Session(engine) as db:
        assert all(row == (None, None) for row in db.execute(owned))
        assert db.execute(foreign).all() == others


@pytest.mark.parametrize("period", ["custom", "week", None])
def test_dashboard_period_rejects_non_presets(client: TestClient, period: str | None) -> None:
    login(client)
    response = client.patch("/api/v1/auth/me", json={"dashboard_period": period}, headers=ORIGIN)
    assert response.status_code == 422
    assert client.get("/api/v1/auth/me").json().get("dashboard_period") == "12m"
