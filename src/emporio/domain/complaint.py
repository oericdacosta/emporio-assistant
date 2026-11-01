"""Palavras de queixa e dobra de texto, compartilhadas pelo estado do agente e pela tool."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

# sobre texto já dobrado (minúsculas, sem acento)
COMPLAINT_WORDS: Final = re.compile(
    r"defeito|quebrad[ao]|quebrou|riscad[ao]|arranh|nao liga|nao funciona|parou de funcionar"
    r"|danificad|avariad|chegou (?:assim|errado|quebrad|com problema)"
    r"|veio (?:errado|com defeito|riscad|quebrad)|reclama"
)


def fold(text: str) -> str:
    """Minúsculas e sem acento."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def complaint_terms(text: str) -> set[str]:
    return set(COMPLAINT_WORDS.findall(fold(text)))
