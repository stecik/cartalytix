"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
from pathlib import Path

from cartalytix.export import ExportError

from .api import Client
from .auth import SESSION_FILE, Vault, login
from .export import collect


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment Bolt Food"
    )
    parser.add_argument("command", choices=["login", "export", "probe", "logout"])
    parser.add_argument(
        "--browser", choices=["msedge", "chrome", "chromium"], default="chrome"
    )
    parser.add_argument(
        "--refresh-details",
        action="store_true",
        help="Re-fetch cached details to check order changes",
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        vault = Vault()
        if args.command == "logout":
            vault.clear()
            print(
                "Lokální token odstraněn. Exporty zůstávají; serverová relace není odvolána."
            )
            return
        secrets = vault.load()
        access: str | None = None
        if args.command == "login" or (args.command == "probe" and not secrets):
            access = login(vault, channel=args.browser).get("access_token")
            print(
                "Přihlášeno. Refresh token uložen ve Windows Credential Manageru.",
                flush=True,
            )
            secrets = vault.load()
        if args.command == "login":
            return
        if not secrets or not SESSION_FILE.exists():
            raise ExportError("Nejprve spusťte: uv run boltfood login")
        profile = secrets.get("profile", "")
        if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise ExportError("Invalid local profile; sign in again")
        session = {
            **json.loads(SESSION_FILE.read_text(encoding="utf-8")),
            **secrets,
            "access_token": access,
        }

        def save_refresh(token: str) -> None:
            vault.save({**secrets, "refresh_token": token})

        destination = Path("data") / "boltfood" / "CZ" / profile
        client = Client(session, save_refresh)
        try:
            report = collect(
                client,
                destination,
                (session.get("detail") or {}).get("id_path", []),
                refresh_details=args.refresh_details,
                progress=lambda text: print(text, flush=True),
            )
        finally:
            client.close()
        print(
            json.dumps(
                {k: v for k, v in report.items() if not k.endswith("_schema")},
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
