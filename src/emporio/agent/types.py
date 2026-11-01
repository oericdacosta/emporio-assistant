"""Tipos que todo motor de agente compartilha: configuração, eventos, resultado do turno.

O grafo LangGraph (``graph.py``) e os passos que ele compartilha (``steps.py``) usam estes tipos;
a CLI, os evals e os testes também os usam para ler o resultado de um turno.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from emporio.llm.base import LLMResponse, Message, Usage

EMPTY_REPLY_MESSAGE: Final = "Sua resposta veio vazia. Responda ao cliente com o que você já sabe."
FORCE_ANSWER_MESSAGE: Final = (
    "Você já consultou o que precisava. Responda agora ao cliente com as informações que tem; "
    "se faltar algum dado, diga que vai confirmar com a equipe."
)


class Toolbox[C](Protocol):
    """O que o agente precisa de um conjunto de tools. ``C`` é o contexto de cada chamada."""

    def specs(self) -> list[dict[str, Any]]: ...

    def call(self, name: str, raw_args: Any, ctx: C) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AgentConfig:
    max_iterations: int = 5  # a última não oferece tools
    max_calls_per_step: int = 3
    max_tokens: int = 600
    temperature: float = 0.2


class Outcome(StrEnum):
    ANSWERED = "answered"
    CORRECTED = "corrected"  # a guarda barrou a primeira resposta e a segunda passou
    FALLBACK = "fallback"  # o cliente recebeu a resposta segura


@dataclass(frozen=True, slots=True)
class ToolStarted:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolFinished:
    name: str
    ok: bool
    seconds: float
    result: dict[str, Any]  # exatamente o que o modelo viu


Event = ToolStarted | ToolFinished
EventHook = Callable[[Event], None]


@dataclass(frozen=True, slots=True)
class ToolRecord:
    name: str
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None
    seconds: float
    result_chars: int


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    outcome: Outcome
    new_messages: tuple[Message, Message]  # a fala do cliente e a resposta, para gravar
    tools: tuple[ToolRecord, ...]
    usage: Usage
    llm_calls: int
    seconds: float
    trace_id: str
    guard_blocked: bool  # a guarda barrou alguma resposta neste turno

    @property
    def fallback(self) -> bool:
        return self.outcome is Outcome.FALLBACK


class Meter:
    """Soma o que o turno consumiu."""

    def __init__(self) -> None:
        self.input_tokens = self.output_tokens = self.cached_tokens = self.calls = 0
        self.tools: list[ToolRecord] = []
        self.guard_blocked = False

    def add(self, response: LLMResponse) -> None:
        self.calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        self.cached_tokens += response.usage.cached_tokens

    @property
    def usage(self) -> Usage:
        return Usage(self.input_tokens, self.output_tokens, self.cached_tokens)


def failure(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}
