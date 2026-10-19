"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
from pathlib import Path

from cartalytix.export import ExportError

from .auth import FLOW_FILE, Vault, login
from .export import Client, collect


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment Globus"
    )
    parser.add_argument("command", choices=["login", "export", "logout"])
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        help="Re-download cached receipt PDFs",
    )
    parser.add_argument(
        "--browser", choices=["msedge", "chrome", "chromium"], default="chrome"
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        vault = Vault()
        if args.command == "logout":
            vault.clear()
            print("Lokální cookie odstraněna. Serverová relace není odvolána.")
            return
        if args.command == "login":
            found = login(vault, channel=args.browser)
            print(json.dumps(found, ensure_ascii=False, indent=2))
            print(f"Přihlášeno. Tok a schémata účtenek: {FLOW_FILE}", flush=True)
            return
        secrets = vault.load() or {}
        cookie = str(secrets.get("session_cookie") or "")
        if not cookie:
            raise ExportError("Nejprve spusťte: uv run globus login")
        profile = str(secrets.get("profile", ""))
        if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise ExportError("Invalid local profile; sign in again")
        destination = Path("data") / "globus" / "CZ" / profile
        client = Client(cookie)
        try:
            report = collect(
                client,
                destination,
                refresh_details=args.refresh_details,
                progress=lambda text: print(text, flush=True),
            )
        finally:
            client.close()
        summary = {k: v for k, v in report.items() if not k.endswith("_schema")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("Export:", destination.resolve())
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Přerušeno.", file=sys.stderr)
        sys.exit(130)
    except Exception:  # noqa: BLE001 - Security boundary: third-party errors may contain tokens.
        print(
            "Lokální operace selhala. Žádné přihlašovací údaje nebyly vypsány. Zkontrolujte dostupnost Credential Manageru, souborů a prohlížeče.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
