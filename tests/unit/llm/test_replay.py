from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from emporio.errors import CassetteError
from emporio.llm.base import LLMResponse, Message, ToolCall, ToolChoice, ToolSpec, Usage
from emporio.llm.replay import RecordingLLM, ReplayLLM, digest, load_cassette

TOOL = ToolSpec("get_product", "ficha", {"type": "object", "properties": {}})
SYSTEM = Message("system", "prompt")
USER = Message("user", "quanto custa o gd20?")
CALL = ToolCall("c1", "get_product", {"name": "gd20"})


class Scripted:
    model = "m-1"

    def __init__(self, *messages: Message) -> None:
        self._messages = list(messages)

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        *,
        tool_choice: ToolChoice = "auto",
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> LLMResponse:
        return LLMResponse(self._messages.pop(0), Usage(10, 2, 1), "m-1")


def cassette(recorder: RecordingLLM, prompt: str = "prompt") -> dict[str, Any]:
    return {
        "meta": {"model": "m-1", "prompt_sha256": digest(prompt)},
        "exchanges": json.loads(json.dumps(recorder.exchanges)),
    }


def record_conversation() -> tuple[RecordingLLM, list[Message]]:
    recorder = RecordingLLM(
        Scripted(Message("assistant", "", (CALL,)), Message("assistant", "R$ 2.199,00"))
    )
    first = [SYSTEM, USER]
    recorder.chat(first, [TOOL])
    second = [
        *first,
        Message("assistant", "", (CALL,)),
        Message("tool", '{"ok": true}', tool_call_id="c1"),
    ]
    recorder.chat(second, [TOOL])
    return recorder, second


def test_replay_returns_the_recorded_responses() -> None:
    recorder, second = record_conversation()
    replay = ReplayLLM(cassette(recorder))
    assert replay.model == "m-1"
    first = replay.chat([SYSTEM, USER], [TOOL])
    assert first.message.tool_calls == (CALL,) and first.usage == Usage(10, 2, 1)
    assert replay.chat(second, [TOOL]).message.content == "R$ 2.199,00"
    assert not replay.prompt_drift


def test_replay_fails_loudly_when_what_the_model_would_see_changes() -> None:
    recorder, second = record_conversation()
    replay = ReplayLLM(cassette(recorder))
    replay.chat([SYSTEM, USER], [TOOL])
    changed = [*second[:-1], Message("tool", '{"ok": true, "preco": "outro"}', tool_call_id="c1")]
    with pytest.raises(CassetteError, match="difere da gravada"):
        replay.chat(changed, [TOOL])


def test_replay_fails_when_the_agent_asks_for_more_calls_than_recorded() -> None:
    recorder, second = record_conversation()
    replay = ReplayLLM(cassette(recorder))
    replay.chat([SYSTEM, USER], [TOOL])
    replay.chat(second, [TOOL])
    with pytest.raises(CassetteError, match="mais chamadas"):
        replay.chat(second, [TOOL])


def test_rewriting_the_system_prompt_only_warns_unless_strict() -> None:
    recorder, _ = record_conversation()
    lenient = ReplayLLM(cassette(recorder, prompt="outro prompt"))
    lenient.chat([SYSTEM, USER], [TOOL])
    assert lenient.prompt_drift

    strict = ReplayLLM(cassette(recorder, prompt="outro prompt"), strict_prompt=True)
    with pytest.raises(CassetteError, match="prompt"):
        strict.chat([SYSTEM, USER], [TOOL])


def test_load_cassette_roundtrip_and_errors(tmp_path: Path) -> None:
    recorder, _ = record_conversation()
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cassette(recorder)), encoding="utf-8")
    assert load_cassette(path)["meta"]["model"] == "m-1"

    with pytest.raises(CassetteError, match="ilegível"):
        load_cassette(tmp_path / "nao-existe.json")
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CassetteError):
        load_cassette(path)
    path.write_text("[1]", encoding="utf-8")
    with pytest.raises(CassetteError):
        load_cassette(path)
