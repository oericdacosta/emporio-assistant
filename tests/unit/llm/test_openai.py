from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from emporio.errors import ProviderUnavailable
from emporio.llm.base import Message, ToolCall, ToolSpec
from emporio.llm.openai import OpenAIClient

TOOL = ToolSpec("get_product", "ficha", {"type": "object", "properties": {}})
HISTORY = [
    Message("system", "prompt"),
    Message("user", "quanto custa?"),
    Message("assistant", "", (ToolCall("c1", "get_product", {"name": "gd20"}),)),
    Message("tool", '{"ok": true}', tool_call_id="c1"),
]


class FakeCompletions:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error
        self.sent: dict[str, Any] = {}

    def create(self, **params: Any) -> Any:
        self.sent = params
        if self.error:
            raise self.error
        return self.response


def sdk(response: Any = None, error: Exception | None = None) -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions(response, error)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def reply(
    content: str | None = "oi", tool_calls: Any = None, usage: Any = None, model: str = "m-1"
) -> Any:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model=model)


def tool_call(arguments: str) -> Any:
    return SimpleNamespace(
        id="c9", function=SimpleNamespace(name="get_product", arguments=arguments)
    )


def test_text_reply_and_usage_with_cached_tokens() -> None:
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=8,
        prompt_tokens_details=SimpleNamespace(cached_tokens=100),
    )
    fake, _ = sdk(reply("olá!", usage=usage))
    response = OpenAIClient("m", api_key="k", client=fake).chat(HISTORY)
    assert response.message == Message("assistant", "olá!")
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 8)
    assert response.usage.cached_tokens == 100 and response.model == "m-1"


def test_response_without_usage_counts_zero() -> None:
    fake, _ = sdk(reply(usage=None))
    response = OpenAIClient("m", api_key="k", client=fake).chat(HISTORY)
    assert (response.usage.input_tokens, response.usage.output_tokens) == (0, 0)


def test_tool_calls_are_parsed() -> None:
    fake, _ = sdk(reply(None, [tool_call('{"name": "gd20"}')]))
    message = OpenAIClient("m", api_key="k", client=fake).chat(HISTORY, [TOOL]).message
    assert message.content == "" and message.tool_calls == (
        ToolCall("c9", "get_product", {"name": "gd20"}),
    )


@pytest.mark.parametrize("raw", ["{quebrado", "[1, 2]"])
def test_broken_arguments_become_an_invalid_field_for_the_tool_to_refuse(raw: str) -> None:
    fake, _ = sdk(reply(None, [tool_call(raw)]))
    (call,) = OpenAIClient("m", api_key="k", client=fake).chat(HISTORY, [TOOL]).message.tool_calls
    assert call.arguments == {"_invalid_json": raw}


def test_request_format() -> None:
    fake, completions = sdk(reply())
    OpenAIClient("m", api_key="k", client=fake).chat(
        HISTORY, [TOOL], tool_choice="none", temperature=0.0, max_tokens=50
    )
    sent = completions.sent
    assert sent["messages"][2]["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "get_product", "arguments": json.dumps({"name": "gd20"})},
        }
    ]
    assert sent["messages"][2]["content"] is None
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}
    assert sent["tools"][0]["function"]["name"] == "get_product"
    assert sent["tool_choice"] == "none" and sent["temperature"] == 0.0
    assert sent["max_completion_tokens"] == 50 and "max_tokens" not in sent


def test_no_tools_means_no_tool_params() -> None:
    fake, completions = sdk(reply())
    OpenAIClient("m", api_key="k", client=fake).chat(HISTORY)
    assert "tools" not in completions.sent and "tool_choice" not in completions.sent


def test_sdk_errors_are_translated() -> None:
    rate = openai.RateLimitError(
        "msg",
        response=httpx.Response(429, request=httpx.Request("POST", "https://x.test")),
        body=None,
    )
    fake, _ = sdk(error=rate)
    with pytest.raises(ProviderUnavailable) as raised:
        OpenAIClient("m", api_key="k", client=fake).chat(HISTORY)
    assert raised.value.rate_limited
