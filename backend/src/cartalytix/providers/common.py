"""Types shared by store adapters and the provider-neutral sync engine."""

import re
from typing import Any

import httpx

_AUTH_PHASES = frozenset(
    {"login", "login_page", "login_verify", "refresh", "save_refresh", "history", "detail"}
)
_AUTH_REASONS = frozenset(
    {
        "invalid_grant",
        "invalid_token",
        "invalid_refresh_token",
        "invalid_client",
        "token_expired",
        "token_revoked",
        "refresh_token_expired",
        "refresh_token_revoked",
        "refresh_token_reused",
        "unauthorized",
        "forbidden",
        "invalid_request",
        "access_denied",
        "invalid_credentials",
        "csrf_token_invalid",
        "invalid_csrf_token",
        "csrf_token_missing",
        "csrf_validation_failed",
        "verification_required",
        "session_not_authenticated",
        "persistence_failed",
        "unrecognized_provider_error",
        "invalid_provider_response",
    }
)
_AUTH_TRANSPORTS = frozenset(
    {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "CloseError",
        "LocalProtocolError",
        "RemoteProtocolError",
        "ProxyError",
        "UnsupportedProtocol",
        "DecodingError",
        "TooManyRedirects",
        "HTTPError",
    }
)
_AUTH_DETAILS = {
    "csrf_validation_failed": (
        r"\b(?:csrf token (?:validation |check )?failed|invalid csrf token|csrf token mismatch)\b"
    ),
    "token_expired": r"\b(?:refresh |access )?token (?:has |is )?expired\b",
    "token_revoked": r"\b(?:refresh |access )?token (?:has been |was |is )?revoked\b",
    "token_reused": r"\b(?:refresh )?token (?:has been |was |is )?(?:reused|already used)\b",
}


def safe_auth_diagnostic(value: object) -> dict[str, Any]:
    """Accept fixed classifications only, including when exporting existing database rows."""
    if not isinstance(value, dict):
        return {}
    phase = value.get("phase")
    if not isinstance(phase, str) or phase not in _AUTH_PHASES:
        return {}
    safe: dict[str, Any] = {"phase": phase}
    status = value.get("status")
    if type(status) is int and 100 <= status <= 599:
        safe["status"] = status
    for key, allowed in (
        ("reason", _AUTH_REASONS),
        ("transport", _AUTH_TRANSPORTS),
        ("detail", _AUTH_DETAILS),
    ):
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            safe[key] = item
    if type(value.get("csrf_present")) is bool:
        safe["csrf_present"] = value.get("csrf_present")
    return safe


def auth_diagnostic(
    phase: str,
    *,
    response: httpx.Response | None = None,
    error: httpx.HTTPError | None = None,
    csrf_present: bool | None = None,
) -> dict[str, Any]:
    """Classify an auth response without retaining arbitrary codes, messages or headers."""
    diagnostic: dict[str, Any] = {"phase": phase}
    if csrf_present is not None:
        diagnostic["csrf_present"] = csrf_present
    if error is not None:
        name = type(error).__name__
        diagnostic["transport"] = name if name in _AUTH_TRANSPORTS else "HTTPError"
    if response is not None:
        diagnostic["status"] = response.status_code
        if len(response.content) <= 65536:
            try:
                payload = response.json()
            except ValueError, RecursionError:
                payload = None
            message = ""
            if isinstance(payload, dict):
                reason = payload.get("error") or payload.get("error_code") or payload.get("code")
                if isinstance(reason, dict):
                    reason = reason.get("code")
                if reason is not None:
                    diagnostic["reason"] = (
                        reason
                        if isinstance(reason, str) and reason in _AUTH_REASONS
                        else "unrecognized_provider_error"
                    )
                message = payload.get("error_description") or payload.get("message") or ""
            elif payload is None:
                message = response.text
            if response.status_code >= 400 and isinstance(message, str):
                for detail, pattern in _AUTH_DETAILS.items():
                    if re.search(pattern, message, re.IGNORECASE):
                        diagnostic["detail"] = detail
                        break
    return safe_auth_diagnostic(diagnostic)


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, diagnostic: dict[str, Any] | None = None) -> None:
        self.code = code
        self.diagnostic = safe_auth_diagnostic(diagnostic)
        super().__init__(code)


class ProviderPayload(dict[str, Any]):
    """Decoded data with the original JSON entity for lossless source archiving."""

    def __init__(self, payload: dict[str, Any], raw: bytes) -> None:
        super().__init__(payload)
        self.raw = raw
