"""Read-only Globus adapter: pasted globus.cz session cookie, receipt list and receipt PDFs.

The web API proxies the loyalty receipt list and renders each receipt only as PDF/PNG;
there is no JSON detail with items, so items are parsed from the PDF text layer.
"""

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload
from cartalytix.providers.globus.receipt_pdf import pdf_text

LIST_URL = "https://www.globus.cz/api/v1/gsoa/customer/receipts"
PROFILE_URL = "https://www.globus.cz/api/v1/gsoa/customer/profiles/me/auth-status"
COOKIE = "oidc_session"
PAGE_SIZE = 20
# RFC 6265 cookie-octet; the value goes only into the Cookie header for www.globus.cz.
COOKIE_VALUE = re.compile(r"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]{1,4096}")


class GlobusError(ProviderError):
    pass


@dataclass(frozen=True)
class GlobusAccount:
    subject: str
    email: str | None


def parse_session_cookie(value: str) -> str:
    """Accept the copied cookie value, optionally pasted together with its name."""
    cookie = value.strip().removeprefix(f"{COOKIE}=")
    if not COOKIE_VALUE.fullmatch(cookie):
        raise GlobusError("session_cookie_invalid")
    return cookie


def _client(http: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (http, False) if http is not None else (httpx.Client(timeout=30), True)


def _headers(cookie: str, accept: str) -> dict[str, str]:
    return {"Accept": accept, "Cookie": f"{COOKIE}={cookie}"}


def fetch_identity(cookie: str, *, http: httpx.Client | None = None) -> GlobusAccount:
    client, owned = _client(http)
    try:
        try:
            response = client.get(
                PROFILE_URL, headers=_headers(cookie, "application/json"), follow_redirects=False
            )
        except httpx.HTTPError:
            raise GlobusError("provider_unavailable") from None
    finally:
        if owned:
            client.close()
    if response.status_code in {401, 403} or response.is_redirect:
        raise GlobusError("session_cookie_rejected")
    if response.status_code != 200:
        raise GlobusError("provider_unavailable")
    try:
        data = response.json()
    except ValueError:
        raise GlobusError("provider_response_invalid") from None
    if not isinstance(data, dict) or data.get("authenticated") is not True:
        raise GlobusError("session_cookie_rejected")
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    registration = profile.get("registration") if isinstance(profile, dict) else None
    contacts = profile.get("contacts") if isinstance(profile, dict) else None
    customer = registration.get("customerID") if isinstance(registration, dict) else None
    if isinstance(customer, bool) or not isinstance(customer, int | str) or not str(customer):
        raise GlobusError("account_identity_unavailable")
    email = contacts.get("email") if isinstance(contacts, dict) else None
    return GlobusAccount(
        subject=str(customer), email=email if isinstance(email, str) and email else None
    )


class GlobusClient:
    def __init__(
        self,
        cookie: str,
        save_refresh: Callable[[str], None],
        *,
        http: httpx.Client | None = None,
        delay: float = 1.0,
        heartbeat: Callable[[], None] | None = None,
        extract: Callable[[bytes], str] = pdf_text,
    ) -> None:
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        # The session cookie is never rotated by Globus, so save_refresh stays unused.
        self._cookie = cookie
        self._http, self._owns_http = _client(http)
        self._delay = delay
        self._heartbeat = heartbeat
        self._extract = extract

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _get(self, url: str, params: dict[str, str] | None, accept: str) -> httpx.Response:
        for attempt in range(4):
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    url,
                    params=params,
                    headers=_headers(self._cookie, accept),
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise GlobusError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            # An expired session is answered with 401/403 or a redirect to the login page.
            if response.status_code in {401, 403} or response.is_redirect:
                raise GlobusError("auth_required")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise GlobusError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 2) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise GlobusError("provider_unavailable")
            return response
        raise GlobusError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise GlobusError("provider_response_invalid")
        response = self._get(
            LIST_URL,
            {
                "pageNumber": str(number),
                "pageSize": str(PAGE_SIZE),
                "totalFrom": "0",
                "includeGlobusOnline": "true",
            },
            "application/json",
        )
        try:
            result = response.json()
        except ValueError:
            raise GlobusError("provider_response_invalid") from None
        if not isinstance(result, dict):
            raise GlobusError("provider_response_invalid")
        return ProviderPayload(result, response.content)

    def detail(self, receipt_id: str) -> dict[str, Any]:
        if not receipt_id:
            raise GlobusError("receipt_identity_mismatch")
        content = self._get(
            f"{LIST_URL}/{quote(receipt_id, safe='')}/pdf", None, "application/pdf"
        ).content
        if not content.startswith(b"%PDF"):
            raise GlobusError("provider_response_invalid")
        try:
            text = self._extract(content)
        except Exception:
            # pdfminer raises many unrelated exception types for malformed documents.
            raise GlobusError("provider_response_invalid") from None
        # The PDF itself is archived as the raw document; parsing reads only its text.
        return ProviderPayload({"id": receipt_id, "text": text}, content)
