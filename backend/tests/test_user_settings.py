import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_api import client as client
from test_api import engine as engine
from test_api import login

from cartalytix.models import User

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
