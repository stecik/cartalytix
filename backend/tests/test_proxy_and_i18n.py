import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from cartalytix.i18n import catalog, translate
from cartalytix.main import create_app
from cartalytix.models import Base
from cartalytix.settings import Settings


class RecordingLimiter:
    def __init__(self) -> None:
        self.addresses: list[str] = []

    def check(self, address: str, email: str) -> bool:
        self.addresses.append(address)
        return True


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        ("192.0.2.10", ["198.51.100.1", "198.51.100.2"]),
        ("192.0.2.11", ["192.0.2.11", "192.0.2.11"]),
    ],
)
def test_only_trusted_proxy_can_set_login_client_address(peer: str, expected: list[str]) -> None:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    limiter = RecordingLimiter()
    config = Settings(
        environment="test",
        cookie_secure=False,
        allowed_origins=["http://testserver"],
        trusted_proxy_hosts=["192.0.2.10"],
        bootstrap_password=SecretStr("Proxy-test-only!"),
    )
    try:
        app = create_app(config, engine=engine, limiter=limiter)
        with TestClient(app, client=(peer, 50000)) as client:
            for address in ["198.51.100.1", "198.51.100.2"]:
                response = client.post(
                    "/api/v1/auth/login",
                    json={"email": "missing@example.com", "password": "incorrect password"},
                    headers={"Origin": "http://testserver", "X-Forwarded-For": address},
                )
                assert response.status_code == 401
        assert limiter.addresses == expected
    finally:
        engine.dispose()


def test_proxy_trust_cannot_cover_every_address() -> None:
    for value in ["*", "0.0.0.0/0", "::/0"]:
        with pytest.raises(ValueError):
            Settings(trusted_proxy_hosts=[value])


def test_gettext_catalogs_load_from_versioned_sources() -> None:
    catalog.cache_clear()
    assert translate("Authentication required", "cs-CZ") == "Je vyžadováno přihlášení"
    assert translate("Authentication required", "en") == "Authentication required"
    assert translate("Unknown source text", "cs") == "Unknown source text"
