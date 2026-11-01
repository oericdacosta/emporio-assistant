from __future__ import annotations

import re
from pathlib import Path

import pytest

from emporio.errors import ConfigError
from emporio.llm.factory import make_llm
from emporio.llm.openai import OpenAIClient


def test_model_is_required_and_has_no_default() -> None:
    with pytest.raises(ConfigError, match="LLM_MODEL") as raised:
        make_llm(None, env={"OPENAI_API_KEY": "k"})
    assert raised.value.exit_code == 3


def test_missing_key_names_the_variable_and_points_to_the_demo() -> None:
    with pytest.raises(ConfigError, match=r"(?s)OPENAI_API_KEY.*--demo"):
        make_llm("m", env={})


def test_model_comes_from_the_argument_or_the_environment() -> None:
    env = {"OPENAI_API_KEY": "k", "LLM_MODEL": "do-ambiente"}
    from_env = make_llm(None, env=env)
    assert isinstance(from_env, OpenAIClient) and from_env.model == "do-ambiente"
    assert make_llm("do-argumento", env=env).model == "do-argumento"


def test_no_model_name_is_hardcoded_in_the_source() -> None:
    source = Path(__file__).resolve().parents[3] / "src"
    names = re.compile(r"\b(claude-|gpt-|gemini-\d|llama|mixtral|qwen)", re.IGNORECASE)
    offenders = [
        path.name for path in source.rglob("*.py") if names.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
