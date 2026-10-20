"""Production Foodora session validation and private transport contract."""

import base64
import json
import subprocess
from typing import Any

import pytest

from cartalytix.providers.foodora import session


def payload(subject: str = "42", expires: int = 4102444800) -> dict[str, Any]:
    encoded = (
        base64.urlsafe_b64encode(json.dumps({"user_id": subject, "expires": expires}).encode())
        .decode()
        .rstrip("=")
    )
    return {
        "access_token": f"header.{encoded}.signature",
        "refresh_token": "private-refresh",
        "device_token": "private-device",
        "subject": subject,
        "expires": expires,
    }


def test_session_roundtrips_only_credentials_and_hides_repr() -> None:
    value = session.FoodoraSession.from_payload({**payload(), "password": "private-password"})
    assert json.loads(value.serialize()) == payload()
    assert "private-" not in repr(value)
    assert value.subject == "42"


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"subject": "99"}, "account_mismatch"),
        ({"access_token": "bad"}, "stored_session_invalid"),
        ({"refresh_token": "newline\nsecret"}, "stored_session_invalid"),
        ({"device_token": ""}, "stored_session_invalid"),
        ({"expires": True}, "stored_session_invalid"),
        ({"expires": 1}, "stored_session_invalid"),
    ],
)
def test_session_rejects_inconsistent_or_unsafe_credentials(
    patch: dict[str, Any], code: str
) -> None:
    with pytest.raises(session.FoodoraError, match=f"^{code}$"):
        session.FoodoraSession.from_payload({**payload(), **patch})


def test_bridge_uses_stdin_and_sanitizes_untrusted_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def child(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend(args)
        assert "private-password" in str(kwargs.get("input", ""))
        assert kwargs.get("capture_output") is True
        return subprocess.CompletedProcess(args, 1, '{"error":"private-secret"}', "private-stderr")

    monkeypatch.setattr(session.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(session.subprocess, "run", child)
    with pytest.raises(session.FoodoraError, match="^provider_unavailable$"):
        session.execute({"command": "login", "password": "private-password"})
    assert "private-password" not in " ".join(seen)


def test_bridge_maps_foreign_identity_without_provider_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, '{"error":"account_identity_mismatch"}', "")

    monkeypatch.setattr(session.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(session.subprocess, "run", child)
    with pytest.raises(session.FoodoraError, match="^account_mismatch$"):
        session.execute({"command": "refresh", "session": payload()})


def test_bridge_keeps_only_validated_report_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def child(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                {
                    "session": {**payload(), "password": "private-password"},
                    "report": {
                        "authenticated": True,
                        "refresh_verified": True,
                        "expires": 4102444800,
                        "password": "private-password",
                    },
                }
            ),
            "",
        )

    monkeypatch.setattr(session.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(session.subprocess, "run", child)
    result = session.execute({"command": "refresh", "session": payload()})
    assert "private-password" not in json.dumps(result)
    assert result.get("report") == {
        "authenticated": True,
        "refresh_verified": True,
        "expires": 4102444800,
    }


def test_bridge_timeout_does_not_expose_private_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    def child(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 100, "private-stdout", "private-stderr")

    monkeypatch.setattr(session.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(session.subprocess, "run", child)
    with pytest.raises(session.FoodoraError, match="^provider_unavailable$") as error:
        session.execute({"command": "login", "password": "private-password"})
    assert "private" not in str(error.value)
