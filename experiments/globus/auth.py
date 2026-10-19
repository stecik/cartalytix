"""Manual headed login on globus.cz; password and codes go only to Globus."""

import json
import time
import uuid
from pathlib import Path
from typing import Any

from cartalytix.export import ExportError, write_json
from wolt.auth import Vault as WoltVault

from .capture import Recorder, api_url

SITE = "https://www.globus.cz/"
SESSION_FILE = Path("work") / "globus-session.json"
FLOW_FILE = Path("work") / "globus-flow.json"
LIST_KEY = "GET www.globus.cz/api/v1/gsoa/customer/receipts"
COOKIE = "oidc_session"


class Vault(WoltVault):
    service = "Cartalytix.Globus"

    def save(self, value: dict[str, Any]) -> None:
        kept = {k: value.get(k) for k in ("session_cookie", "profile")}
        self.backend.set_password(self.service, self.account, json.dumps(kept))


def login(
    vault: Vault, *, channel: str = "chrome", timeout: float = 900
) -> dict[str, Any]:
    """Record the flow and keep the session cookie; return only non-secret facts."""
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import Response, sync_playwright

    recorder = Recorder()
    pending: list[Response] = []
    cookie: str | None = None
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
            # Playwright cannot wrap builtin methods such as list.append as handlers.
            context.on("response", lambda response: pending.append(response))
            page = context.new_page()
            try:
                page.goto(SITE, wait_until="domcontentloaded", timeout=60000)
            except BrowserError:
                raise ExportError("Cannot load Globus; nothing recorded") from None
            print(
                "Přihlaste se ručně do Můj Globus v otevřeném okně (údaje a kódy "
                "zadávejte jen tam) a otevřete přehled účtenek. Okno se pak "
                "zavře samo.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            # Login may open further pages; the user is done only when all are closed.
            while time.monotonic() < deadline and context.pages and cookie is None:
                try:
                    context.pages[0].wait_for_timeout(200)
                except BrowserError:
                    continue  # That page closed meanwhile; re-check the others.
                while pending:
                    response = pending.pop(0)
                    request = response.request
                    if not api_url(request.url):
                        continue
                    try:
                        data = response.json()
                    except (BrowserError, ValueError):
                        data = None
                    try:
                        headers = request.all_headers()
                    except BrowserError:
                        headers = {}
                    try:
                        body = request.post_data
                    except UnicodeDecodeError:
                        body = None  # Binary body (beacons); never a token request.
                    recorder.observe(
                        request.method,
                        request.url,
                        headers,
                        body,
                        response.status,
                        data,
                    )
                if LIST_KEY in recorder.receipts:
                    cookie = next(
                        (
                            c.get("value")
                            for c in context.cookies(SITE)
                            if c.get("name") == COOKIE
                        ),
                        None,
                    )
        except BrowserError:
            raise ExportError("Browser login interrupted; rerun locally") from None
        finally:
            write_json(
                FLOW_FILE,
                {"requests": recorder.flow, "receipt_schemas": recorder.samples},
            )
            browser.close()
    if not cookie:
        raise ExportError(
            f"Receipt overview or session cookie not seen; inspect {FLOW_FILE}"
        )
    # Keeping the profile keeps the data directory and its PDF cache across logins.
    profile = str((vault.load() or {}).get("profile") or uuid.uuid4().hex)
    vault.save({"session_cookie": cookie, "profile": profile})
    write_json(
        SESSION_FILE, {"refresh": recorder.refresh, "receipts": recorder.receipts}
    )
    # Raw responses stay local and private for designing the export and normalization.
    capture = Path("data") / "globus" / "CZ" / profile / "raw" / "capture"
    for number, value in enumerate(recorder.raw.values(), 1):
        write_json(capture / f"{number:03d}.json", value)
    return {
        "receipt_requests": sorted(recorder.receipts),
        "session_cookie": True,
        "refresh_token": recorder.refresh_token is not None,
    }
