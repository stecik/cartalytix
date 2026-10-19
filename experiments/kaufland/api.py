"""cidaas OAuth2 (account.kaufland.com) and the Kaufland Card app transaction API."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from cartalytix.export import ExportError

# Public identifiers of the Kaufland mobile app, not secrets.
AUTH = "https://account.kaufland.com"
API = "https://p.crm-dynamics.schwarz"
CLIENT_ID = "fb1b425b-ab2f-4140-aef9-20263b6cfa49"
LOYALTY_CLIENT_ID = "88207bfc-780b-400d-92ee-893ae72dab40"
REDIRECT = "com.kaufland.kaufland://oauth/callback"
OAUTH_VERSION = "1.5.22"
APP_VERSION = "6.17.1"
COUNTRY = "CZ"
PAGE_SIZE = 50


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def authorization_url(challenge: str, state: str) -> str:
    return (
        AUTH
        + "/authz-srv/authz?"
        + urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT,
                "ui_locales": "cs-CZ",
                "v": OAUTH_VERSION,
                "view_type": "login",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )


def callback_code(url: str, expected_state: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc, parsed.path) != (
        "com.kaufland.kaufland",
        "oauth",
        "/callback",
    ):
        raise ValueError("Unexpected OAuth redirect")
    params = parse_qs(parsed.query)
    if len(params.get("state", [])) != 1 or not hmac.compare_digest(
        params["state"][0], expected_state
    ):
        raise ValueError("OAuth state mismatch")
    if "error" in params or len(params.get("code", [])) != 1:
        raise ValueError("OAuth authorization failed")
    return params["code"][0]


def token_request(http: httpx.Client, form: dict[str, str]) -> dict[str, Any]:
    """Single POST; a rotating refresh is never retried because a lost response may consume it."""
    try:
        response = http.post(
            AUTH + "/token-srv/token",
            data={**form, "client_id": CLIENT_ID, "v": OAUTH_VERSION},
            headers={"Accept": "application/json"},
        )
    except httpx.HTTPError:
        raise ExportError(
            "Token request failed; run login again if it repeats"
        ) from None
    if response.status_code != 200:
        raise ExportError(f"Token HTTP {response.status_code}; no response body logged")
    try:
        data = response.json()
    except ValueError:
        raise ExportError("Token response was not JSON") from None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ExportError("Token response without access token")
    return data


def subject(http: httpx.Client, access_token: str) -> str:
    try:
        response = http.get(
            AUTH + "/users-srv/userinfo",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
            },
        )
    except httpx.HTTPError:
        raise ExportError("Userinfo request failed") from None
    data = response.json() if response.status_code == 200 else None
    sub = data.get("sub") if isinstance(data, dict) else None
    if not isinstance(sub, str) or not sub:
        raise ExportError(f"Userinfo HTTP {response.status_code}; no customer id")
    return sub


def renew(
    http: httpx.Client, refresh_token: str, save_refresh: Callable[[str], None]
) -> str:
    """Return a fresh access token; a rotated refresh token is persisted before returning."""
    data = token_request(
        http, {"grant_type": "refresh_token", "refresh_token": refresh_token}
    )
    rotated = data.get("refresh_token")
    if isinstance(rotated, str) and rotated and rotated != refresh_token:
        save_refresh(rotated)
    return str(data["access_token"])


def transactions_url(customer: str) -> str:
    return f"{API}/api/v2/customers/{quote(customer, safe='')}/transactions"


def app_headers(access_token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "client-id": LOYALTY_CLIENT_ID,
        "app-platform": "Android",
        "app-version": APP_VERSION,
    }
