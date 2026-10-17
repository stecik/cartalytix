"""Read-only Lidl CZ adapter with serialized, fail-closed refresh rotation."""

import base64
import binascii
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, NotRequired, TypedDict
from urllib.parse import quote

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload

AUTH = "https://accounts.lidl.com"
TICKETS = "https://tickets.lidlplus.com/api"
CLIENT_ID = "LidlPlusNativeClient"
APP_VERSION = "16.45.5"


class LidlError(ProviderError):
    pass


class LidlPayload(ProviderPayload):
    pass


class TokenData(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: float
    subject: NotRequired[str]


def _response_subject(payload: dict[str, Any]) -> str | None:
    # Only called on the fixed HTTPS token endpoint's response, never user input.
    for name in ("id_token", "access_token"):
        value = payload.get(name)
        if not isinstance(value, str):
            continue
        parts = value.split(".")
        if len(parts) != 3:
            continue
        try:
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        except ValueError, UnicodeError, binascii.Error:
            continue
        if isinstance(claims, dict):
            subject = claims.get("sub")
            if isinstance(subject, str) and subject:
                return subject
    return None


def exchange_refresh(
    refresh_token: str,
    *,
    http: httpx.Client | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> TokenData:
    if not refresh_token:
        raise LidlError("auth_required")
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    try:
        if heartbeat is not None:
            heartbeat()
        try:
            response = transport.post(
                f"{AUTH}/connect/token",
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                auth=(CLIENT_ID, "secret"),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise LidlError("auth_required") from None
        if response.status_code != 200:
            raise LidlError("auth_required")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            access = payload.get("access_token")
            rotated = payload.get("refresh_token", refresh_token)
            expires = payload.get("expires_in")
            if not isinstance(access, str) or not access:
                raise ValueError
            if not isinstance(rotated, str) or not rotated or isinstance(expires, bool):
                raise ValueError
            lifetime = float(expires)
            if not math.isfinite(lifetime) or lifetime <= 0:
                raise ValueError
        except ValueError, TypeError, OverflowError:
            raise LidlError("auth_required") from None
        result: TokenData = {
            "access_token": access,
            "refresh_token": rotated,
            "expires_at": time.time() + lifetime,
        }
        subject = _response_subject(payload)
        if subject is not None:
            result["subject"] = subject
        return result
    finally:
        if owned:
            transport.close()


def _retry_wait(value: str, fallback: float) -> float:
    if not value:
        return fallback
    try:
        wait = float(value)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=UTC)
            wait = max(0.0, (target - datetime.now(UTC)).total_seconds())
        except ValueError, TypeError, OverflowError:
            raise LidlError("rate_limited") from None
    if not math.isfinite(wait) or not 0 <= wait <= 60:
        raise LidlError("rate_limited")
    return wait


class LidlClient:
    def __init__(
        self,
        refresh_token: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 0.5,
        app_version: str = APP_VERSION,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        self._refresh_token = refresh_token
        self._tokens: TokenData | None = None
        self._save_refresh = save_refresh
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
        self._delay = delay
        self._app_version = app_version
        self._heartbeat = heartbeat
        self._auth_failed = False

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def renew(self) -> None:
        if self._auth_failed:
            raise LidlError("auth_required")
        # Once the POST might have happened, a failed result must never replay it.
        self._auth_failed = True
        updated = exchange_refresh(self._refresh_token, http=self._http, heartbeat=self._heartbeat)
        try:
            self._save_refresh(updated.get("refresh_token", ""))
        except Exception:
            raise LidlError("auth_required") from None
        self._refresh_token = updated.get("refresh_token", "")
        self._tokens = updated
        self._auth_failed = False

    def _get(
        self, path: str, params: dict[str, str | int] | None = None, *, language: str = "cs-CZ"
    ) -> dict[str, Any]:
        renewed = False
        for attempt in range(4):
            if self._auth_failed:
                raise LidlError("auth_required")
            if self._tokens is None or self._tokens.get("expires_at", 0) <= time.time() + 60:
                self.renew()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            access = self._tokens.get("access_token", "") if self._tokens else ""
            try:
                response = self._http.get(
                    f"{TICKETS}/{path}",
                    params=params,
                    headers={
                        "Authorization": "Bearer " + access,
                        "App-Version": self._app_version,
                        "Operating-System": "iOs",
                        "App": "com.lidl.eci.lidl.plus",
                        "Accept-Language": language,
                    },
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise LidlError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code == 401:
                if renewed:
                    self._auth_failed = True
                    raise LidlError("auth_required")
                self.renew()
                renewed = True
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                wait = _retry_wait(
                    response.headers.get("Retry-After", ""),
                    min(60, max(self._delay, 1) * (2**attempt)),
                )
                if attempt == 3:
                    raise LidlError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise LidlError("provider_unavailable")
            try:
                result = json.loads(response.content, parse_float=Decimal)
            except ValueError:
                raise LidlError("provider_response_invalid") from None
            if not isinstance(result, dict):
                raise LidlError("provider_response_invalid")
            return LidlPayload(result, response.content)
        raise LidlError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise LidlError("provider_response_invalid")
        return self._get("v2/CZ/tickets", {"pageNumber": number, "onlyFavorite": "false"})

    def detail(self, receipt_id: str) -> dict[str, Any]:
        if not receipt_id:
            raise LidlError("receipt_identity_mismatch")
        return self._get("v3/CZ/tickets/" + quote(receipt_id, safe=""), language="cs")
