"""Manual headed login on account.kaufland.com; credentials and codes go only to Kaufland."""

import json
import secrets
import sys
import time
import uuid
from typing import Any

import httpx

from cartalytix.export import ExportError

from .api import (
    REDIRECT,
    authorization_url,
    callback_code,
    pkce,
    subject,
    token_request,
)


class Vault:
    """Explicit Windows vault backend. Never fall back to an unencrypted keyring."""

    service = "Cartalytix.Kaufland"
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
        # Access token is unnecessary at rest and can exceed Windows vault size limits.
        kept = {k: value.get(k) for k in ("refresh_token", "profile", "customer")}
        self.backend.set_password(self.service, self.account, json.dumps(kept))

    def clear(self) -> None:
        if self.load():
            self.backend.delete_password(self.service, self.account)


def login(
    vault: Vault, *, channel: str = "chrome", timeout: float = 600
) -> dict[str, Any]:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import sync_playwright

    verifier, challenge = pkce()
    state = secrets.token_urlsafe(32)
    captured: list[str] = []

    def capture(url: str) -> None:
        if url.startswith(REDIRECT) and not captured:
            captured.append(url)

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
            page.on("request", lambda request: capture(request.url))
            page.on(
                "response",
                lambda response: capture(response.headers.get("location", "")),
            )
            # The custom-scheme redirect never loads, so CDP sees it before Playwright does.
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.on(
                "Network.requestWillBeSent",
                lambda event: capture(event.get("request", {}).get("url", "")),
            )
            cdp.on(
                "Network.responseReceivedExtraInfo",
                lambda event: capture(
                    next(
                        (
                            v
                            for k, v in event.get("headers", {}).items()
                            if k.lower() == "location"
                        ),
                        "",
                    )
                ),
            )
            try:
                page.goto(
                    authorization_url(challenge, state),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
            except BrowserError:
                if not captured:
                    raise ExportError(
                        "Cannot load Kaufland login; nothing recorded"
                    ) from None
            print(
                "Přihlaste se ručně v okně Kaufland. E-mail, heslo i kód zadávejte pouze tam.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while not captured and time.monotonic() < deadline:
                if page.is_closed():
                    raise ExportError("Login window closed")
                page.wait_for_timeout(200)
            if not captured:
                raise ExportError("Login timed out; rerun locally")
            try:
                code = callback_code(captured[0], state)
            except ValueError:
                raise ExportError(
                    "OAuth callback validation failed; no token saved"
                ) from None
        except BrowserError:
            raise ExportError("Browser login interrupted; rerun locally") from None
        finally:
            browser.close()
    with httpx.Client(timeout=30, follow_redirects=False) as http:
        result = token_request(
            http,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            },
        )
        customer = subject(http, str(result["access_token"]))
    if not result.get("refresh_token"):
        raise ExportError("Login did not return a refresh token")
    # Fresh namespace on every login prevents different accounts sharing cached receipts.
    tokens = {
        "refresh_token": result["refresh_token"],
        "profile": uuid.uuid4().hex,
        "customer": customer,
        "expires_in": result.get("expires_in"),
    }
    vault.save(tokens)
    return tokens
