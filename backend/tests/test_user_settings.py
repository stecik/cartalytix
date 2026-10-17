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
