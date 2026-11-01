"""Remoção de dados pessoais de textos livres antes de gravá-los."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Final

EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# telefone brasileiro: DDD + 8 dígitos (fixo) ou 9 + 8 dígitos (celular)
PHONE: Final = re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-?\d{4}\b")

_PATTERNS: Final = (
    (re.compile(r"\b\d(?:[ -]?\d){12,15}\b"), "[cartao]"),  # antes do CPF e do telefone
    (EMAIL, "[email]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[cpf]"),
    (PHONE, "[telefone]"),
    (re.compile(r"\bBR[A-Z0-9]{9}BR\b"), "[rastreio]"),
)


def redact(text: str) -> str:
    """Troca cartão, e-mail, CPF, telefone e código de rastreio por marcadores."""
    for pattern, marker in _PATTERNS:
        text = pattern.sub(marker, text)
    return text


def extract_contact(texts: Sequence[str]) -> str | None:
    """E-mail ou telefone nas falas do cliente, da mais recente para a mais antiga."""
    for text in reversed(texts):
        if match := EMAIL.search(text) or PHONE.search(text):
            return match.group(0)
    return None


_SECRET_ARGUMENTS: Final = frozenset({"proof", "contact"})


def mask(value: Any) -> Any:
    """Máscara só para EXIBIR trace e detalhes na tela: dados pessoais e o que identifica o cliente."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: "***" if k in _SECRET_ARGUMENTS else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(item) for item in value]
    return value
