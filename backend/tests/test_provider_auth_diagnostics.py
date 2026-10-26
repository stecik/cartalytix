"""Authentication diagnostics accept fixed metadata, never provider-controlled secrets."""

import json

import httpx
import pytest

from cartalytix.providers.common import ProviderError, auth_diagnostic, safe_auth_diagnostic


@pytest.mark.parametrize("field", ["error", "error_code", "code"])
def test_auth_response_extracts_only_known_reason(field: str) -> None:
    response = httpx.Response(
        401,
        json={field: "invalid_grant", "error_description": "private-token", "token": "private"},
    )
    assert auth_diagnostic("refresh", response=response) == {
        "phase": "refresh",
        "status": 401,
        "reason": "invalid_grant",
    }


def test_auth_response_drops_unknown_codes_and_oversize_bodies() -> None:
    response = httpx.Response(401, json={"error": "private-token"})
    assert auth_diagnostic("refresh", response=response) == {
        "phase": "refresh",
        "status": 401,
        "reason": "unrecognized_provider_error",
    }
    huge = httpx.Response(403, content=b"private-" * 10000)
    assert auth_diagnostic("login", response=huge, csrf_present=True) == {
        "phase": "login",
        "status": 403,
        "csrf_present": True,
    }


def test_auth_diagnostic_records_transport_type_without_message_or_url() -> None:
    error = httpx.ReadTimeout("private-token", request=httpx.Request("POST", "https://secret.test"))
    assert auth_diagnostic("refresh", error=error) == {
        "phase": "refresh",
        "transport": "ReadTimeout",
    }


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(403, text="<h1>CSRF token validation failed</h1>private-token"),
            {"phase": "login", "status": 403, "detail": "csrf_validation_failed"},
        ),
        (
            httpx.Response(
                401,
                json={
                    "error": "invalid_grant",
                    "error_description": "Refresh token has expired: private-token",
                },
            ),
            {"phase": "login", "status": 401, "reason": "invalid_grant", "detail": "token_expired"},
        ),
        (httpx.Response(401, content=b"[" * 3000 + b"]" * 3000), {"phase": "login", "status": 401}),
    ],
)
def test_auth_response_classifies_known_details_without_copying_messages(
    response: httpx.Response, expected: dict[str, object]
) -> None:
    assert auth_diagnostic("login", response=response) == expected


def test_auth_metadata_is_allowlisted_before_storage() -> None:
    diagnostic = {
        "phase": "refresh",
        "status": 401,
        "reason": "invalid_grant",
        "transport": "private-token",
        "password": "private-password",
        "body": "private-body",
        "csrf_present": True,
    }
    error = ProviderError("auth_required", diagnostic=diagnostic)
    assert error.diagnostic == {
        "phase": "refresh",
        "status": 401,
        "reason": "invalid_grant",
        "csrf_present": True,
    }
    assert "private" not in json.dumps(error.diagnostic)
    assert safe_auth_diagnostic({"phase": "private-phase", "status": 401}) == {}
    assert safe_auth_diagnostic({"phase": ["refresh"], "status": 401}) == {}
    assert safe_auth_diagnostic({"phase": "login", "status": True, "csrf_present": "private"}) == {
        "phase": "login"
    }
