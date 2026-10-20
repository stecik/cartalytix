"""Read-only Globus receipts with automatic renewal of verified web sessions.

The web API proxies the loyalty receipt list and renders each receipt only as PDF/PNG;
there is no JSON detail with items, so items are parsed from the PDF text layer.
"""

import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from cartalytix.providers.common import ProviderPayload
from cartalytix.providers.globus.receipt_pdf import pdf_text
from cartalytix.providers.globus.session import COOKIE as COOKIE
from cartalytix.providers.globus.session import COOKIE_VALUE as COOKIE_VALUE
from cartalytix.providers.globus.session import PROFILE_URL as PROFILE_URL
from cartalytix.providers.globus.session import GlobusAccount as GlobusAccount
from cartalytix.providers.globus.session import (
    GlobusError,
    decode_session,
    refresh_session,
    session_headers,
)
from cartalytix.providers.globus.session import fetch_identity as fetch_identity
from cartalytix.providers.globus.session import parse_session_cookie as parse_session_cookie

LIST_URL = "https://www.globus.cz/api/v1/gsoa/customer/receipts"
PAGE_SIZE = 20


def _client(http: httpx.Client | None) -> tuple[httpx.Client, bool]:
    return (http, False) if http is not None else (httpx.Client(timeout=30), True)


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
        self._session = decode_session(cookie)
        self._cookie = (
            self._session.cookie if self._session is not None else parse_session_cookie(cookie)
        )
        self._save_refresh = save_refresh
        self._prepared = False
        self._http, self._owns_http = _client(http)
        self._delay = delay
        self._heartbeat = heartbeat
        self._extract = extract

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _refresh(self) -> None:
        if self._session is None:
            raise GlobusError("auth_required")
        updated = refresh_session(self._session, http=self._http, heartbeat=self._heartbeat)
        serialized = updated.serialize()
        if serialized != self._session.serialize():
            self._save_refresh(serialized)
        self._session = updated
        self._cookie = updated.cookie

    def _get(self, url: str, params: dict[str, str] | None, accept: str) -> httpx.Response:
        if not self._prepared and self._session is not None:
            self._refresh()
            self._prepared = True
        refreshed = False
        attempt = 0
        while attempt < 4:
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(
                    url,
                    params=params,
                    headers=session_headers(
                        self._cookie,
                        accept,
                        self._session.user_agent if self._session is not None else "",
                    ),
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if attempt == 3:
                    raise GlobusError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                attempt += 1
                continue
            if response.status_code in {401, 403} or response.is_redirect:
                if self._session is not None and not refreshed:
                    self._refresh()
                    refreshed = True
                    continue
                if response.status_code == 403 and self._session is not None:
                    raise GlobusError("provider_unavailable")
                raise GlobusError("auth_required")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise GlobusError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 2) * (2**attempt)))
                attempt += 1
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
