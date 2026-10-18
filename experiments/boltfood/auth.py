"""Manual headed login on food.bolt.eu; phone, SMS code and any challenge go only to Bolt."""

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from cartalytix.export import ExportError, write_json

from .capture import (
    api_path,
    base_url,
    context_headers,
    find_key,
    find_path,
    keys,
    query,
    scheme,
    token_header,
    values_at,
)

SITE = "https://food.bolt.eu/cs-cz/"
SESSION_FILE = Path("work") / "boltfood-session.json"
FLOW_FILE = Path("work") / "boltfood-flow.json"


class Vault:
    """Explicit Windows vault backend. Never fall back to an unencrypted keyring."""

    service = "Cartalytix.BoltFood"
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


class Recorder:
    """Builds replay templates from the app's own requests; keeps only names in the flow log."""

    def __init__(self) -> None:
        self.flow: list[dict[str, Any]] = []
        self.refresh_token: str | None = None
        self.access_token: str | None = None
        self.refresh: dict[str, Any] | None = None
        self.history: dict[str, Any] | None = None
        self.history_sample: Any = None
        self.detail: dict[str, Any] | None = None
        self.challenge_seen = False

    def observe(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
        status: int,
        data: Any,
    ) -> None:
        path = api_path(url)
        if path is None or method not in {"GET", "POST"}:
            return
        code = data.get("code") if isinstance(data, dict) else None
        self.flow.append(
            {
                "method": method,
                "path": path,
                "status": status,
                "code": code if isinstance(code, int) else None,
                "auth": scheme(headers),
                "query_keys": sorted(query(url)),
                "body_keys": keys(body),
                "response_keys": keys(data),
                "data_keys": keys(data.get("data")) if isinstance(data, dict) else [],
                "jwt_keys": keys(find_key(data, "user_jwt_authentication")),
            }
        )
        if path.endswith("/profile/verification/start"):
            # Records only whether Bolt demanded a bot challenge, never its contents.
            self.challenge_seen |= "challenge" in json.dumps(
                data
            ) or "challenge" in keys(body)
        template = {
            "base": base_url(url),
            "path": path,
            "method": method,
            "query": query(url),
            "headers": context_headers(headers),
        }
        rotated = find_key(data, "refresh_token")
        access = find_key(data, "access_token")
        # The web app receives its tokens in the OTP confirm response, not from authenticate.
        if path.endswith(
            ("/profile/verification/confirm", "/profile/eater/auth/authenticate")
        ) and isinstance(rotated, str):
            self.refresh_token = rotated
            self.access_token = access if isinstance(access, str) else None
        elif "/profile/eater/auth/getAccessToken" in path and self.refresh_token:
            carrier = token_header(headers, self.refresh_token)
            if carrier is not None:
                self.refresh = {
                    **template,
                    "token_header": carrier[0],
                    "token_prefix": carrier[1],
                }
            if isinstance(rotated, str):
                self.refresh_token = rotated
        elif path.endswith("/deliveryClient/getOrderHistory") and status == 200:
            self.history = template
            self.history_sample = data
        elif (
            path.endswith("/getOrderDetails")
            and status == 200
            and isinstance(body, dict)
        ):
            self.detail = {**template, "body": body}

    def complete(self) -> bool:
        return bool(self.refresh_token and self.history and self.detail)

    def session(self) -> dict[str, Any]:
        assert self.history is not None and self.detail is not None
        history_query = {
            name: value
            for name, value in self.history.get("query", {}).items()
            if name != "page_token"
        }
        history = {**self.history, "query": history_query}
        # Unobserved while the fresh token is valid; export verifies this bundle-derived guess.
        refresh = self.refresh or {
            **history,
            "path": "/profile/eater/auth/getAccessTokenWithRotation",
            "method": "POST",
            "query": {k: v for k, v in history_query.items() if k != "page_size"},
            "token_header": "authorization",
            "token_prefix": "Bearer ",
            "observed": False,
        }
        body = self.detail.get("body", {})
        # The order id is the detail body field whose history values are unique per row;
        # shared fields such as city_id also match but would collapse all orders into one.
        best: tuple[int, str, tuple[str, ...]] | None = None
        for key, value in body.items():
            path = find_path(self.history_sample, value)
            if path is None or "[]" not in path or path[-1] != key:
                continue
            found = values_at(self.history_sample, path)
            if len(set(map(str, found))) == len(found) and (
                best is None or len(found) > best[0]
            ):
                best = (len(found), key, path)
        if best is not None:
            return {
                "refresh": refresh,
                "history": history,
                "detail": {**self.detail, "id_key": best[1], "id_path": list(best[2])},
            }
        raise ExportError(
            "Order id from detail request not found in history; open an order"
        )


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
                raise ExportError("Cannot load Bolt Food; nothing recorded") from None
            print(
                "Přihlaste se ručně v okně Bolt Food (telefon a SMS kód zadávejte jen tam). "
                "Pak otevřete Objednávky a detail jedné objednávky.",
                flush=True,
            )
            deadline = time.monotonic() + timeout
            while not recorder.complete() and time.monotonic() < deadline:
                if page.is_closed():
                    break
                try:
                    page.wait_for_timeout(200)
                except BrowserError:
                    break  # Window closed by the user; report what is missing.
                while pending:
                    response = pending.pop(0)
                    request = response.request
                    if api_path(request.url) is None:
                        continue
                    try:
                        body = request.post_data_json if request.post_data else None
                    except ValueError:
                        body = None
                    try:
                        data = response.json()
                    except (BrowserError, ValueError):
                        data = None
                    recorder.observe(
                        request.method,
                        request.url,
                        request.all_headers(),
                        body,
                        response.status,
                        data,
                    )
        except BrowserError:
            raise ExportError("Browser login interrupted; rerun locally") from None
        finally:
            write_json(
                FLOW_FILE,
                {"challenge_seen": recorder.challenge_seen, "requests": recorder.flow},
            )
            browser.close()
    if not recorder.complete():
        missing = [
            name
            for name, value in (
                ("refresh_token", recorder.refresh_token),
                ("history_request", recorder.history),
                ("detail_request", recorder.detail),
            )
            if not value
        ]
        raise ExportError(
            f"Login incomplete, missing: {', '.join(missing)}; see {FLOW_FILE}"
        )
    session = recorder.session()
    # Fresh namespace on every login prevents different accounts sharing cached orders.
    secrets = {"refresh_token": recorder.refresh_token, "profile": uuid.uuid4().hex}
    vault.save(secrets)
    write_json(SESSION_FILE, session)
    # The short-lived access token stays in memory for an immediate probe export.
    return {**session, **secrets, "access_token": recorder.access_token}
