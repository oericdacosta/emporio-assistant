from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest

from emporio import envfile
from emporio.cli.text import format_moment, sanitize, scrub
from emporio.clock import TZ


def test_sanitize_removes_control_and_direction_characters() -> None:
    assert sanitize("oi\x00 tudo‮ bem\x07") == "oi tudo bem"
    assert sanitize("  várias\n linhas\t aqui  ") == "várias linhas aqui"
    assert sanitize("\x00​") == ""


def test_scrub_masks_long_tokens_and_cuts_the_text() -> None:
    assert scrub("chave sk-abcdefghijklmnopqrstuvwxyz1234 inválida") == "chave *** inválida"
    assert scrub("a" * 500, limit=10) == "***"[:10]
    assert len(scrub("palavra curta " * 50)) == 200


def test_format_moment_names_the_weekday() -> None:
    assert format_moment(datetime(2026, 2, 20, 10, 0, tzinfo=TZ)) == "sexta-feira, 20/02/2026 10:00"


def test_envfile_parse_handles_comments_quotes_and_export() -> None:
    text = """
# comentário
OPENAI_API_KEY="abc"
export LLM_MODEL=modelo
VAZIA=
SEM_IGUAL
ASPAS='x y'
"""
    assert envfile.parse(text) == {"OPENAI_API_KEY": "abc", "LLM_MODEL": "modelo", "ASPAS": "x y"}


def test_envfile_load_never_overrides_the_environment(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=do-arquivo\nB=do-arquivo\n", encoding="utf-8")
    env = {"A": "do-ambiente"}
    envfile.load(path, env)
    assert env == {"A": "do-ambiente", "B": "do-arquivo"}


def test_envfile_load_ignores_a_missing_file(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    envfile.load(tmp_path / "nao-existe", env)
    assert env == {}


def test_reply_is_wrapped_to_the_terminal_width_only_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from emporio.cli.ui import Ui

    text = "palavra " * 30
    ui = Ui(trace=False, debug=False)
    ui.reply(text)
    assert capsys.readouterr().out.count("\n") == 2  # sem terminal: uma linha, sem quebra

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = Tty()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setenv("COLUMNS", "50")
    monkeypatch.setenv("NO_COLOR", "1")
    Ui(trace=False, debug=False).reply(text)
    lines = out.getvalue().rstrip("\n").split("\n")
    assert len(lines) > 2 and all(len(line) <= 50 for line in lines)
    assert all(line.startswith("     ") for line in lines[1:])  # alinhado depois de "Téo> "
    assert " ".join(out.getvalue().split())[len("Téo> ") :] == text.strip()  # mesmas palavras
