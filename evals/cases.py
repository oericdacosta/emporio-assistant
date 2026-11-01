"""Os casos de referência: o formato, a leitura e a validação de ``golden.yaml``."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from evals.facts import Facts

DEFAULT_NOW: Final = "2026-03-23T10:00:00-04:00"  # segunda-feira, depois do último pedido dos dados

Group = tuple[str, ...]  # padrões alternativos: basta casar um

# regression: o que o agente já acerta; precisa estar em 100% e quebra a execução se cair.
# capability: os casos difíceis, ainda não dominados; a taxa mostra o quanto falta.
SUITES: Final = ("regression", "capability")


@dataclass(frozen=True, slots=True)
class ToolExpectation:
    name: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Hard:
    """O que reprova o caso."""

    tools_must: tuple[ToolExpectation, ...] = ()
    tools_any_of: tuple[ToolExpectation, ...] = ()
    tools_forbidden: tuple[str, ...] = ()
    tools_none: bool = False
    contains: tuple[Group, ...] = ()  # na última resposta
    contains_in_conversation: tuple[Group, ...] = ()  # em alguma resposta da conversa
    forbidden: tuple[str, ...] = ()  # em qualquer resposta da conversa
    only_if_unavailable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    intent: str
    turns: tuple[str, ...]
    now: str = DEFAULT_NOW
    suite: str = "regression"
    hard: Hard = field(default_factory=Hard)
    soft: tuple[Group, ...] = ()  # frases esperadas; só são reportadas
    assume: tuple[Mapping[str, Any], ...] = ()


class CaseError(ValueError):
    """O golden está mal formado."""


def _groups(raw: Sequence[Any], facts: Facts) -> tuple[Group, ...]:
    groups: list[Group] = []
    for item in raw:
        patterns = [item] if isinstance(item, str) else list(item)
        expanded = tuple(facts.expand(p) for p in patterns)
        for pattern in expanded:
            re.compile(pattern)
        groups.append(expanded)
    return tuple(groups)


def _patterns(raw: Sequence[str], facts: Facts) -> tuple[str, ...]:
    expanded = tuple(facts.expand(p) for p in raw)
    for pattern in expanded:
        re.compile(pattern)
    return expanded


def _tools(raw: Sequence[Mapping[str, Any]]) -> tuple[ToolExpectation, ...]:
    return tuple(ToolExpectation(item["name"], dict(item.get("args", {}))) for item in raw)


def parse_case(raw: Mapping[str, Any], facts: Facts) -> Case:
    try:
        hard = raw.get("hard", {})
        tools = hard.get("tools", {})
        suite = raw.get("suite", "regression")
        if suite not in SUITES:
            raise CaseError(f"suite {suite!r} desconhecida (use {' ou '.join(SUITES)})")
        return Case(
            id=raw["id"],
            intent=raw["intent"],
            turns=tuple(raw["turns"]),
            now=raw.get("now", DEFAULT_NOW),
            suite=suite,
            hard=Hard(
                tools_must=_tools(tools.get("must", ())),
                tools_any_of=_tools(tools.get("any_of", ())),
                tools_forbidden=tuple(tools.get("forbidden", ())),
                tools_none=bool(tools.get("none", False)),
                contains=_groups(hard.get("contains", ()), facts),
                contains_in_conversation=_groups(hard.get("contains_in_conversation", ()), facts),
                forbidden=_patterns(hard.get("forbidden", ()), facts),
                only_if_unavailable=tuple(hard.get("only_if_unavailable", ())),
            ),
            soft=_groups(raw.get("soft", {}).get("phrases", ()), facts),
            assume=tuple(raw.get("assume", ())),
        )
    except CaseError:
        raise
    except (KeyError, TypeError, re.error) as error:
        raise CaseError(f"caso {raw.get('id', '?')} mal formado: {error!r}") from error


def load_cases(path: Path, facts: Facts) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [parse_case(item, facts) for item in raw]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise CaseError("há ids repetidos no golden")
    if any(not case.turns for case in cases):
        raise CaseError("há caso sem nenhuma fala do cliente")
    return cases
