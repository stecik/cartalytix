"""Manual headed browser login; credentials go directly to Mixit, never to Python."""

import base64
import json
import re
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from cartalytix.export import ExportError

from .api import SITE

SKIPPED_HEADERS = {
    "accept",
    "accept-encoding",
    "accept-language",
    "connection",
    "content-length",
    "content-type",
    "host",
    "origin",
    "referer",
    "user-agent",
}


def api_base(url: str) -> str | None:
    parsed = urlsplit(url)
    match = re.match(r"^(/api/v\d+)/", parsed.path)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not match or not host.endswith(".mixit.cz"):
        return None
    return f"https://{host}{match.group(1)}"


def capture_session(
    url: str, headers: dict[str, str], token: str
) -> dict[str, Any] | None:
    """Return how the web app itself sends its stored token to its own API, or None."""
    base = api_base(url)
    if base is None or len(token) < 16:
        return None
    found = next(
        (
            (name, value.removesuffix(token))
            for name, value in headers.items()
            if value in (token, "Bearer " + token)
        ),
        None,
    )
    if found is None:
        return None
    lowered = {name.lower(): value for name, value in headers.items()}
    context = {
        name: value
        for name, value in lowered.items()
        if name not in SKIPPED_HEADERS
        and not name.startswith(("sec-", ":"))
        and not any(part in name for part in ("token", "auth", "cookie", "visitor"))
        and len(value) <= 100
    }
    return {
        "token": token,
        "header": found[0],
        "prefix": found[1],
        "api": base,
        "context_headers": context,
    }


def token_expiry(token: str) -> float | None:
    """Unverified JWT `exp` read only to warn early; the server stays authoritative."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
        exp = payload.get("exp") if isinstance(payload, dict) else None
        return (
            float(exp)
            if isinstance(exp, int | float) and not isinstance(exp, bool)
            else None
        )
    except ValueError:
        return None


class Vault:
    """Explicit Windows vault backend. Never fall back to an unencrypted keyring."""

    service = "Cartalytix.Mixit"
    account = "CZ:default"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ExportError("This experiment requires Windows Credential Manager")
        from keyring.backends.Windows import WinVaultKeyring

        self.backend = WinVaultKeyring()

    def load(self) -> dict[str, Any] | None:
        value = self.backend.get_password(self.service, self.account)
        return json.loads(value) if value else None

    def save(self, value: dict[str, Any]) -> None:
        kept = {
            k: value.get(k)
            for k in ("token", "header", "prefix", "api", "context_headers", "profile")
        }
        self.backend.set_password(self.service, self.account, json.dumps(kept))

    def clear(self) -> None:
        if self.load():
            self.backend.delete_password(self.service, self.account)


def login(
    vault: Vault, *, channel: str = "chrome", timeout: float = 600
) -> dict[str, Any]:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import sync_playwright

    # Kept in memory only, limited to the app's own API requests.
    requests: dict[str, dict[str, Any]] = {}
    extra: dict[str, dict[str, str]] = {}

    def on_request(event: dict[str, Any]) -> None:
        request = event.get("request", {})
        url = request.get("url", "")
        request_id = event.get("requestId", "")
        if api_base(url) is None:
            extra.pop(request_id, None)
            return
        previous = requests.get(request_id, {}).get("headers", {})
        headers = {**previous, **request.get("headers", {})}
        requests[request_id] = {
            "url": url,
            "headers": {**headers, **extra.pop(request_id, {})},
        }

    def on_extra(event: dict[str, Any]) -> None:
        request_id = event.get("requestId", "")
        if request_id in requests:
            requests[request_id]["headers"].update(event.get("headers", {}))
            return
        if len(extra) > 200:
            extra.clear()
        extra[request_id] = event.get("headers", {})

    def find_session(token: str) -> dict[str, Any] | None:
        for request in list(requests.values()):
            session = capture_session(request["url"], request["headers"], token)
            if session:
                return session
        return None

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False, channel=None if channel == "chromium" else channel
            )
        except BrowserError:
            raise ExportError(
                "Browser launch failed; use --browser chrome or install Chromium locally"
            ) from None
        try:
            context = browser.new_context(locale="cs-CZ")
            page = context.new_page()
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.on("Network.requestWillBeSent", on_request)
            cdp.on("Network.requestWillBeSentExtraInfo", on_extra)
            try:
                page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60000)
            except BrowserError:
                raise ExportError(
                    "Cannot load Mixit; no credentials recorded"
                ) from None
            print(
                "Přihlaste se ručně v okně Mixit. Heslo zadávejte pouze tam. "
                "Po přihlášení otevřete Můj účet > Objednávky.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            token: str | None = None
            session: dict[str, Any] | None = None
            while session is None and time.monotonic() < deadline:
                if page.is_closed():
                    raise ExportError("Login window closed")
                page.wait_for_timeout(200)
                if token is None:
                    try:
                        stored = page.evaluate("() => localStorage.getItem('token')")
                    except BrowserError:
                        continue  # Page is navigating; read again on next tick.
                    if isinstance(stored, str) and stored:
                        token = stored
                        # Token exists; give the app a moment to call its API with it.
                        deadline = min(deadline, time.monotonic() + 120)
                if token:
                    session = find_session(token)
            if session is None:
                if token:
                    names = sorted(
                        {n.lower() for r in requests.values() for n in r["headers"]}
                    )
                    # Header names only, never values.
                    print(
                        f"Diagnostika: {len(requests)} API požadavků, hlavičky: {names}",
                        flush=True,
                    )
                    raise ExportError("Token found but not seen in API requests")
                raise ExportError("Login timed out; rerun locally")
        except BrowserError:
            raise ExportError("Browser login interrupted; rerun locally") from None
        finally:
            browser.close()
    # Fresh namespace on every login prevents different accounts sharing cached orders.
    session = {**session, "profile": uuid.uuid4().hex}
    vault.save(session)
    return session
