"""Tool certa para o assunto: frete e horário só se sustentam com a tool que os responde.

Mapa conservador intenção -> tools aceitas. Se a fala do cliente tem a intenção, o rascunho afirma
algo e nenhuma tool aceita rodou NO TURNO, é violação (``get_store_info`` não responde frete).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Final

from emporio.agent.claims import about_conversation, needs_check
from emporio.domain.complaint import fold

# tools que respondem outro assunto (pedido, reclamação): "enviado" não é pergunta de frete
_OTHER_SUBJECT: Final = frozenset(
    {"verify_customer", "get_order_status", "check_return_eligibility", "register_complaint"}
)
_INTENTS: Final = (
    (
        re.compile(r"frete|entreg|envi|mand(?:am|a)|prazo de entrega|correios|sedex|pac\b|jadlog"),
        frozenset({"estimate_shipping", "search_policy"}),
        "Para frete e prazo de entrega consulte estimate_shipping ou "
        "search_policy(frete_entrega); get_store_info não responde isso.",
    ),
    (
        re.compile(r"abre|fecha|horario|funcionamento|aberto|aberta|fechado"),
        frozenset({"get_store_info", "search_policy"}),
        "Para horário de funcionamento consulte get_store_info ou search_policy.",
    ),
)


def wrong_tool_correction(user_text: str, draft: str, tools: Collection[str]) -> str | None:
    """A correção a dar ao modelo, ou ``None`` se o rascunho está sustentado (ou não afirma nada)."""
    if about_conversation(user_text) or _OTHER_SUBJECT & set(tools) or not needs_check(draft):
        return None
    plain = fold(user_text)
    for pattern, accepted, correction in _INTENTS:
        if pattern.search(plain) and not accepted & set(tools):
            return correction
    return None
