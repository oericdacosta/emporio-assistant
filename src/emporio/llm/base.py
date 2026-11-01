"""Tipos e contrato do cliente de LLM."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]
ToolChoice = Literal["auto", "none", "required"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()  # só em mensagens do assistente
    tool_call_id: str | None = None  # só em mensagens de resultado de tool


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema dos argumentos


@dataclass(frozen=True, slots=True)
class Usage:
    """``input_tokens`` conta todo o prompt; ``cached_tokens`` é a parte lida do cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    message: Message
    usage: Usage
    model: str


class LLMClient(Protocol):
    model: str

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        """``messages[0]`` é o prompt de sistema. ``tool_choice="none"`` proíbe chamar tools; ``"required"`` obriga."""
        ...
