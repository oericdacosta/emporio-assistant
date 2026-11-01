"""Leitura de um arquivo ``.env`` simples (``CHAVE=valor``), sem dependência externa.

Não sobrescreve variável que já exista no ambiente, e nunca imprime valores.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            values[key] = value
    return values


def load(path: Path, env: MutableMapping[str, str] | None = None) -> None:
    """Coloca no ambiente o que o arquivo define e o ambiente ainda não tem."""
    target = os.environ if env is None else env
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for key, value in parse(text).items():
        target.setdefault(key, value)
