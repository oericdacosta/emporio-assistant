from __future__ import annotations

from pathlib import Path

from emporio.web.launch import ensure_env


def test_creates_the_env_from_the_example_and_asks_to_fill_it(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    env = tmp_path / ".env"
    assert ensure_env(env, example) is False
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=\n"


def test_keeps_an_existing_env_untouched(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=k\n", encoding="utf-8")
    assert ensure_env(env, tmp_path / ".env.example") is True
    assert env.read_text(encoding="utf-8") == "OPENAI_API_KEY=k\n"
