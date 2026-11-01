"""``emporio --demo``: reproduz uma conversa gravada, sem chave e sem rede."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from emporio.agent.session import Session
from emporio.app import build_agent, load_data
from emporio.cli import codes
from emporio.cli.chat import Chat
from emporio.cli.text import AGENT_NAME, format_moment
from emporio.cli.ui import Ui
from emporio.clock import FixedClock
from emporio.errors import CassetteError
from emporio.llm.replay import ReplayLLM, load_cassette


def _runs(meta: dict[str, Any], path: Path) -> list[dict[str, Any]]:
    runs = meta.get("runs")
    if (
        not isinstance(runs, list)
        or not runs
        or not all(isinstance(run, dict) and {"label", "now", "turns"} <= set(run) for run in runs)
    ):
        raise CassetteError(
            f"o cassete {path} não tem as conversas (runs) esperadas. Grave de novo."
        )
    return runs


def run_demo(path: Path, ui: Ui, data_dir: Path) -> int:
    """Roda as conversas do cassete. Tools, dados, regras e guarda de fundamentação são os de
    verdade; só as respostas do modelo vêm do arquivo."""
    cassette = load_cassette(path)
    meta = cassette["meta"]
    runs = _runs(meta, path)
    repo, policies = load_data(data_dir)
    replay = ReplayLLM(cassette)

    ui.note(f"Empório da Música - {AGENT_NAME}, assistente virtual (simulação de atendimento)", "1")
    ui.note(
        f"  MODO DEMO: conversa gravada com o modelo {meta['model']} em {meta.get('recorded_on', '?')}"
        f" (commit {meta.get('commit') or '?'}).",
        "1;33",
    )
    ui.note(
        "  Real e executado agora: ferramentas, dados, regras de negócio e guarda de fundamentação."
    )
    ui.note(
        "  Gravado: apenas as respostas do modelo. Não é interativo; use --model para conversar.\n"
    )

    with tempfile.TemporaryDirectory() as scratch:  # reclamações da demo não ficam em disco
        for run in runs:
            clock = FixedClock.parse(run["now"])
            ui.note(f"--- {run['label']} | relógio simulado: {format_moment(clock.now())}", "1")
            agent = build_agent(
                replay,
                repo=repo,
                clock=clock,
                policies=policies,
                complaints_log=Path(scratch) / "complaints.jsonl",
            )
            chat = Chat(agent, Session(f"demo-{path.stem}"), ui, clock=clock)
            if code := chat.play(run["turns"]):
                return code
            ui.say()

    if replay.remaining:
        raise CassetteError("o cassete tem respostas que o agente não pediu. Grave de novo.")
    if replay.prompt_drift:
        ui.note(
            "Aviso: o prompt de sistema mudou desde a gravação; o texto gravado pode estar defasado.",
            "33",
        )
    return codes.OK
