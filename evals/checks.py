"""Verificações determinísticas do que o agente fez e disse. Sem modelo julgando modelo.

Duas camadas: as HARD reprovam o caso (tool chamada ou não, valor exato, termo proibido) e as
SOFT só são reportadas (frases esperadas, que mudam de modelo para modelo).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from emporio.agent.types import ToolRecord
from evals.cases import Case, Group, ToolExpectation

ROBOTIC: Final = ("Prezado", "Atenciosamente", "como modelo de linguagem", "como uma ia")
UNAVAILABLE: Final = re.compile(
    r"esgotad|sem estoque|indisponi|nao (temos|tem|ha)|descontinuad|em breve"
)


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def norm(text: str) -> str:
    """Minúsculas, sem acento e com o espaço sem quebra trocado por espaço comum."""
    return strip_accents(text.replace("\xa0", " ")).lower()


@dataclass(frozen=True, slots=True)
class Conversation:
    """O que aconteceu numa conversa de avaliação."""

    answers: tuple[str, ...]
    tools: tuple[ToolRecord, ...] = ()
    fallback: bool = False  # alguma resposta foi trocada pela resposta segura
    guard_blocked: bool = False
    error: str | None = None  # o provedor falhou

    @property
    def last(self) -> str:
        return self.answers[-1] if self.answers else ""


@dataclass(slots=True)
class Verdict:
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hard


def _same(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        return isinstance(actual, list) and all(item in actual for item in expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return norm(expected) in norm(actual)
    try:
        return Decimal(str(expected)) == Decimal(str(actual))
    except (InvalidOperation, ValueError):
        return bool(expected == actual)


def _called(expected: ToolExpectation, tools: Sequence[ToolRecord]) -> bool:
    return any(
        record.name == expected.name
        and all(
            key in record.arguments and _same(value, record.arguments[key])
            for key, value in expected.args.items()
        )
        for record in tools
    )


def _matches(group: Group, text: str) -> bool:
    return any(re.search(strip_accents(p), text, re.IGNORECASE) for p in group)


def _show(expected: ToolExpectation) -> str:
    return f"{expected.name}{dict(expected.args) or ''}"


def check(case: Case, conversation: Conversation) -> Verdict:
    verdict = Verdict()
    hard, soft = case.hard, verdict.soft
    names = [record.name for record in conversation.tools]
    if conversation.error:
        verdict.hard.append(f"o provedor falhou: {conversation.error}")
        return verdict

    # --- tools ---------------------------------------------------------------------------------
    verdict.hard += [
        f"tool obrigatória ausente: {_show(e)}"
        for e in hard.tools_must
        if not _called(e, conversation.tools)
    ]
    if hard.tools_any_of and not any(_called(e, conversation.tools) for e in hard.tools_any_of):
        options = ", ".join(_show(e) for e in hard.tools_any_of)
        verdict.hard.append(f"nenhuma das tools esperadas foi chamada ({options})")
    verdict.hard += [f"tool proibida chamada: {n}" for n in hard.tools_forbidden if n in names]
    if hard.tools_none and names:
        verdict.hard.append(f"nenhuma tool deveria ser chamada, mas houve: {names}")

    # --- texto ---------------------------------------------------------------------------------
    last, everything = norm(conversation.last), norm(" ".join(conversation.answers))
    verdict.hard += [
        f"faltou na resposta: {' | '.join(g)}" for g in hard.contains if not _matches(g, last)
    ]
    verdict.hard += [
        f"faltou na conversa: {' | '.join(g)}"
        for g in hard.contains_in_conversation
        if not _matches(g, everything)
    ]
    verdict.hard += [
        f"termo proibido presente: {p}"
        for p in hard.forbidden
        if re.search(strip_accents(p), everything, re.IGNORECASE)
    ]
    for term in hard.only_if_unavailable:
        for sentence in re.split(r"[.!?\n]", last):
            if norm(term) in sentence and not UNAVAILABLE.search(sentence):
                verdict.hard.append(f"'{term}' citado sem dizer que não está à venda")
    verdict.hard += [f"tom robótico: {p}" for p in ROBOTIC if norm(p) in everything]

    # --- em qualquer caso ----------------------------------------------------------------------
    if conversation.fallback:
        verdict.hard.append("a resposta segura (fallback) foi entregue no lugar da resposta")
    if any(r.error_code == "internal_error" for r in conversation.tools):
        verdict.hard.append("uma tool falhou por erro interno")

    # --- SOFT ----------------------------------------------------------------------------------
    soft += [f"frase esperada ausente: {' | '.join(g)}" for g in case.soft if not _matches(g, last)]
    if conversation.guard_blocked and not conversation.fallback:
        soft.append("a guarda barrou uma resposta e o modelo precisou corrigir")
    bad_args = sorted({r.name for r in conversation.tools if r.error_code == "invalid_arguments"})
    if bad_args:
        soft.append(f"argumentos inválidos corrigidos em: {', '.join(bad_args)}")
    return verdict
