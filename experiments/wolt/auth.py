"""Manual headed login on wolt.com; e-mail, codes and any challenge go only to Wolt."""

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cartalytix.export import ExportError, write_json

from .capture import Recorder, api_url

SITE = "https://wolt.com/cs/cze"
SESSION_FILE = Path("work") / "wolt-session.json"
FLOW_FILE = Path("work") / "wolt-flow.json"


class Vault:
    """Explicit Windows vault backend. Never fall back to an unencrypted keyring."""

    service = "Cartalytix.Wolt"
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
        kept = {k: value.get(k) for k in ("refresh_token", "profile")}
        self.backend.set_password(self.service, self.account, json.dumps(kept))

    def clear(self) -> None:
        if self.load():
            self.backend.delete_password(self.service, self.account)


def login(
    vault: Vault, *, channel: str = "chrome", timeout: float = 900
) -> dict[str, Any]:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import Response, sync_playwright

    recorder = Recorder()
    pending: list[Response] = []
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
                raise ExportError("Cannot load Wolt; nothing recorded") from None
            print(
                "Přihlaste se ručně v okně Wolt (údaje a kódy zadávejte jen tam). "
                "Otevřete Profil > Historie objednávek a detail jedné objednávky, "
                "pak okno zavřete.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not page.is_closed():
                try:
                    page.wait_for_timeout(200)
                except BrowserError:
                    break  # Window closed by the user.
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
                    recorder.observe(
                        request.method,
                        request.url,
                        headers,
                        request.post_data,
                        response.status,
                        data,
                    )
        except BrowserError:
            raise ExportError("Browser login interrupted; rerun locally") from None
        finally:
            write_json(
                FLOW_FILE,
                {"requests": recorder.flow, "order_schemas": recorder.samples},
            )
            browser.close()
    if not recorder.complete():
        missing = [
            name
            for name, value in (
                ("refresh_token", recorder.refresh_token),
                ("order_request", recorder.orders),
            )
            if not value
        ]
        raise ExportError(
            f"Login incomplete, missing: {', '.join(missing)}; see {FLOW_FILE}"
        )
    profile = uuid.uuid4().hex
    secrets = {"refresh_token": recorder.refresh_token, "profile": profile}
    vault.save(secrets)
    write_json(
        SESSION_FILE,
        {"refresh": recorder.refresh_template(), "orders": recorder.orders},
    )
    # Raw responses stay local and private for designing the export and normalization.
    capture = Path("data") / "wolt" / "CZ" / profile / "raw" / "capture"
    for number, value in enumerate(recorder.raw.values(), 1):
        write_json(capture / f"{number:03d}.json", value)
    return secrets
