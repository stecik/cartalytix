"""Private Node transport and validated, serializable Foodora credentials."""

import base64
import binascii
import json
import math
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from cartalytix.providers.common import ProviderError

TRANSPORT = Path(__file__).with_name("node_session.mjs")
ERROR_CODES = frozenset(
    {
        "input_invalid",
        "token_invalid",
        "stored_session_invalid",
        "account_mismatch",
        "network_error",
        "redirect_rejected",
        "rate_limited",
        "provider_unavailable",
        "provider_response_invalid",
        "challenge_required",
        "credentials_rejected",
        "auth_required",
        "refresh_rejected",
        "refresh_credentials_missing",
        "account_not_found",
        "password_login_unavailable",
        "login_identity_unavailable",
        "otp_rejected",
    }
)
METHODS = frozenset({"EMAIL", "SMS", "CALL"})


class FoodoraError(ProviderError):
    """A fixed diagnostic code, never a provider response or subprocess output."""


def valid_credential(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,16384}", value) is not None
    )


def token_identity(access_token: str) -> tuple[str, float]:
    parts = access_token.split(".")
    if len(parts) != 3 or not re.fullmatch(r"[A-Za-z0-9_-]+", parts[1]):
        raise FoodoraError("stored_session_invalid")
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError, UnicodeError, binascii.Error:
        raise FoodoraError("stored_session_invalid") from None
    if not isinstance(claims, dict):
        raise FoodoraError("stored_session_invalid")
    subject = claims.get("user_id")
    expires = claims.get("expires")
    if (
        isinstance(subject, bool)
        or not isinstance(subject, str | int)
        or not str(subject).strip()
        or len(str(subject)) > 256
        or (isinstance(subject, int) and abs(subject) > 2**53 - 1)
        or isinstance(expires, bool)
        or not isinstance(expires, int | float)
        or not math.isfinite(expires)
        or expires <= 0
    ):
        raise FoodoraError("stored_session_invalid")
    return str(subject), float(expires / 1000 if expires > 1e12 else expires)


@dataclass(frozen=True, repr=False)
class FoodoraSession:
    access_token: str
    refresh_token: str
    device_token: str
    subject: str
    expires: float

    @classmethod
    def from_payload(cls, value: object) -> FoodoraSession:
        if not isinstance(value, dict) or not all(
            valid_credential(value.get(name))
            for name in ("access_token", "refresh_token", "device_token")
        ):
            raise FoodoraError("stored_session_invalid")
        access = value.get("access_token")
        refresh = value.get("refresh_token")
        device = value.get("device_token")
        if (
            not isinstance(access, str)
            or not isinstance(refresh, str)
            or not isinstance(device, str)
        ):
            raise FoodoraError("stored_session_invalid")
        subject, expires = token_identity(access)
        if subject != value.get("subject"):
            raise FoodoraError("account_mismatch")
        stored_expiry = value.get("expires")
        if (
            isinstance(stored_expiry, bool)
            or not isinstance(stored_expiry, int | float)
            or not math.isfinite(stored_expiry)
            or stored_expiry != expires
        ):
            raise FoodoraError("stored_session_invalid")
        return cls(access, refresh, device, subject, expires)

    def serialize(self) -> str:
        return json.dumps(asdict(self), allow_nan=False, separators=(",", ":"))


def execute(payload: dict[str, Any]) -> dict[str, Any]:
    """Send credentials only over private pipes and expose fixed failure codes."""
    node = shutil.which("node")
    if node is None:
        raise FoodoraError("provider_unavailable")
    try:
        result = subprocess.run(
            [node, str(TRANSPORT)],
            input=json.dumps(payload, allow_nan=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=100,
            check=False,
        )
    except subprocess.TimeoutExpired, OSError, UnicodeError:
        raise FoodoraError("provider_unavailable") from None
    try:
        if len(result.stdout) > 100000:
            raise ValueError
        data = json.loads(result.stdout)
    except ValueError:
        raise FoodoraError("provider_response_invalid") from None
    if not isinstance(data, dict):
        raise FoodoraError("provider_response_invalid")
    error = data.get("error")
    if error or result.returncode:
        error = "account_mismatch" if error == "account_identity_mismatch" else error
        raise FoodoraError(
            error if isinstance(error, str) and error in ERROR_CODES else "provider_unavailable"
        )
    if data.get("status") == "otp_required":
        methods = data.get("methods")
        cookies = data.get("cookies")
        if (
            not isinstance(methods, list)
            or not methods
            or any(not isinstance(method, str) or method not in METHODS for method in methods)
            or not isinstance(cookies, dict)
            or any(
                name not in {"token", "refresh_token", "device_token"}
                or not valid_credential(value)
                for name, value in cookies.items()
            )
        ):
            raise FoodoraError("provider_response_invalid")
        return {"status": "otp_required", "methods": methods, "cookies": cookies}
    session = FoodoraSession.from_payload(data.get("session"))
    report = data.get("report")
    if not isinstance(report, dict) or report.get("authenticated") is not True:
        raise FoodoraError("provider_response_invalid")
    kept: dict[str, Any] = {"authenticated": True}
    for name in (
        "refresh_available",
        "refresh_verified",
        "refresh_attempted",
        "long_term_refresh_verified",
        "access_rotated",
        "refresh_rotated",
        "device_rotated",
    ):
        if name in report:
            if not isinstance(report.get(name), bool):
                raise FoodoraError("provider_response_invalid")
            kept[name] = report.get(name)
    if "expires" in report:
        if isinstance(report.get("expires"), bool) or report.get("expires") != session.expires:
            raise FoodoraError("provider_response_invalid")
        kept["expires"] = session.expires
    if "refresh_http_status" in report:
        if report.get("refresh_http_status") != 200:
            raise FoodoraError("provider_response_invalid")
        kept["refresh_http_status"] = 200
    return {"session": asdict(session), "report": kept}
