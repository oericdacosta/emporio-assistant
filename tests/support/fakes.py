"""Dublês de teste do agente: um modelo roteirizável e um conjunto de tools falso."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from emporio.llm.base import LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage

_ids = count(1)

Step = LLMResponse | Callable[[list[Message]], LLMResponse] | Exception


@dataclass
class LLMCall:
    messages: list[Message]
    tools: list[ToolSpec]
    tool_choice: str
    temperature: float
    max_tokens: int

    @property
    def system(self) -> str:
        return self.messages[0].content


class FakeLLM:
    """Devolve o roteiro na ordem. Chamar mais do que o previsto é erro de teste."""

    model = "fake-model"

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.calls: list[LLMCall] = []

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        self.calls.append(
            LLMCall(list(messages), list(tools), tool_choice, temperature, max_tokens)
        )
        if not self._steps:
            raise AssertionError(f"o agente fez a chamada {len(self.calls)}, mas o roteiro acabou")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(list(messages))
        return step

    def assert_exhausted(self) -> None:
        assert not self._steps, f"sobraram {len(self._steps)} passos do roteiro"


def say(text: str, usage: Usage = Usage(10, 5, 0)) -> LLMResponse:  # noqa: B008
    return LLMResponse(Message("assistant", text), usage, "fake-model")


def use_many(*calls: tuple[str, dict[str, Any]], text: str = "") -> LLMResponse:
    tool_calls = tuple(ToolCall(f"call-{next(_ids)}", name, args) for name, args in calls)
    return LLMResponse(Message("assistant", text, tool_calls), Usage(10, 5, 0), "fake-model")


def use(tool: str, /, **arguments: Any) -> LLMResponse:
    return use_many((tool, arguments))


def say_from_tool(
    compose: Callable[[dict[str, Any]], str],
) -> Callable[[list[Message]], LLMResponse]:
    """Um "modelo" que escreve a resposta a partir do que a última tool devolveu.

    O texto nasce do resultado real, então a guarda é exercitada de verdade e o teste não fixa
    preço de memória."""

    def step(messages: list[Message]) -> LLMResponse:
        last = next(m for m in reversed(messages) if m.role == "tool")
        return say(compose(json.loads(last.content).get("data", {})))

    return step


@dataclass
class StubToolbox:
    """Conjunto de tools falso: devolve resultados fixos por nome e registra as chamadas."""

    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, Any, Any]] = field(default_factory=list)
    raises: dict[str, Exception] = field(default_factory=dict)

    def specs(self) -> list[dict[str, Any]]:
        names = [*self.results, *self.raises]
        return [
            {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}
            for n in names
        ]

    def call(self, name: str, raw_args: Any, ctx: Any) -> dict[str, Any]:
        self.calls.append((name, raw_args, ctx))
        if name in self.raises:
            raise self.raises[name]
        return self.results[name]
