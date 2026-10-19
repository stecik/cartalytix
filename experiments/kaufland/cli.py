"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx

from cartalytix.export import ExportError

from .api import renew
from .auth import Vault, login
from .export import Client, collect


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment Kaufland Card"
    )
    parser.add_argument(
        "command", choices=["login", "export", "refresh-check", "logout"]
    )
    parser.add_argument("--max-pages", type=int, default=1000)
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
            tokens = login(vault, channel=args.browser)
            print(
                f"Přihlášeno. Platnost access tokenu: {tokens.get('expires_in')} s",
                flush=True,
            )
            return
        secrets = vault.load()
        if not secrets:
            raise ExportError("Nejprve spusťte: uv run kaufland login")

        def save(token: str) -> None:
            secrets["refresh_token"] = token
            vault.save(secrets)

        if args.command == "refresh-check":
            with httpx.Client(timeout=30, follow_redirects=False) as http:
                renew(http, str(secrets.get("refresh_token", "")), save)
            print("Obnova tokenu funguje.")
            return
        profile = str(secrets.get("profile", ""))
        if len(profile) != 32 or any(c not in "0123456789abcdef" for c in profile):
            raise ExportError("Invalid local profile; sign in again")
        destination = Path("data") / "kaufland" / "CZ" / profile
        client = Client(
            str(secrets.get("customer", "")),
            str(secrets.get("refresh_token", "")),
            save,
        )
        try:
            report = collect(
                client,
                destination,
                max_pages=args.max_pages,
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
