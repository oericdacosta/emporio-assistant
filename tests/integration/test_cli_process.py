"""O ``emporio`` como programa de verdade: processo novo, códigos de saída e mensagens."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def clean_env() -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "LLM_MODEL", "AGENT_NOW")
    }
    env["NO_COLOR"] = "1"
    return env


def emporio(*args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "emporio", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
        input="",
        check=False,
    )


def test_version(clean_env: dict[str, str], tmp_path: Path) -> None:
    result = emporio("--version", env=clean_env, cwd=tmp_path)
    assert result.returncode == 0 and result.stdout.startswith("emporio ")


def test_check_data_succeeds_with_the_real_files(clean_env: dict[str, str], tmp_path: Path) -> None:
    result = emporio("--check-data", env=clean_env, cwd=tmp_path)
    assert result.returncode == 0
    assert "65 produtos" in result.stdout and "10 seções de política" in result.stdout


def test_check_data_exits_3_when_the_folder_does_not_exist(
    clean_env: dict[str, str], tmp_path: Path
) -> None:
    result = emporio("--check-data", "--data", str(tmp_path / "nada"), env=clean_env, cwd=tmp_path)
    assert result.returncode == 3 and "Traceback" not in result.stderr


def test_without_configuration_it_exits_3_with_guidance(
    clean_env: dict[str, str], tmp_path: Path
) -> None:
    result = emporio(env=clean_env, cwd=tmp_path)
    assert result.returncode == 3
    assert "LLM_MODEL" in result.stderr and "Traceback" not in result.stderr


def test_invalid_option_exits_2(clean_env: dict[str, str], tmp_path: Path) -> None:
    result = emporio("--provider", "skynet", env=clean_env, cwd=tmp_path)
    assert result.returncode == 2 and "usage:" in result.stderr


def test_end_of_input_leaves_the_conversation_with_exit_0(
    clean_env: dict[str, str], tmp_path: Path
) -> None:
    env = {
        **clean_env,
        "OPENAI_API_KEY": "chave-de-teste",
        "LLM_MODEL": "modelo-x",
        "EMPORIO_HISTORY_DB": ":memory:",
        "EMPORIO_TURN_LOG": str(tmp_path / "t.jsonl"),
    }
    result = emporio(env=env, cwd=tmp_path)  # stdin vazio: Ctrl-D logo de início
    assert result.returncode == 0
    assert "enviadas à OpenAI" in result.stderr and "Até logo!" in result.stderr
