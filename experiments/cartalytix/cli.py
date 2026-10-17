"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .api import APP_VERSION, Client
from .auth import Vault, login
from .export import ExportError, collect
from .fermato import export as fermato_export


def main():
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment Lidl Plus CZ"
    )
    parser.add_argument(
        "command", choices=["login", "export", "probe", "logout", "fermato"]
    )
    parser.add_argument(
        "--browser", choices=["msedge", "chrome", "chromium"], default="chrome"
    )
    parser.add_argument("--app-version", default=APP_VERSION)
    parser.add_argument("--max-pages", type=int, default=10000)
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        help="Re-fetch cached details to check returns/corrections",
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.max_pages < 1:
        parser.error("--max-pages must be positive")
    try:
        if args.command == "fermato":
            report, destination = fermato_export(
                channel=args.browser,
                max_pages=args.max_pages,
                refresh_details=args.refresh_details,
                progress=lambda text: print(text, flush=True),
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("Export:", destination.resolve())
            if not report["complete"]:
                sys.exit(1)
            return
        vault = Vault()
        if args.command == "logout":
            vault.clear()
            print(
                "Lokální token odstraněn. Exporty zůstávají; serverová relace není odvolána."
            )
            return
        tokens = vault.load()
        if args.command == "login" or (args.command == "probe" and not tokens):
            tokens = login(vault, channel=args.browser)
            print(
                "Přihlášeno. Refresh token uložen ve Windows Credential Manageru.",
                flush=True,
            )
        if args.command == "login":
            return
        if not tokens:
            raise ExportError("Nejprve spusťte: uv run cartalytix login")
        profile = tokens.get("profile", "")
        if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise ExportError("Invalid local profile; sign in again")
        destination = Path("data") / "CZ" / profile
        client = Client(tokens, vault.save, app_version=args.app_version)
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
                {
                    k: report[k]
                    for k in (
                        "complete",
                        "unique_receipts",
                        "oldest_receipt",
                        "newest_receipt",
                        "downloaded_details",
                        "cached_details",
                        "quality_issues",
                    )
                },
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
        # Never print third-party exceptions: they can contain OAuth URLs or response bodies.
        print(
            "Lokální operace selhala. Žádné přihlašovací údaje nebyly vypsány. Zkontrolujte dostupnost Credential Manageru, souborů a prohlížeče.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
