"""Manual headed browser login; credentials go directly to Lidl, never to Python."""

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .api import AUTH, CLIENT_ID, token_request
from .export import ExportError

REDIRECT = "com.lidlplus.app://callback"


def callback_code(url, expected_state):
    parsed = urlsplit(url)
    if (parsed.scheme, parsed.netloc, parsed.path) != (
        "com.lidlplus.app",
        "callback",
        "",
    ):
        raise ValueError("Unexpected OAuth redirect")
    params = parse_qs(parsed.query)
    if len(params.get("state", [])) != 1 or not hmac.compare_digest(
        params["state"][0], expected_state
    ):
        raise ValueError("OAuth state mismatch")
    if "error" in params or len(params.get("code", [])) != 1:
        raise ValueError("OAuth authorization failed")
    return params["code"][0]


class Vault:
    """Explicit Windows vault backend. Never fall back to an unencrypted keyring."""

    service = "Cartalytix.LidlPlus"
    account = "CZ:default"

    def __init__(self):
        if sys.platform != "win32":
            raise ExportError("This experiment requires Windows Credential Manager")
        from keyring.backends.Windows import WinVaultKeyring

        self.backend = WinVaultKeyring()

    def load(self):
        value = self.backend.get_password(self.service, self.account)
        return json.loads(value) if value else None

    def save(self, value):
        # Access token is unnecessary at rest and can exceed Windows vault size limits.
        kept = {k: value[k] for k in ("refresh_token", "profile")}
        self.backend.set_password(self.service, self.account, json.dumps(kept))

    def clear(self):
        if self.load():
            self.backend.delete_password(self.service, self.account)


def login(vault, *, channel="chrome", timeout=600):
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import sync_playwright

    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(32)
    auth_url = (
        AUTH
        + "/connect/authorize?"
        + urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT,
                "scope": "openid profile offline_access lpprofile lpapis",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "nonce": secrets.token_urlsafe(32),
                "Country": "CZ",
                "language": "cs-CZ",
            }
        )
    )
    captured = []

    def capture(url):
        if url.startswith(REDIRECT) and not captured:
            captured.append(url)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=False,
                **({"channel": channel} if channel != "chromium" else {}),
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
                page.goto(auth_url, wait_until="domcontentloaded", timeout=60000)
            except BrowserError:
                if not captured:
                    raise ExportError(
                        "Cannot load Lidl login; no credentials recorded"
                    ) from None
            print(
                "Přihlaste se ručně v okně Lidl. Heslo i SMS/e-mail kód zadávejte pouze tam.",
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
    if not result.get("refresh_token"):
        raise ExportError("Login did not return a refresh token")
    # Fresh namespace on every login prevents different accounts sharing cached receipts.
    tokens = {
        "refresh_token": result["refresh_token"],
        "profile": uuid.uuid4().hex,
        "access_token": result["access_token"],
        "expires_at": time.time() + float(result["expires_in"]),
    }
    vault.save(tokens)
    return tokens
