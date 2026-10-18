"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
from pathlib import Path

from cartalytix.export import ExportError

from .api import renew
from .auth import FLOW_FILE, SESSION_FILE, Vault, login
from .export import Client, collect


def main() -> None:
    parser = argparse.ArgumentParser(description="Cartalytix: lokální experiment Wolt")
    parser.add_argument(
        "command", choices=["login", "export", "refresh-check", "logout"]
    )
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        help="Re-fetch cached details to check order changes",
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
            print("Lokální token odstraněn. Serverová relace není odvolána.")
            return
        if args.command == "login":
            login(vault, channel=args.browser)
            print(f"Přihlášeno. Tok a schémata objednávek: {FLOW_FILE}", flush=True)
            return
        secrets = vault.load()
        if not secrets or not SESSION_FILE.exists():
            raise ExportError("Nejprve spusťte: uv run wolt login")
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        template = session.get("refresh") or {}
        if args.command == "export":
            profile = str(secrets.get("profile", ""))
            if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
                raise ExportError("Invalid local profile; sign in again")
            destination = Path("data") / "wolt" / "CZ" / profile
            client = Client(
                session,
                str(secrets.get("refresh_token", "")),
                lambda token: vault.save({**secrets, "refresh_token": token}),
            )
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
            return
        renew(
            template,
            str(secrets.get("refresh_token", "")),
            lambda token: vault.save({**secrets, "refresh_token": token}),
        )
        print(
            f"Obnova tokenu funguje (šablona pozorována: {template.get('observed')})."
        )
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
