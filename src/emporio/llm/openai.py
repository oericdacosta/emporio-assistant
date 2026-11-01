"""Cliente da API de chat da OpenAI."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langsmith.wrappers import wrap_openai
from openai import OpenAI

from emporio.llm.base import LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage
from emporio.llm.errors_map import translate

TIMEOUT_SECONDS = 30
MAX_RETRIES = 2


def _to_wire(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["content"] = message.content or None
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return wire


def _arguments(raw: str) -> dict[str, Any]:
    """Argumentos da chamada. JSON quebrado vira um campo inválido que a tool recusa, e o
    modelo recebe o erro e tenta de novo."""
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {"_invalid_json": raw}
    return parsed if isinstance(parsed, dict) else {"_invalid_json": raw}


class OpenAIClient:
    def __init__(self, model: str, *, api_key: str, client: Any = None) -> None:
        self.model = model
        self._client = client or wrap_openai(  # no-op com o tracing desligado
            OpenAI(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)
        )

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            params["tool_choice"] = tool_choice
        try:
            response = self._client.chat.completions.create(**params)
        except Exception as error:
            raise translate(error) from error

        choice = response.choices[0].message
        calls = tuple(
            ToolCall(c.id, c.function.name, _arguments(c.function.arguments))
            for c in choice.tool_calls or ()
        )
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        return LLMResponse(
            Message("assistant", choice.content or "", calls),
            Usage(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
                getattr(details, "cached_tokens", 0) or 0,
            ),
            response.model or self.model,
        )
