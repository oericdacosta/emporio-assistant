from __future__ import annotations

import json
from pathlib import Path

import pytest

from emporio.agent.session import Session
from emporio.app import build_agent, load_data
from emporio.cli import main
from emporio.clock import FixedClock
from emporio.llm.replay import RecordingLLM, build_cassette, save_cassette
from emporio.paths import default_data_dir
from support.fakes import FakeLLM, say, use

NOW = "2026-03-23T10:00:00"
TURNS = ["Quanto custa o Takamine GD20?", "E o endereço da loja?"]


def record(path: Path) -> Path:
    """Grava um cassete com o agente de verdade e um modelo roteirizado."""
    repo, policies = load_data(default_data_dir())
    steps = [
        use("get_product", name="gd20"),
        say("O Takamine GD20 sai por R$ 2.199,00, ou R$ 2.089,05 no PIX."),
        use("get_store_info"),
        say("Ficamos na Rua 14 de Maio, 3200, no Centro de Campo Grande."),
    ]
    recorder = RecordingLLM(FakeLLM(*steps))
    clock = FixedClock.parse(NOW)
    agent = build_agent(
        recorder, repo=repo, clock=clock, policies=policies, complaints_log=path.parent / "c.jsonl"
    )
    session = Session("gravacao")
    for text in TURNS:
        agent.run_turn(session, text)
    cassette = build_cassette(
        recorder,
        [{"label": "preço e endereço", "now": NOW, "turns": TURNS}],
        recorded_on="2026-03-23",
        commit="abc1234",
    )
    save_cassette(path, cassette)
    return path


@pytest.fixture
def cassette(tmp_path: Path) -> Path:
    return record(tmp_path / "demo.json")


def test_demo_replays_the_conversation_with_real_tools(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--demo", str(cassette)], env={}) == 0
    cap = capsys.readouterr()
    assert (
        "MODO DEMO" in cap.err
        and "abc1234" in cap.err
        and "segunda-feira, 23/03/2026 10:00" in cap.err
    )
    assert "Você> Quanto custa o Takamine GD20?" in cap.out
    assert "Téo> O Takamine GD20 sai por R$ 2.199,00, ou R$ 2.089,05 no PIX." in cap.out
    assert "Rua 14 de Maio" in cap.out


def test_demo_needs_no_key_and_writes_nothing(cassette: Path, tmp_path: Path) -> None:
    before = {p.name for p in tmp_path.iterdir()}
    assert main(["--demo", str(cassette)], env={}) == 0
    assert {p.name for p in tmp_path.iterdir()} == before


def test_demo_fails_loudly_when_what_the_model_would_see_changes(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(cassette.read_text(encoding="utf-8"))
    data["meta"]["runs"][0]["now"] = (
        "2026-03-22T10:00:00"  # domingo: o prompt muda, mas as tools também
    )
    data["meta"]["runs"][0]["turns"][1] = (
        "E o horário da loja?"  # outra pergunta, outro pedido ao modelo
    )
    cassette.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--demo", str(cassette)], env={}) == 5
    assert "difere da gravada" in capsys.readouterr().err


def test_demo_rejects_a_missing_or_broken_cassette(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--demo", str(tmp_path / "nada.json")], env={}) == 5
    assert "ilegível ou ausente" in capsys.readouterr().err

    broken = tmp_path / "quebrado.json"
    broken.write_text(json.dumps({"meta": {"model": "m", "prompt_sha256": "x"}, "exchanges": []}))
    assert main(["--demo", str(broken)], env={}) == 5
    assert "runs" in capsys.readouterr().err


def test_demo_complains_when_the_cassette_has_unused_answers(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(cassette.read_text(encoding="utf-8"))
    data["exchanges"].append(data["exchanges"][-1])
    cassette.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--demo", str(cassette)], env={}) == 5
    assert "não pediu" in capsys.readouterr().err


def test_demo_warns_when_the_prompt_changed_since_the_recording(
    cassette: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = json.loads(cassette.read_text(encoding="utf-8"))
    data["meta"]["prompt_sha256"] = "0" * 16
    cassette.write_text(json.dumps(data), encoding="utf-8")
    assert main(["--demo", str(cassette)], env={}) == 0
    assert "o prompt de sistema mudou" in capsys.readouterr().err
