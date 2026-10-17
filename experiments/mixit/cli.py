"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from cartalytix.export import ExportError

from .api import Client
from .auth import Vault, login, token_expiry
from .export import collect


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment Mixit.cz"
    )
    parser.add_argument("command", choices=["login", "export", "probe", "logout"])
    parser.add_argument(
        "--browser", choices=["msedge", "chrome", "chromium"], default="chrome"
    )
    parser.add_argument("--max-pages", type=int, default=10000)
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        help="Re-fetch cached details to check order state changes",
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.max_pages < 1:
        parser.error("--max-pages must be positive")
    try:
        vault = Vault()
        if args.command == "logout":
            vault.clear()
            print(
                "Lokální token odstraněn. Exporty zůstávají; serverová relace není odvolána."
            )
            return
        session = vault.load()
        if args.command == "login" or (args.command == "probe" and not session):
            session = login(vault, channel=args.browser)
            print(
                "Přihlášeno. Token uložen ve Windows Credential Manageru.", flush=True
            )
        if args.command == "login":
            return
        if not session:
            raise ExportError("Nejprve spusťte: uv run mixit login")
        expires = token_expiry(session.get("token", ""))
        if expires is not None and expires <= time.time():
            raise ExportError("Token vypršel; spusťte: uv run mixit login")
        profile = session.get("profile", "")
        if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise ExportError("Invalid local profile; sign in again")
        destination = Path("data") / "mixit" / "CZ" / profile
        client = Client(session)
        try:
            report = collect(
                client,
                destination,
                max_pages=args.max_pages,
                refresh_details=args.refresh_details,
                progress=lambda text: print(text, flush=True),
            )
        finally:
            client.close()
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "schema"},
                ensure_ascii=False,
                indent=2,
            )
        )
        print("Export:", destination.resolve())
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Přerušeno. Další export naváže z uložených detailů.", file=sys.stderr)
        sys.exit(130)
    except Exception:  # noqa: BLE001 - Security boundary: third-party errors may contain tokens.
        print(
            "Lokální operace selhala. Žádné přihlašovací údaje nebyly vypsány. Zkontrolujte dostupnost Credential Manageru, souborů a prohlížeče.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
