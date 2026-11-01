"""O agente não conhece as tools concretas: quem monta o conjunto é a camada de cima."""

from __future__ import annotations

import ast
from pathlib import Path

AGENT = Path(__file__).resolve().parents[3] / "src" / "emporio" / "agent"


def test_agent_package_does_not_import_the_concrete_tools() -> None:
    offenders = []
    for path in AGENT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            offenders += [
                f"{path.name}: {name}" for name in names if name.startswith("emporio.tools")
            ]
    assert offenders == []
