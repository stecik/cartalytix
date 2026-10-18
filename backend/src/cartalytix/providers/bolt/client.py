"""Read-only Bolt Food adapter: SMS sign-in, rotating refresh token, paged order history."""

import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload

API = "https://deliveryuser.live.boltsvc.net"
# Web client version observed live on 2026-10-18; Bolt may reject very old versions.
VERSION = "FW.1.120"
PAGE_SIZE = 20


class BoltError(ProviderError):
    pass


@dataclass(frozen=True)
class BoltAccount:
    credential: str
    subject: str


def new_device() -> str:
    return str(uuid.uuid4())


def account_subject(phone: str) -> str:
    """Bolt identifies eaters by phone; only a hash is kept for the identity binding."""
    return hashlib.sha256(phone.encode()).hexdigest()


def _query(device_id: str, **extra: str) -> dict[str, str]:
    return {
        "version": VERSION,
        "language": "cs-CZ",
        "deviceId": device_id,
        "deviceType": "web",
        "device_name": "web",
        "device_os_version": "web",
        "session_id": device_id,
        "theme": "light",
        **extra,
    }


def _result(
    response: httpx.Response, rejected: str, *, verification: bool = False
) -> dict[str, Any]:
    if response.status_code == 429:
        raise BoltError("login_rate_limited")
    if response.status_code in {401, 403}:
        raise BoltError(rejected)
    if response.status_code != 200:
        raise BoltError("provider_unavailable")
    try:
        payload = json.loads(response.content, parse_float=Decimal)
    except ValueError:
        raise BoltError("provider_response_invalid") from None
    if not isinstance(payload, dict):
        raise BoltError("provider_response_invalid")
    data = payload.get("data")
    if verification and "challenge" in json.dumps(data, default=str):
        # Bot challenges are only solvable on Bolt's own pages; never bypassed here.
        raise BoltError("challenge_required")
    if payload.get("code") != 0 or not isinstance(data, dict):
        raise BoltError(rejected)
    return data


def _post(
    path: str, device_id: str, body: dict[str, Any], http: httpx.Client | None, rejected: str
) -> dict[str, Any]:
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            response = transport.post(
                f"{API}{path}",
                params=_query(device_id, country="cz"),
                json=body,
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise BoltError("provider_unavailable") from None
        return _result(response, rejected, verification=True)
    finally:
        if owned:
            transport.close()


def start_verification(phone: str, device_id: str, *, http: httpx.Client | None = None) -> None:
    """Ask Bolt to send an SMS code; the POST is never retried."""
    body = {
        "type": "phone",
        "method": "sms",
        "phone_number": phone,
        "phone_uuid": device_id,
        "last_known_state": {},
    }
    _post("/profile/verification/start", device_id, body, http, "phone_rejected")


def _find(value: object, key: str) -> object:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        value = list(value.values())
    if isinstance(value, list):
        for child in value:
            found = _find(child, key)
            if found is not None:
                return found
    return None


def confirm_verification(
    phone: str, device_id: str, code: str, *, http: httpx.Client | None = None
) -> BoltAccount:
    body = {
        "type": "phone",
        "phone_number": phone,
        "phone_uuid": device_id,
        "code": code,
        "last_known_state": {},
    }
    data = _post("/profile/verification/confirm", device_id, body, http, "otp_rejected")
    # The web app receives its tokens in this confirm response, not from a separate call.
    refresh = _find(data, "refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise BoltError("verification_incomplete")
    credential = json.dumps({"refresh_token": refresh, "device_id": device_id})
    return BoltAccount(credential=credential, subject=account_subject(phone))


class BoltClient:
    def __init__(
        self,
        credential: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        try:
            stored = json.loads(credential)
            self._refresh = str(stored.get("refresh_token", ""))
            self._device = str(stored.get("device_id", ""))
        except ValueError, AttributeError:
            raise BoltError("auth_required") from None
        self._save = save_refresh
        self._access: str | None = None
        self._expires_at = 0.0
        self._auth_failed = False
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
        self._delay = delay
        self._heartbeat = heartbeat
        self._tokens: dict[int, str] = {}
        self._cities: dict[str, object] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _send(
        self,
        method: str,
        path: str,
        token: str,
        params: dict[str, str],
        body: dict[str, object] | None = None,
    ) -> httpx.Response:
        for attempt in range(4):
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.request(
                    method,
                    f"{API}{path}",
                    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                    follow_redirects=False,
                    params=params,
                    json=body,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise BoltError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise BoltError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                # The web app itself hits 429 (code 1005) while paging; back off generously.
                time.sleep(min(60, max(self._delay, 2) * (2**attempt)))
                continue
            return response
        raise BoltError("provider_unavailable")

    def renew(self) -> None:
        if self._auth_failed or not self._refresh:
            raise BoltError("auth_required")
        # Once the rotating POST may have happened, a failed result must never replay it.
        self._auth_failed = True
        response = self._send(
            "POST",
            "/profile/eater/auth/getAccessTokenWithRotation",
            self._refresh,
            _query(self._device),
            {},
        )
        data = _result(response, "auth_required")
        access = _find(data, "access_token")
        if not isinstance(access, str) or not access:
            raise BoltError("auth_required")
        rotated = _find(data, "refresh_token")
        if isinstance(rotated, str) and rotated and rotated != self._refresh:
            try:
                self._save(json.dumps({"refresh_token": rotated, "device_id": self._device}))
            except Exception:
                raise BoltError("auth_required") from None
            self._refresh = rotated
        self._access = access
        # Access tokens are short-lived; renew well before Bolt rejects them.
        self._expires_at = time.time() + 240
        self._auth_failed = False

    def _get_data(
        self,
        method: str,
        path: str,
        params: dict[str, str],
        body: dict[str, object] | None = None,
    ) -> ProviderPayload:
        if self._access is None or self._expires_at <= time.time():
            self.renew()
        assert self._access is not None
        response = self._send(method, path, self._access, params, body)
        data = _result(response, "auth_required")
        # Callers get Bolt's inner data; the full original response is archived losslessly.
        return ProviderPayload(data, response.content)

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise BoltError("provider_response_invalid")
        params = _query(self._device, page_size=str(PAGE_SIZE))
        if number > 1:
            token = self._tokens.get(number)
            if not token:
                # Past the last page: the sync engine stops on an empty page.
                return ProviderPayload({"orders": []}, b'{"code":0,"data":{"orders":[]}}')
            params["page_token"] = token
        data = self._get_data("GET", "/deliveryClient/getOrderHistory", params)
        orders = data.get("orders")
        if isinstance(orders, list):
            for order in orders:
                if isinstance(order, dict) and order.get("order_id") is not None:
                    self._cities[str(order.get("order_id"))] = order.get("city_id")
        next_token = data.get("next_page_token")
        if isinstance(next_token, str) and next_token:
            self._tokens[number + 1] = next_token
        return data

    def detail(self, order_id: str) -> dict[str, Any]:
        if not order_id or not order_id.isdigit():
            raise BoltError("receipt_identity_mismatch")
        body = {"order_id": int(order_id), "city_id": self._cities.get(order_id)}
        return self._get_data(
            "POST", "/deliveryClient/v2/getOrderDetails", _query(self._device), body
        )
