"""Offline quality report. Accepts only a local export directory, never credentials."""

import argparse
import json
from pathlib import Path

from cartalytix.audit import audit

parser = argparse.ArgumentParser(description="Offline audit Cartalytix JSON exportu")
parser.add_argument("directory", nargs="?", type=Path)
args = parser.parse_args()
directory = args.directory
if directory is None:
    profiles = list(Path("data/CZ").glob("*/index.json"))
    if len(profiles) != 1:
        parser.error("Zadejte adresář data/CZ/<profil> (žádný nebo více profilů).")
    directory = profiles[0].parent
report = audit(directory)
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report["quality_passed"] else 2)
