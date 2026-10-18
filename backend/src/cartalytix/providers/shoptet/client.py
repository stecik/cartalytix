"""Read-only Shoptet e-shop adapter; no cart, reorder or profile mutation methods."""

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
from cartalytix.providers.shoptet.normalization import (
    account_email,
    csrf_token,
    order_code,
    parse_list,
)

# Shops verified live; all share the stock Shoptet customer area markup.
SHOPS = {"fermato": "https://www.fermato.cz", "nejkafe": "https://www.nejkafe.cz"}
LOGIN_PAGE = "/login/"
LOGIN_ACTION = "/action/Customer/Login/"
ORDERS = "/klient/objednavky/"
SETTINGS = "/klient/nastaveni/"
CODE = re.compile(r"\d{1,20}")


class ShoptetError(ProviderError):
    pass


def _headers(site: str) -> dict[str, str]:
    return {"Accept": "text/html", "Accept-Language": "cs-CZ", "Referer": site + LOGIN_PAGE}


def _signed_out(response: httpx.Response) -> bool:
    location = response.headers.get("Location", "")
    return response.is_redirect and urlsplit(location).path.startswith("/login")


def sign_in(email: str, password: str, *, site: str, http: httpx.Client | None = None) -> str:
    """Start a Shoptet session and return the hashed account e-mail; the POST is never retried."""
    owned = http is None
    transport = http if http is not None else httpx.Client(timeout=30, follow_redirects=False)
    headers = _headers(site)
    try:
        try:
            # The login page issues the session cookie and CSRF token the form POST must carry.
            login = transport.get(site + LOGIN_PAGE, headers=headers, follow_redirects=False)
            form = {"email": email, "password": password, "referer": "", "surname": ""}
            token = csrf_token(login.text)
            if token:
                form["__csrf__"] = token
            response = transport.post(
                site + LOGIN_ACTION,
                data=form,
                headers=headers,
                follow_redirects=False,
            )
            if response.status_code == 429:
                raise ShoptetError("login_rate_limited")
            if response.status_code >= 500:
                raise ShoptetError("provider_unavailable")
            settings = transport.get(site + SETTINGS, headers=headers, follow_redirects=False)
        except httpx.HTTPError:
            raise ShoptetError("provider_unavailable") from None
        if _signed_out(settings):
            raise ShoptetError("credentials_rejected")
        if settings.status_code != 200:
            raise ShoptetError("provider_unavailable")
        address = account_email(settings.text)
        if not address:
            raise ShoptetError("provider_response_invalid")
        # Hashed address binds the connection to one account without storing it twice.
        return hashlib.sha256(address.strip().casefold().encode()).hexdigest()
    finally:
        if owned:
            transport.close()


class ShoptetClient:
    def __init__(
        self,
        credentials: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        site: str,
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
            raise ShoptetError("auth_required") from None
        self._site = site
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
            sign_in(self._email, self._password, site=self._site, http=self._http)
        except ShoptetError as error:
            raise ShoptetError(
                "auth_required" if error.code == "credentials_rejected" else error.code
            ) from None
        self._signed_in = True

    def _get(self, url: str) -> httpx.Response:
        if not self._email or not self._password:
            raise ShoptetError("auth_required")
        renewed = False
        for attempt in range(4):
            if not self._signed_in:
                self._sign_in()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(url, headers=_headers(self._site), follow_redirects=False)
            except httpx.HTTPError:
                if attempt == 3:
                    raise ShoptetError("provider_unavailable") from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if _signed_out(response):
                # Shoptet sessions expire; sign in once more, then treat as revoked.
                if renewed:
                    raise ShoptetError("auth_required")
                self._signed_in = False
                renewed = True
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise ShoptetError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable"
                    )
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise ShoptetError("provider_unavailable")
            return response
        raise ShoptetError("provider_unavailable")

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise ShoptetError("provider_response_invalid")
        url = self._site + ORDERS if number == 1 else self._pages.get(number)
        if url is None:
            # Shoptet links further pages only when they exist.
            return {"orders": [], "totalRecords": None}
        response = self._get(url)
        orders, pages = parse_list(response.text, self._site)
        self._pages.update(pages)
        return ProviderPayload({"orders": orders, "totalRecords": None}, response.content)

    def detail(self, code: str) -> dict[str, Any]:
        if not CODE.fullmatch(code):
            raise ShoptetError("receipt_identity_mismatch")
        response = self._get(f"{self._site}{ORDERS}?code={code}")
        return ProviderPayload(
            {"id": order_code(response.text), "html": response.text}, response.content
        )


def client_factory(shop: str) -> Callable[..., ShoptetClient]:
    """Bind a shop to the shared sync engine's client factory contract."""
    site = SHOPS[shop]

    def create(
        credentials: str,
        save_refresh: Callable[[str], None],
        /,
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> ShoptetClient:
        return ShoptetClient(credentials, save_refresh, site=site, heartbeat=heartbeat)

    return create
