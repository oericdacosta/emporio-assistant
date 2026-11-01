"""Gravação e reprodução de conversas, para ``emporio --demo`` e para os exemplos do README.

O cassete guarda só as respostas do LLM. As tools, o relógio e a guarda de fundamentação rodam
de verdade na reprodução: se o que o modelo veria mudar, a reprodução falha em vez de mostrar
uma conversa que o sistema não produz mais.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from emporio.errors import CassetteError
from emporio.llm.base import (
    LLMClient,
    LLMResponse,
    Message,
    ToolCall,
    ToolChoice,
    ToolSpec,
    Usage,
)


def _message_to_json(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
        ],
    }


def _message_from_json(data: dict[str, Any]) -> Message:
    calls = tuple(ToolCall(c["id"], c["name"], c["arguments"]) for c in data["tool_calls"])
    return Message(data["role"], data["content"], calls, data["tool_call_id"])


def digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def request_sha(messages: Sequence[Message], tools: Sequence[ToolSpec], tool_choice: str) -> str:
    """Impressão digital do pedido sem o prompt de sistema: reescrever o prompt não invalida o
    cassete (só gera aviso), mas mudar o que as tools devolveram, sim."""
    return digest(
        {
            "messages": [_message_to_json(m) for m in messages if m.role != "system"],
            "tools": sorted(t.name for t in tools),
            "tool_choice": tool_choice,
        }
    )


def load_cassette(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["meta"]["model"], data["meta"]["prompt_sha256"], data["exchanges"]  # noqa: B018
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise CassetteError(
            f"cassete ilegível ou ausente: {path} ({type(error).__name__}). "
            "Grave de novo com o gravador de conversas."
        ) from error
    if not isinstance(data, dict):
        raise CassetteError(f"cassete ilegível: {path}")
    return data


def build_cassette(
    recorder: RecordingLLM,
    runs: list[dict[str, Any]],
    *,
    recorded_on: str,
    commit: str = "",
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Monta o cassete de uma gravação. Cada ``run`` é ``{"label", "now", "turns": [texto, ...]}``:
    uma conversa com o relógio fixo em ``now``, na ordem em que foi gravada."""
    return {
        "meta": {
            "model": recorder.model,
            "prompt_sha256": recorder.prompt_sha,
            "recorded_on": recorded_on,
            "commit": commit,
            "temperature": temperature,
            "runs": runs,
        },
        "exchanges": recorder.exchanges,
    }


def save_cassette(path: Path, cassette: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cassette, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RecordingLLM:
    """Envolve um cliente real e guarda cada resposta dele."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.model = inner.model
        self.exchanges: list[dict[str, Any]] = []
        self.prompt_sha = ""

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        response = self.inner.chat(
            messages, tools, tool_choice=tool_choice, temperature=temperature, max_tokens=max_tokens
        )
        self.prompt_sha = self.prompt_sha or digest(messages[0].content)
        usage = response.usage
        self.exchanges.append(
            {
                "request_sha": request_sha(messages, tools, tool_choice),
                "message": _message_to_json(response.message),
                "usage": [usage.input_tokens, usage.output_tokens, usage.cached_tokens],
                "model": response.model,
            }
        )
        return response


class ReplayLLM:
    def __init__(self, cassette: dict[str, Any], *, strict_prompt: bool = False) -> None:
        self.model: str = cassette["meta"]["model"]
        self._exchanges: list[dict[str, Any]] = cassette["exchanges"]
        self._next = 0
        self._strict = strict_prompt
        self._expected_prompt: str = cassette["meta"]["prompt_sha256"]
        self.prompt_drift = False  # o prompt de sistema mudou desde a gravação

    @property
    def remaining(self) -> int:
        """Respostas gravadas que o agente ainda não pediu."""
        return len(self._exchanges) - self._next

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        if self._next == 0 and digest(messages[0].content) != self._expected_prompt:
            self.prompt_drift = True
            if self._strict:
                raise CassetteError(
                    "o prompt de sistema mudou desde a gravação (modo estrito). Grave de novo."
                )
        if self._next >= len(self._exchanges):
            raise CassetteError(
                "o agente fez mais chamadas ao LLM do que as gravadas. Grave de novo."
            )
        exchange = self._exchanges[self._next]
        if exchange["request_sha"] != request_sha(messages, tools, tool_choice):
            raise CassetteError(
                f"a chamada {self._next + 1} ao LLM difere da gravada (resultado de tool, "
                "histórico ou número de turnos mudou). Grave de novo."
            )
        self._next += 1
        return LLMResponse(
            _message_from_json(exchange["message"]), Usage(*exchange["usage"]), exchange["model"]
        )
