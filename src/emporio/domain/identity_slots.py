"""Preenchimento de slots de identidade em código: pedido e prova citados na fala do cliente.

O modelo não chama ``verify_customer`` de forma confiável quando o cliente já deu o pedido e a
prova; o orquestrador lê a fala e faz a chamada. Heurísticas conservadoras: na dúvida, ``None``.
"""

from __future__ import annotations

import re
from typing import Final

from emporio.domain.complaint import fold
from emporio.privacy import EMAIL, PHONE

MAX_ORDER_ID: Final = 999_999  # o mesmo teto de ``tools.orders``

# sobre texto dobrado. O grupo 1 é o número do pedido.
_NUM: Final = r"#?\s*(\d{1,6})(?![\d]|[.,/:-]\d)"
_ORDER_PATTERNS: Final = tuple(
    re.compile(p)
    for p in (
        rf"\bpedido\s*(?:n\S{{0,2}}\s*)?{_NUM}",
        rf"\bpedido\s+(?:e|eh)\s+(?:o\s+)?{_NUM}",
        r"#(\d{1,6})(?![\d]|[.,/:-]\d)",
        rf"\b(?:status|situacao|andamento|rastreio|rastreamento|cade)\s+(?:d?[oa]s?\s+)?{_NUM}",
        r"\bdo\s+#?(\d{1,6})\s*\?",
    )
)
# trechos que têm número mas não são pedido: valores em R$, datas, telefone
_NOT_ORDER: Final = (
    re.compile(r"r\$\s?[\d.,]+|\b\d[\d.,]*\s*(?:reais|real|mil)\b"),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(PHONE.pattern),
)
_PHONE_WORDS: Final = re.compile(r"telefone|celular|fone|final|ultimos|digitos|terminad")
_FOUR_DIGITS: Final = re.compile(r"(?<![\d#])(\d{4})(?![\d]|[.,/:-]\d)")


def extract_order_id(text: str) -> int | None:
    """O último pedido citado na fala, ou ``None``."""
    plain = fold(text)
    blocked = [m.span() for p in _NOT_ORDER for m in p.finditer(plain)]
    found: list[tuple[int, int]] = []
    for pattern in _ORDER_PATTERNS:
        for m in pattern.finditer(plain):
            start, end = m.span(1)
            number = int(m[1])
            if 0 < number <= MAX_ORDER_ID and not any(a < end and start < b for a, b in blocked):
                found.append((start, number))
    return max(found)[1] if found else None


def extract_proof(text: str) -> str | None:
    """A prova na fala: e-mail, ou 4 dígitos quando se fala de telefone, final ou últimos dígitos."""
    if email := EMAIL.search(text):
        return email.group(0)
    plain = fold(text)
    if not _PHONE_WORDS.search(plain):
        return None
    if phone := PHONE.search(plain):  # telefone inteiro: vale o final dele
        return re.sub(r"\D", "", phone.group(0))[-4:]
    plain = re.sub(r"pedido\s*(?:n\S{0,2}\s*)?#?\d+", " ", plain)  # "pedido 1234" não é prova
    match = _FOUR_DIGITS.search(plain)
    return match.group(1) if match else None
