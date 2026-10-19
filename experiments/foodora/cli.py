"""Commands intentionally accept no credentials or tokens."""

import argparse
import json
import logging
import sys

from cartalytix.export import ExportError

from .export import export


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cartalytix: lokální experiment foodora.cz"
    )
    parser.add_argument("command", choices=["export"])
    parser.add_argument(
        "--browser", choices=["msedge", "chrome", "chromium"], default="chrome"
    )
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        report, destination = export(
            channel=args.browser, progress=lambda text: print(text, flush=True)
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("Export:", destination.resolve())
        if not report.get("complete"):
            sys.exit(1)
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Přerušeno.", file=sys.stderr)
        sys.exit(130)
    except Exception:  # noqa: BLE001 - Security boundary: third-party errors may contain tokens.
        print(
            "Lokální operace selhala. Žádné přihlašovací údaje nebyly vypsány.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
