"""Read-only Fermato.cz (Shoptet) adapter; no cart, reorder or profile mutation methods."""

import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from cartalytix.providers.common import ProviderError, ProviderPayload
from cartalytix.providers.fermato.normalization import account_email, order_code, parse_list

SITE = "https://www.fermato.cz"
LOGIN_PAGE = SITE + "/login/"
LOGIN_ACTION = SITE + "/action/Customer/Login/"
ORDERS = SITE + "/klient/objednavky/"
SETTINGS = SITE + "/klient/nastaveni/"
CODE = re.compile(r"\d{1,20}")


class FermatoError(ProviderError):
    pass


def _headers() -> dict[str, str]:
    return {"Accept": "text/html", "Accept-Language": "cs-CZ", "Referer": LOGIN_PAGE}


def _signed_out(response: httpx.Response) -> bool:
    location = response.headers.get("Location", "")
    return response.is_redirect and urlsplit(location).path.startswith("/login")


def sign_in(email: str, password: str, *, http: httpx.Client | None = None) -> str:
    """Start a Shoptet session and return the hashed account e-mail; the POST is never retried."""
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    try:
        try:
            # The login page issues the session cookie the form POST must carry.
            transport.get(LOGIN_PAGE, headers=_headers(), follow_redirects=False)
            response = transport.post(
                LOGIN_ACTION,
                data={"email": email, "password": password, "referer": "", "surname": ""},
                headers=_headers(),
                follow_redirects=False,
            )
            if response.status_code == 429:
                raise FermatoError("login_rate_limited")
            if response.status_code >= 500:
                raise FermatoError("provider_unavailable")
            settings = transport.get(SETTINGS, headers=_headers(), follow_redirects=False)
        except httpx.HTTPError:
            raise FermatoError("provider_unavailable") from None
        if _signed_out(settings):
            raise FermatoError("credentials_rejected")
        if settings.status_code != 200:
            raise FermatoError("provider_unavailable")
        address = account_email(settings.text)
        if not address:
            raise FermatoError("provider_response_invalid")
        # Hashed address binds the connection to one account without storing it twice.
        return hashlib.sha256(address.strip().casefold().encode()).hexdigest()
    finally:
        if owned:
            transport.close()


class FermatoClient:
    def __init__(
        self,
        credentials: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        http: httpx.Client | None = None,
        delay: float = 0.5,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        # Credentials never rotate; save_refresh is kept for the shared factory contract.
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("Invalid request delay")
        try:
            values = json.loads(credentials)
            self._email = str(values.get("email", ""))
            self._password = str(values.get("password", ""))
        except ValueError, AttributeError:
            raise FermatoError("auth_required") from None
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
        self._delay = delay
        self._heartbeat = heartbeat
        self._signed_in = False
        self._pages: dict[int, str] = {}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _sign_in(self) -> None:
        try:
            sign_in(self._email, self._password, http=self._http)
        except FermatoError as error:
            raise FermatoError(
                "auth_required" if error.code == "credentials_rejected" else error.code
            ) from None
        self._signed_in = True

    def _get(self, url: str) -> httpx.Response:
        if not self._email or not self._password:
            raise FermatoError("auth_required")
        renewed = False
        for attempt in range(4):
            if not self._signed_in:
                self._sign_in()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(url, headers=_headers(), follow_redirects=False)
            except httpx.HTTPError:
                if attempt == 3:
                    raise FermatoError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if _signed_out(response):
                # Shoptet sessions expire; sign in once more, then treat as revoked.
                if renewed:
                    raise FermatoError("auth_required")
                self._signed_in = False
                renewed = True
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise FermatoError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise FermatoError("provider_unavailable")
            return response
        raise FermatoError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise FermatoError("provider_response_invalid")
        url = ORDERS if number == 1 else self._pages.get(number)
        if url is None:
            # Shoptet links further pages only when they exist.
            return {"orders": [], "totalRecords": None}
        response = self._get(url)
        orders, pages = parse_list(response.text)
        self._pages.update(pages)
        return ProviderPayload({"orders": orders, "totalRecords": None}, response.content)

    def detail(self, code: str) -> dict[str, Any]:
        if not CODE.fullmatch(code):
            raise FermatoError("receipt_identity_mismatch")
        response = self._get(f"{ORDERS}?code={code}")
        return ProviderPayload(
            {"id": order_code(response.text), "html": response.text}, response.content
        )
