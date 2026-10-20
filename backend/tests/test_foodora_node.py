"""Node transport contract with synthetic cookies; no real credentials or requests."""

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

MODULE = Path(__file__).parents[1] / "src/cartalytix/providers/foodora/node_session.mjs"


def token(subject: object = "42", expires: int = 4102444800) -> str:
    payload = json.dumps({"user_id": subject, "expires": expires})
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def stored() -> dict[str, Any]:
    return {
        "access_token": token(),
        "refresh_token": "old-refresh",
        "device_token": "old-device",
        "subject": "42",
        "expires": 4102444800,
    }


def response(
    status: int = 200,
    data: object = None,
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "data": data,
        "cookies": cookies or {},
        "headers": headers or {},
    }


def invoke(payload: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the provider"
    script = """
const {execute} = await import(process.argv[1]);
const input = JSON.parse(await new Promise(resolve => {
  let data = ''; process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => data += chunk);
  process.stdin.on('end', () => resolve(data));
}));
const calls = [];
const fetcher = async (url, options) => {
  calls.push({url, ...options, signal: undefined});
  const row = input.responses.shift();
  if (!row) throw new Error('Unexpected provider request');
  const headers = new Headers({'content-type': 'application/json', ...row.headers});
  for (const [name,value] of Object.entries(row.cookies)) {
    headers.append('set-cookie', `${name}=${encodeURIComponent(value)}; Secure; Path=/; HttpOnly`);
  }
  return new Response(JSON.stringify(row.data), {status:row.status, headers});
};
try {
  const result = await execute(input.payload, fetcher);
  console.log(JSON.stringify({result,calls,remaining:input.responses.length}));
} catch (error) {
  console.log(JSON.stringify({error:error.code,calls,remaining:input.responses.length}));
}
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script, MODULE.as_uri()],
        input=json.dumps({"payload": payload, "responses": responses}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, "Provider transport must run without exposing provider errors"
    return json.loads(completed.stdout)


def login_responses() -> list[dict[str, Any]]:
    return [
        response(data={}),
        response(data={"is_known": True, "has_password": True}),
        response(
            data={"user_id": 42},
            cookies={
                "token": token(),
                "refresh_token": "old-refresh",
                "device_token": "old-device",
            },
        ),
        response(data={"data": {"items": []}}),
    ]


def test_login_checks_email_and_verifies_token_against_history() -> None:
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        login_responses(),
    )
    assert result.get("remaining") == 0
    outcome = result.get("result", {})
    assert outcome.get("session") == stored()
    report = outcome.get("report", {})
    assert report.get("authenticated") is True
    assert report.get("long_term_refresh_verified") is False
    assert "old-refresh" not in json.dumps(report)
    assert "private-password" not in json.dumps(outcome)
    calls = result.get("calls", [])
    assert [row.get("url", "").split("?")[0] for row in calls] == [
        "https://www.foodora.cz/login/new",
        "https://www.foodora.cz/login/new/api/email-check",
        "https://www.foodora.cz/login/new/api/login",
        "https://cz.fd-api.com/api/v5/orders/order_history",
    ]
    assert all(row.get("redirect") == "manual" for row in calls)
    assert calls[-1].get("headers", {}).get("Authorization") == f"Bearer {token()}"
    assert "Cookie" not in calls[-1].get("headers", {})


def test_login_metadata_does_not_override_api_verified_token_identity() -> None:
    rows = login_responses()
    rows[2]["data"] = {"user_id": "web-identifier", "code": "web-customer-code"}
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("result", {}).get("session", {}).get("subject") == "42"
    assert result.get("remaining") == 0
    assert (
        result.get("calls", [])[-1].get("headers", {}).get("Authorization") == f"Bearer {token()}"
    )


def test_login_cannot_validate_with_access_token_left_from_before_authentication() -> None:
    rows = login_responses()
    rows[0]["cookies"] = rows[2].get("cookies", {})
    rows[2]["cookies"] = {}
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("error") == "refresh_credentials_missing"
    assert "result" not in result


def test_otp_login_cannot_validate_with_stale_access_token() -> None:
    rows = [response(data={"user_id": "42"}), response(data={"data": {"items": []}})]
    result = invoke(
        {
            "command": "otp",
            "username": "test@example.invalid",
            "password": "private-password",
            "method": "EMAIL",
            "otp": "123456",
            "cookies": {
                "token": token(),
                "refresh_token": "old-refresh",
                "device_token": "old-device",
            },
        },
        rows,
    )
    assert result.get("error") == "refresh_credentials_missing"
    assert "result" not in result


def test_refresh_saves_rotations_only_after_same_identity_is_verified() -> None:
    new_token = token(expires=4102444900)
    result = invoke(
        {"command": "refresh", "session": stored()},
        [
            response(
                data={"expires_in": 4102444900},
                cookies={"token": new_token, "refresh_token": "new-refresh"},
            ),
            response(data={"data": {"items": []}}),
        ],
    )
    outcome = result.get("result", {})
    assert outcome.get("session", {}).get("access_token") == new_token
    assert outcome.get("session", {}).get("refresh_token") == "new-refresh"
    assert outcome.get("session", {}).get("device_token") == "old-device"
    assert outcome.get("report", {}).get("refresh_verified") is True
    assert outcome.get("report", {}).get("long_term_refresh_verified") is False
    body = json.loads(result.get("calls", [])[0].get("body", "{}"))
    assert body == {
        "country": "cz",
        "platform": "b2c",
        "device_token": "old-device",
        "refresh_token": "old-refresh",
    }


@pytest.mark.parametrize("subject", ["99", "", True])
def test_refresh_rejects_foreign_or_missing_customer(subject: object) -> None:
    changed = token(subject=subject)
    result = invoke(
        {"command": "refresh", "session": stored()},
        [
            response(data={}, cookies={"token": changed}),
            response(data={"data": {"items": []}}),
        ],
    )
    assert result.get("error") in {"account_identity_mismatch", "token_invalid"}
    assert "result" not in result


@pytest.mark.parametrize("status", [302, 307, 308])
def test_login_redirect_cannot_forward_password(status: int) -> None:
    rows = login_responses()[:2] + [response(status, headers={"location": "https://evil.example/"})]
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("error") == "redirect_rejected"
    assert len(result.get("calls", [])) == 3
    assert "result" not in result


@pytest.mark.parametrize(
    "status,data,code",
    [
        (
            403,
            {"appId": "PXtest", "private": "private-provider-secret"},
            "challenge_required",
        ),
        (429, {"private": "private-provider-secret"}, "rate_limited"),
        (401, {"private": "private-provider-secret"}, "credentials_rejected"),
        (403, {}, "credentials_rejected"),
        (500, {"private": "private-provider-secret"}, "provider_unavailable"),
    ],
)
def test_login_errors_never_expose_provider_response(
    status: int, data: dict[str, str], code: str
) -> None:
    rows = login_responses()[:2] + [response(status, data)]
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("error") == code
    assert "private-provider-secret" not in json.dumps(result)


def test_otp_uses_documented_headers_without_persisting_password_or_code() -> None:
    result = invoke(
        {
            "command": "otp",
            "username": "test@example.invalid",
            "password": "private-password",
            "method": "EMAIL",
            "otp": "123456",
            "cookies": {"device_token": "old-device"},
        },
        login_responses()[2:],
    )
    assert result.get("result", {}).get("report", {}).get("authenticated") is True
    headers = result.get("calls", [])[0].get("headers", {})
    assert headers.get("X-OTP-Method") == "EMAIL"
    assert headers.get("X-OTP") == "123456"
    assert headers.get("X-Device") == "old-device"
    assert "123456" not in json.dumps(result.get("result"))


def test_otp_challenge_returns_only_allowed_methods() -> None:
    rows = login_responses()[:2] + [
        response(
            400,
            {"data": {"exception_type": "invalid_otp_method"}},
            headers={"x-otp-method": "EMAIL,SMS,private-provider-secret"},
        )
    ]
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("result", {}).get("methods") == ["EMAIL", "SMS"]
    assert "private-provider-secret" not in json.dumps(result.get("result"))


def test_expired_status_refreshes_once_then_verifies_history() -> None:
    expired = {**stored(), "access_token": token(expires=1), "expires": 1}
    result = invoke(
        {"command": "status", "session": expired},
        [
            response(data={}, cookies={"token": token()}),
            response(data={"data": {"items": []}}),
        ],
    )
    assert result.get("result", {}).get("report", {}).get("refresh_verified") is True
    assert len(result.get("calls", [])) == 2


def test_http_200_refresh_error_is_not_reported_as_verified() -> None:
    result = invoke(
        {"command": "refresh", "session": stored()},
        [
            response(
                data={
                    "status_code": 400,
                    "data": {"exception_type": "invalid_refresh_token"},
                }
            ),
            response(data={"data": {"items": []}}),
        ],
    )
    assert result.get("error") == "refresh_rejected"
    assert "result" not in result


def test_unchanged_live_token_does_not_prove_refresh() -> None:
    result = invoke(
        {"command": "refresh", "session": stored()},
        [
            response(data={}),
            response(data={"data": {"items": []}}),
        ],
    )
    report = result.get("result", {}).get("report", {})
    assert report.get("refresh_verified") is False
    assert report.get("refresh_attempted") is True
    assert report.get("refresh_http_status") == 200
    assert report.get("access_rotated") is False


def test_successful_http_without_required_refresh_credentials_is_rejected() -> None:
    rows = login_responses()
    rows[2].get("cookies", {}).pop("device_token")
    result = invoke(
        {
            "command": "login",
            "username": "test@example.invalid",
            "password": "private-password",
        },
        rows,
    )
    assert result.get("error") == "refresh_credentials_missing"


def test_refresh_perimeter_challenge_does_not_invalidate_account() -> None:
    result = invoke(
        {"command": "refresh", "session": stored()},
        [response(403, {"appId": "PXtest", "blockScript": "private-provider-secret"})],
    )
    assert result.get("error") == "challenge_required"
    assert "private-provider-secret" not in json.dumps(result)


def test_history_403_does_not_become_refresh_rejection() -> None:
    result = invoke(
        {"command": "refresh", "session": stored()},
        [response(data={}), response(403, {})],
    )
    assert result.get("error") == "challenge_required"


def test_refresh_identity_mismatch_stops_before_history() -> None:
    result = invoke(
        {"command": "refresh", "session": stored()},
        [response(data={}, cookies={"token": token(subject="99")})],
    )
    assert result.get("error") == "account_identity_mismatch"
    assert len(result.get("calls", [])) == 1
