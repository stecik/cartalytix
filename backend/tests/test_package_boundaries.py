"""Application imports must remain independent from the scratchpad directory."""

import ast
from pathlib import Path


def test_application_never_imports_experimental_packages() -> None:
    package = Path(__file__).parents[1] / "src" / "cartalytix"
    forbidden = {"experiments", "http_login"}
    for source in package.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] not in forbidden for alias in node.names), (
                    source
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, source
