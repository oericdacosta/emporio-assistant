"""Remoção de dados pessoais de textos livres antes de gravá-los."""

from __future__ import annotations

import re
from typing import Final

_PATTERNS: Final = (
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[cpf]"),
    (re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b"), "[telefone]"),
    (re.compile(r"\bBR[A-Z0-9]{9}BR\b"), "[rastreio]"),
)


def redact(text: str) -> str:
    """Troca e-mail, CPF, telefone e código de rastreio por marcadores."""
    for pattern, marker in _PATTERNS:
        text = pattern.sub(marker, text)
    return text
