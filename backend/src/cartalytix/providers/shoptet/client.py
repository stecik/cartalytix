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

from cartalytix.providers.common import ProviderError, ProviderPayload, auth_diagnostic
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
    phase = "login_page"
    csrf_present: bool | None = None
    try:
        try:
            # The login page issues the session cookie and CSRF token the form POST must carry.
            login = transport.get(site + LOGIN_PAGE, headers=headers, follow_redirects=False)
            form = {"email": email, "password": password, "referer": "", "surname": ""}
            token = csrf_token(login.text)
            csrf_present = bool(token)
            if token:
                form["__csrf__"] = token
            phase = "login"
            response = transport.post(
                site + LOGIN_ACTION,
                data=form,
                headers=headers,
                follow_redirects=False,
            )
            if response.status_code == 429:
                raise ShoptetError(
                    "login_rate_limited",
                    diagnostic=auth_diagnostic(phase, response=response, csrf_present=csrf_present),
                )
            if response.status_code >= 500:
                raise ShoptetError(
                    "provider_unavailable",
                    diagnostic=auth_diagnostic(phase, response=response, csrf_present=csrf_present),
                )
            phase = "login_verify"
            settings = transport.get(site + SETTINGS, headers=headers, follow_redirects=False)
        except httpx.HTTPError as error:
            raise ShoptetError(
                "provider_unavailable",
                diagnostic=auth_diagnostic(phase, error=error, csrf_present=csrf_present),
            ) from None
        if response.status_code >= 400:
            diagnostic = auth_diagnostic("login", response=response, csrf_present=csrf_present)
        elif login.status_code >= 400:
            diagnostic = auth_diagnostic("login_page", response=login, csrf_present=csrf_present)
        else:
            diagnostic = auth_diagnostic(phase, response=settings, csrf_present=csrf_present)
            if _signed_out(settings):
                diagnostic["reason"] = "session_not_authenticated"
        if _signed_out(settings):
            raise ShoptetError("credentials_rejected", diagnostic=diagnostic)
        if settings.status_code != 200:
            raise ShoptetError("provider_unavailable", diagnostic=diagnostic)
        address = account_email(settings.text)
        if not address:
            raise ShoptetError("provider_response_invalid", diagnostic=diagnostic)
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
                "auth_required" if error.code == "credentials_rejected" else error.code,
                diagnostic=error.diagnostic,
            ) from None
        self._signed_in = True

    def _get(self, url: str, *, phase: str) -> httpx.Response:
        if not self._email or not self._password:
            raise ShoptetError("auth_required", diagnostic=auth_diagnostic(phase))
        renewed = False
        for attempt in range(4):
            if not self._signed_in:
                self._sign_in()
            time.sleep(self._delay)
            if self._heartbeat is not None:
                self._heartbeat()
            try:
                response = self._http.get(url, headers=_headers(self._site), follow_redirects=False)
            except httpx.HTTPError as error:
                if attempt == 3:
                    raise ShoptetError(
                        "provider_unavailable", diagnostic=auth_diagnostic(phase, error=error)
                    ) from None
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if _signed_out(response):
                # Shoptet sessions expire; sign in once more, then treat as revoked.
                if renewed:
                    diagnostic = auth_diagnostic(phase, response=response)
                    diagnostic["reason"] = "session_not_authenticated"
                    raise ShoptetError("auth_required", diagnostic=diagnostic)
                self._signed_in = False
                renewed = True
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt == 3:
                    raise ShoptetError(
                        "rate_limited" if response.status_code == 429 else "provider_unavailable",
                        diagnostic=auth_diagnostic(phase, response=response),
                    )
                time.sleep(min(60, max(self._delay, 1) * (2**attempt)))
                continue
            if response.status_code != 200:
                raise ShoptetError(
                    "provider_unavailable", diagnostic=auth_diagnostic(phase, response=response)
                )
            return response
        raise ShoptetError("provider_unavailable", diagnostic=auth_diagnostic(phase))

    def page(self, number: int) -> dict[str, Any]:
        if isinstance(number, bool) or number < 1:
            raise ShoptetError("provider_response_invalid")
        url = self._site + ORDERS if number == 1 else self._pages.get(number)
        if url is None:
            # Shoptet links further pages only when they exist.
            return {"orders": [], "totalRecords": None}
        response = self._get(url, phase="history")
        orders, pages = parse_list(response.text, self._site)
        self._pages.update(pages)
        return ProviderPayload({"orders": orders, "totalRecords": None}, response.content)

    def detail(self, code: str) -> dict[str, Any]:
        if not CODE.fullmatch(code):
            raise ShoptetError("receipt_identity_mismatch")
        response = self._get(f"{self._site}{ORDERS}?code={code}", phase="detail")
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
