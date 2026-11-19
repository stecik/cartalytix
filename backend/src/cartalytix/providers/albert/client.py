"""Můj Albert SMS sign-in and read-only purchase history."""

import base64
import hmac
import json
import math
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import SecretStr

from cartalytix.providers.common import ProviderError, ProviderPayload
from cartalytix.settings import Settings

AUTH = "https://aczloyalty-auth-service-v2-prod.delhaize.eu"
API = "https://aczloyalty-service-app-prod.delhaize.eu"
PAGE_SIZE = 20
MAX_PAGES = 10_000
FLOW_TTL = 600
TOKEN_LIMIT = 16_384
APP_HEADERS = {
    "Accept": "application/json",
    "AppVersion": "7.4.0",
    "BuildVersion": "7951",
    "X-App-Build-Number": "7951",
}


class AlbertError(ProviderError):
    pass


@dataclass(frozen=True)
class AlbertConfig:
    username: SecretStr
    password: SecretStr
    verification_salt: SecretStr
    login_salt: SecretStr

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AlbertConfig:
        settings = settings if settings is not None else Settings()
        username = settings.albert_client_username
        password = settings.albert_client_password
        verification_salt = settings.albert_verification_salt
        login_salt = settings.albert_login_salt
        if not (username and password and verification_salt and login_salt):
            raise AlbertError("credentials_unavailable")
        return cls(username, password, verification_salt, login_salt)

    def basic_auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.username.get_secret_value(), self.password.get_secret_value())


@dataclass(frozen=True)
class AlbertFlow:
    login_id: str = field(repr=False)
    installation_id: str = field(repr=False)
    phone: str = field(repr=False)
    expires_at: float
    clock_offset: float = 0.0


@dataclass(frozen=True)
class AlbertAccount:
    credential: str = field(repr=False)
    subject: str = field(repr=False)
    label: str | None = field(default=None, repr=False)


def _identifier(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= 256 and value.strip() and value.isprintable():
        return value
    return None


def _token(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= TOKEN_LIMIT and re.fullmatch(r"[!-~]+", value):
        return value
    return None


def _auth_request(
    http: httpx.Client,
    config: AlbertConfig,
    method: str,
    path: str,
    *,
    content: bytes | None = None,
    json_body: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        return http.request(
            method,
            f"{AUTH}{path}",
            auth=config.basic_auth(),
            follow_redirects=False,
            content=content,
            json=json_body,
            headers=headers,
        )
    except httpx.HTTPError:
        raise AlbertError("provider_unavailable") from None


def _clock_offset(response: httpx.Response, fallback: float = 0.0) -> float:
    date = response.headers.get("Date")
    if date:
        try:
            server_date = parsedate_to_datetime(date)
            if server_date.tzinfo is not None:
                return server_date.timestamp() - time.time()
        except ValueError, OverflowError:
            pass
    return fallback


def _payload(
    response: httpx.Response, *, invalid: str = "provider_response_invalid"
) -> dict[str, Any]:
    try:
        data = json.loads(response.content, parse_float=Decimal)
    except ValueError, RecursionError:
        raise AlbertError(invalid) from None
    if not isinstance(data, dict):
        raise AlbertError(invalid)
    return data


def _phone_digits(phone: str) -> str:
    if not re.fullmatch(r"\+420[0-9]{9}", phone):
        raise AlbertError("phone_rejected")
    return phone[4:]


def _signed_request(
    http: httpx.Client,
    config: AlbertConfig,
    method: str,
    path: str,
    body: dict[str, str],
    *,
    digits: str,
    installation_id: str,
    clock_offset: float,
    rejected: str,
    verification: bool = False,
) -> tuple[dict[str, Any], httpx.Response]:
    salt = config.verification_salt if verification else config.login_salt
    content = json.dumps(body, separators=(",", ":")).encode()
    timestamp = str(int((time.time() + clock_offset) * 1000))
    signature = hmac.digest(
        salt.get_secret_value().encode(), content + timestamp.encode(), "sha256"
    )
    response = _auth_request(
        http,
        config,
        method,
        path,
        content=content,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Installation-Id": installation_id,
            "Phone-Number": base64.b64encode(digits.encode()).decode(),
            "Timestamp": timestamp,
            "Verification-Token": signature.hex(),
        },
    )
    if response.status_code == 429:
        raise AlbertError("login_rate_limited")
    if 400 <= response.status_code < 500:
        raise AlbertError(rejected)
    if not response.is_success:
        raise AlbertError("provider_unavailable")
    return _payload(response), response


def _flow_expiry(value: object, clock_offset: float) -> float:
    now = time.time()
    if value is None:
        return now + FLOW_TTL
    try:
        if type(value) is int or isinstance(value, Decimal):
            expiry = float(value)
            if expiry >= 100_000_000_000:
                expiry /= 1000
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError
            expiry = parsed.timestamp()
        else:
            raise ValueError
    except ValueError, OverflowError, OSError:
        raise AlbertError("provider_response_invalid") from None
    expiry -= clock_offset
    if not math.isfinite(expiry) or expiry <= now:
        raise AlbertError("provider_response_invalid")
    return min(expiry, now + FLOW_TTL)


def start_verification(
    phone: str,
    *,
    config: AlbertConfig | None = None,
    http: httpx.Client | None = None,
) -> AlbertFlow:
    digits = _phone_digits(phone)
    config = config if config is not None else AlbertConfig.from_settings()
    # UUIDv1 wire format with a random multicast node keeps the host MAC private.
    installation_id = str(uuid.uuid1(node=secrets.randbits(48) | (1 << 40)))
    owned = http is None
    http = http if http is not None else httpx.Client(timeout=30)
    try:
        probe = _auth_request(http, config, "GET", "/")
        if probe.status_code == 429:
            raise AlbertError("login_rate_limited")
        if probe.is_redirect or probe.is_server_error:
            raise AlbertError("provider_unavailable")
        clock_offset = _clock_offset(probe)
        body = {"phoneNumber": digits, "phonePrefix": "+420"}
        started, response = _signed_request(
            http,
            config,
            "POST",
            "/auth/start",
            body,
            digits=digits,
            installation_id=installation_id,
            clock_offset=clock_offset,
            rejected="phone_rejected",
        )
        start_id = _identifier(started.get("startId"))
        if start_id is None:
            raise AlbertError("provider_response_invalid")
        clock_offset = _clock_offset(response, clock_offset)
        verified, response = _signed_request(
            http,
            config,
            "PUT",
            "/auth/request-code",
            {**body, "startId": start_id},
            digits=digits,
            installation_id=installation_id,
            clock_offset=clock_offset,
            rejected="phone_rejected",
            verification=True,
        )
        login_id = _identifier(verified.get("loginId"))
        if login_id is None:
            raise AlbertError("provider_response_invalid")
        clock_offset = _clock_offset(response, clock_offset)
        return AlbertFlow(
            login_id,
            installation_id,
            phone,
            _flow_expiry(verified.get("expiresAt"), clock_offset),
            clock_offset,
        )
    finally:
        if owned:
            http.close()


def _tokens(data: dict[str, Any]) -> tuple[str, str, str]:
    access = _token(data.get("accessToken"))
    refresh = _token(data.get("refreshToken"))
    if access is None or refresh is None:
        raise AlbertError("provider_response_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", access):
        raise AlbertError("provider_response_invalid")
    # Albert's auth service issues user_name as the account principal in its TLS
    # response. Use that same claim to bind both login and later token rotations.
    _, encoded, _ = access.split(".")
    try:
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except ValueError, RecursionError:
        raise AlbertError("provider_response_invalid") from None
    subject = _identifier(claims.get("user_name")) if isinstance(claims, dict) else None
    if subject is None:
        raise AlbertError("provider_response_invalid")
    return access, refresh, subject


def _credential(refresh: str, subject: str) -> str:
    return json.dumps({"refresh_token": refresh, "subject": subject}, separators=(",", ":"))


def confirm_verification(
    phone: str,
    installation_id: str,
    login_id: str,
    code: str,
    *,
    config: AlbertConfig | None = None,
    http: httpx.Client | None = None,
    clock_offset: float = 0.0,
) -> AlbertAccount:
    digits = _phone_digits(phone)
    if not re.fullmatch(r"[0-9]{6}", code):
        raise AlbertError("otp_rejected")
    if (
        not re.fullmatch(
            r"[a-f0-9]{8}-[a-f0-9]{4}-1[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}",
            installation_id,
        )
        or _identifier(login_id) is None
        or not math.isfinite(clock_offset)
    ):
        raise AlbertError("otp_rejected")
    config = config if config is not None else AlbertConfig.from_settings()
    owned = http is None
    http = http if http is not None else httpx.Client(timeout=30)
    try:
        result, _ = _signed_request(
            http,
            config,
            "POST",
            "/auth/login",
            {"loginId": login_id, "code": code},
            digits=digits,
            installation_id=installation_id,
            clock_offset=clock_offset,
            rejected="otp_rejected",
        )
        _, refresh, subject = _tokens(result)
        return AlbertAccount(_credential(refresh, subject), subject)
    finally:
        if owned:
            http.close()


@dataclass(frozen=True)
class _HistoryPage:
    receipts: dict[str, dict[str, Any]]
    total: int
    pages: int

    def signature(self) -> tuple[int, tuple[str, ...]]:
        return self.total, tuple(self.receipts)


def _retry_delay(header: str, attempt: int) -> float | None:
    value = header.strip()
    if value.isascii() and value.isdecimal():
        digits = value.lstrip("0")
        if len(digits) > 2:
            return None
        delay = float(digits or "0")
    else:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            delay = max(0.0, retry_at.timestamp() - time.time())
        except ValueError, OverflowError, OSError:
            delay = 2**attempt
    return delay if delay <= 10 else None


class AlbertClient:
    def __init__(
        self,
        credential: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        heartbeat: Callable[[], None] | None = None,
        http: httpx.Client | None = None,
        config: AlbertConfig | None = None,
    ) -> None:
        try:
            stored = json.loads(credential)
        except ValueError, RecursionError:
            raise AlbertError("auth_required") from None
        if not isinstance(stored, dict) or stored.get("refresh_inflight") is True:
            raise AlbertError("auth_required")
        refresh = _token(stored.get("refresh_token"))
        subject = _identifier(stored.get("subject"))
        if refresh is None or subject is None:
            raise AlbertError("auth_required")
        self._refresh = refresh
        self._subject = subject
        self._save = save_refresh
        self._heartbeat = heartbeat
        self._config = config if config is not None else AlbertConfig.from_settings()
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30)
        self._access: str | None = None
        self._auth_failed = False
        self._snapshot: list[dict[str, Any]] | None = None
        self._first_pages: dict[bool, tuple[int, tuple[str, ...]]] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _renew(self) -> None:
        if self._auth_failed:
            raise AlbertError("auth_required")
        if self._heartbeat is not None:
            self._heartbeat()
        # Commit the marker before the POST so a worker crash cannot replay the token.
        self._auth_failed = True
        try:
            self._save(json.dumps({"subject": self._subject, "refresh_inflight": True}))
            response = _auth_request(
                self._http,
                self._config,
                "POST",
                "/auth/refresh-token",
                json_body={"token": self._refresh},
                headers={"Accept": "application/json"},
            )
            if not response.is_success:
                raise AlbertError("auth_required")
            access, refresh, subject = _tokens(_payload(response))
            if subject != self._subject:
                raise AlbertError("auth_required")
            self._save(_credential(refresh, subject))
        except Exception:
            raise AlbertError("auth_required") from None
        self._access = access
        self._refresh = refresh
        self._auth_failed = False

    def _send_get(self, path: str, params: dict[str, str]) -> httpx.Response:
        for attempt in range(3):
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    f"{API}{path}",
                    headers={**APP_HEADERS, "Authorization": f"Bearer {self._access}"},
                    params=params,
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 2:
                    raise AlbertError("provider_unavailable") from None
                time.sleep(2**attempt)
                continue
            status = response.status_code
            if status != 429 and not 500 <= status <= 599:
                return response
            if attempt == 2:
                return response
            delay = _retry_delay(response.headers.get("Retry-After", ""), attempt)
            if delay is None:
                return response
            time.sleep(delay)
        raise AlbertError("provider_unavailable")

    def _get(
        self, path: str, params: dict[str, str], *, invalid: str = "provider_response_invalid"
    ) -> ProviderPayload:
        if self._auth_failed:
            raise AlbertError("auth_required")
        if self._access is None:
            self._renew()
        response = self._send_get(path, params)
        if response.status_code == 401:
            self._renew()
            response = self._send_get(path, params)
        if response.status_code in {401, 403}:
            self._auth_failed = True
            raise AlbertError("auth_required")
        if response.status_code == 429:
            raise AlbertError("rate_limited")
        if not response.is_success:
            raise AlbertError("provider_unavailable")
        return ProviderPayload(_payload(response, invalid=invalid), response.content)

    def _remote_page(self, number: int, *, archived: bool) -> _HistoryPage:
        params = {"pageNumber": str(number), "pageSize": str(PAGE_SIZE)}
        if archived:
            params["archived"] = "true"
        data = self._get("/customer/purchases/v2", params, invalid="history_incomplete")
        receipts, page = data.get("receipts"), data.get("page")
        if not isinstance(receipts, list) or not isinstance(page, dict):
            raise AlbertError("history_incomplete")
        found_number = page.get("number")
        size = page.get("size")
        pages = page.get("total")
        total = page.get("totalElements")
        if (
            type(found_number) is not int
            or found_number != number
            or type(size) is not int
            or size != PAGE_SIZE
            or type(pages) is not int
            or type(total) is not int
            or not 0 <= total <= MAX_PAGES * PAGE_SIZE
            or pages != (total + PAGE_SIZE - 1) // PAGE_SIZE
            or number > max(pages, 1)
            or len(receipts) != min(PAGE_SIZE, max(0, total - (number - 1) * PAGE_SIZE))
        ):
            raise AlbertError("history_incomplete")
        indexed: dict[str, dict[str, Any]] = {}
        for item in receipts:
            identifier = _identifier(item.get("receiptNumber")) if isinstance(item, dict) else None
            if identifier is None or identifier in indexed:
                raise AlbertError("history_incomplete")
            indexed[identifier] = item
        return _HistoryPage(indexed, total, pages)

    def _load_history(self) -> list[dict[str, Any]]:
        combined: dict[str, dict[str, Any]] = {}
        first_pages: dict[bool, tuple[int, tuple[str, ...]]] = {}
        for archived in (False, True):
            first = self._remote_page(1, archived=archived)
            first_pages[archived] = first.signature()
            stream = first.receipts
            for number in range(2, first.pages + 1):
                page = self._remote_page(number, archived=archived)
                if page.total != first.total or not stream.keys().isdisjoint(page.receipts):
                    raise AlbertError("history_incomplete")
                stream.update(page.receipts)
            for identifier, receipt in stream.items():
                combined.setdefault(identifier, receipt)
        if len(combined) > MAX_PAGES * PAGE_SIZE:
            raise AlbertError("history_incomplete")
        self._first_pages = first_pages
        return list(combined.values())

    def page(self, number: int) -> ProviderPayload:
        if type(number) is not int or number < 1:
            raise AlbertError("provider_response_invalid")
        if self._snapshot is None:
            self._snapshot = self._load_history()
        elif number == 1:
            for archived, original in self._first_pages.items():
                if self._remote_page(1, archived=archived).signature() != original:
                    raise AlbertError("history_incomplete")
        start = (number - 1) * PAGE_SIZE
        payload = {
            "receipts": self._snapshot[start : start + PAGE_SIZE],
            "totalCount": len(self._snapshot),
        }
        # A logical page combines two upstream streams. Archive Decimal amounts
        # as strings in this synthesized JSON to preserve every digit.
        raw = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str
        ).encode("utf-8")
        return ProviderPayload(payload, raw)

    def detail(self, receipt_id: str, /) -> ProviderPayload:
        if _identifier(receipt_id) is None:
            raise AlbertError("receipt_identity_mismatch")
        detail = self._get("/customer/purchase/detail", {"receiptNumber": receipt_id})
        if detail.get("receiptNumber") != receipt_id:
            raise AlbertError("receipt_identity_mismatch")
        return detail
