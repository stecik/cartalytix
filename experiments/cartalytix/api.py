"""Small read-only API adapter; no coupon/profile mutation methods."""

import time
from urllib.parse import quote

import httpx

from .export import ExportError

AUTH = "https://accounts.lidl.com"
TICKETS = "https://tickets.lidlplus.com/api"
CLIENT_ID = "LidlPlusNativeClient"
APP_VERSION = (
    "16.45.5"  # Observed in public clients, not a guaranteed current app version.
)


def token_request(http, data):
    # Refresh tokens may rotate: never retry an ambiguous token POST.
    try:
        response = http.post(
            f"{AUTH}/connect/token", data=data, auth=(CLIENT_ID, "secret")
        )
    except httpx.HTTPError:
        raise ExportError("Token request failed; retry by signing in locally") from None
    if response.status_code != 200:
        raise ExportError(
            f"Authentication HTTP {response.status_code}; sign in locally again"
        )
    try:
        result = response.json()
        if (
            not isinstance(result.get("access_token"), str)
            or not result["access_token"]
        ):
            raise ValueError
        if float(result["expires_in"]) <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ExportError("Unexpected token response schema") from None


class Client:
    def __init__(self, tokens, save, *, http=None, delay=0.5, app_version=APP_VERSION):
        self.tokens = dict(tokens)
        self.save = save
        self.http = http or httpx.Client(timeout=30, follow_redirects=False)
        self.owns_http = http is None
        self.delay = delay
        self.app_version = app_version

    def close(self):
        if self.owns_http:
            self.http.close()

    def renew(self):
        result = token_request(
            self.http,
            {
                "grant_type": "refresh_token",
                "refresh_token": self.tokens["refresh_token"],
            },
        )
        updated = {
            **self.tokens,
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", self.tokens["refresh_token"]),
            "expires_at": time.time() + float(result["expires_in"]),
        }
        self.save(updated)  # Persist rotation before any further network call.
        self.tokens = updated

    def get(self, path, params=None, *, language="cs-CZ"):
        renewed = False
        for attempt in range(4):
            if (
                not self.tokens.get("access_token")
                or self.tokens.get("expires_at", 0) <= time.time() + 60
            ):
                self.renew()
            time.sleep(self.delay)
            try:
                response = self.http.get(
                    f"{TICKETS}/{path}",
                    params=params,
                    headers={
                        "Authorization": "Bearer " + self.tokens["access_token"],
                        "App-Version": self.app_version,
                        "Operating-System": "iOs",
                        "App": "com.lidl.eci.lidl.plus",
                        "Accept-Language": language,
                    },
                )
            except httpx.TransportError:
                if attempt == 3:
                    raise ExportError(
                        "API connection failed after bounded retries"
                    ) from None
                time.sleep(self.delay * (2**attempt))
                continue
            if response.status_code == 401 and not renewed:
                self.renew()
                renewed = True
                continue
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                retry = response.headers.get("Retry-After", "")
                if retry:
                    try:
                        wait = float(retry)
                    except ValueError:
                        raise ExportError(
                            "API requested delayed retry; stop and rerun later"
                        ) from None
                    if not 0 <= wait <= 60:
                        raise ExportError("API rate limit; rerun later")
                else:
                    wait = max(self.delay, 1) * (2**attempt)
                time.sleep(wait)
                continue
            if response.status_code != 200:
                raise ExportError(
                    f"Receipt API HTTP {response.status_code}; no response body logged"
                )
            try:
                result = response.json()
            except ValueError:
                raise ExportError("Receipt API returned non-JSON data") from None
            if not isinstance(result, dict):
                raise ExportError("Receipt API returned unexpected schema")
            return result
        raise ExportError("API retry limit reached")

    def page(self, number):
        return self.get(
            "v2/CZ/tickets", {"pageNumber": number, "onlyFavorite": "false"}
        )

    def detail(self, receipt_id):
        return self.get("v3/CZ/tickets/" + quote(receipt_id, safe=""), language="cs")
