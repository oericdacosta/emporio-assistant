"""Roda os casos de referência contra o modelo configurado e grava um relatório versionável.

Uso:  uv run python -m evals.run --model <modelo> [--repeat 3] [--only G0] [--baseline evals/reports/baseline.json]
A chave vem de OPENAI_API_KEY (ou do arquivo .env). Custa chamadas da API: não roda no CI.
Com LANGSMITH_API_KEY o run vira um experimento no LangSmith; sem ela roda local, como sempre.
O relatório e o código de saída (o portão) saem do mesmo resultado nos dois caminhos.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from emporio import envfile, observability
from emporio.agent.prompt import prompt_version
from emporio.app import load_data
from emporio.data.repository import DataLoadError
from emporio.errors import ConfigError, ProviderFatal
from emporio.llm.base import LLMClient
from emporio.llm.factory import make_llm
from emporio.paths import default_data_dir
from evals.cases import SUITES, Case, CaseError, load_cases
from evals.facts import FactError, Facts
from evals.langsmith_eval import run_experiment
from evals.report import Meta, Prices, pass_map, render, render_json
from evals.runner import CaseReport, run_suite
from evals.sync_dataset import sync

GOLDEN = Path(__file__).with_name("golden.yaml")
REPORTS = Path(__file__).with_name("reports")


def _commit() -> str:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    return done.stdout.strip() or "n/a"


def _progress(report: CaseReport) -> None:
    seconds = sum(run.seconds for run in report.runs)
    mark = "PASS" if report.passed else "FAIL"
    print(f"[{mark}] {report.case.id} ({seconds:.1f}s)", file=sys.stderr, flush=True)
    for run in report.runs:
        for reason in run.verdict.hard:
            print(f"        - {reason}", file=sys.stderr, flush=True)


def _langsmith(
    args: argparse.Namespace,
    golden: list[Case],
    cases: list[Case],
    llm: LLMClient,
    repo: Any,
    policies: Any,
) -> tuple[list[CaseReport] | None, str | None]:
    """Publica o dataset e roda o experimento. Se a sincronização falhar, devolve None (rodada local)."""
    from langsmith import Client

    try:
        client = Client()
        sync(client, golden)
    except Exception as error:
        print(
            f"Aviso: LangSmith indisponível ({type(error).__name__}); rodando local, sem experimento.",
            file=sys.stderr,
        )
        return None, None
    metadata = {
        "suite": args.suite,
        "model": llm.model,
        "commit": _commit(),
        "prompt_hash": prompt_version(),
        "repeat": args.repeat,
    }
    return run_experiment(
        client,
        cases,
        llm,
        repo,
        policies,
        repeat=args.repeat,
        metadata=metadata,
        prefix=f"{llm.model}-{args.suite}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run", description=__doc__)
    parser.add_argument("--model", help="modelo da OpenAI (padrão: LLM_MODEL)")
    parser.add_argument("--cases", type=Path, default=GOLDEN)
    parser.add_argument("--only", default="", help="só os casos cujo id começa com este texto")
    parser.add_argument(
        "--suite",
        choices=(*SUITES, "all"),
        default="all",
        help="regression (tem de passar 100%%), capability (casos difíceis) ou all",
    )
    parser.add_argument("--repeat", type=int, default=1, help="execuções por caso (o modelo varia)")
    parser.add_argument("--out", type=Path, default=REPORTS)
    parser.add_argument(
        "--label",
        default="",
        help="sufixo do nome do relatório, para não sobrescrever o do mesmo dia",
    )
    parser.add_argument(
        "--baseline", type=Path, help="JSON de uma execução anterior; sai com 1 se algo regredir"
    )
    parser.add_argument("--price-input", type=float, help="US$ por milhão de tokens de entrada")
    parser.add_argument("--price-cached", type=float, help="US$ por milhão de tokens em cache")
    parser.add_argument("--price-output", type=float, help="US$ por milhão de tokens de saída")
    args = parser.parse_args(argv)

    envfile.load(Path(".env"))
    try:
        llm = make_llm(args.model, os.environ)
        data_dir = default_data_dir()
        repo, policies = load_data(data_dir)
        facts = Facts(repo)
        golden = load_cases(args.cases, facts)
        cases = [c for c in golden if c.id.startswith(args.only) and args.suite in ("all", c.suite)]
        stale = [
            f"{c.id}: {why}" for c in cases for a in c.assume if (why := facts.check_assumption(a))
        ]
    except (ConfigError, DataLoadError, CaseError, FactError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 3
    if stale:
        print(
            "Erro: os dados não são mais como o golden supõe:\n  " + "\n  ".join(stale),
            file=sys.stderr,
        )
        return 3
    if not cases:
        print("Erro: nenhum caso selecionado.", file=sys.stderr)
        return 3

    prices = None
    if None not in (args.price_input, args.price_cached, args.price_output):
        prices = Prices(args.price_input, args.price_cached, args.price_output)
    print(
        f"{len(cases)} casos | modelo {llm.model} | {args.repeat} execução(ões) por caso",
        file=sys.stderr,
    )
    experiment: str | None = None
    tracing = observability.setup()
    try:
        reports = None
        if tracing == "ativo" and args.cases == GOLDEN:
            reports, experiment = _langsmith(args, golden, cases, llm, repo, policies)
        elif tracing == "ativo":
            print("Aviso: golden alternativo; nenhum experimento criado.", file=sys.stderr)
        else:
            why = observability.NO_KEY_WARNING if tracing == "sem_chave" else "tracing desligado"
            print(f"Aviso: {why}. Nenhum experimento foi criado.", file=sys.stderr)
        if reports is None:
            reports = run_suite(cases, llm, repo, policies, repeat=args.repeat, on_case=_progress)
        else:
            for report in reports:
                _progress(report)
    except ProviderFatal as error:
        print(
            f"Erro: o provedor recusou a requisição ({str(error)[:120]}). Confira a chave e o modelo.",
            file=sys.stderr,
        )
        return 3
    finally:
        observability.flush()

    now = datetime.now()
    meta = Meta(llm.model, now, _commit(), prompt_version(), args.repeat, prices, experiment)
    args.out.mkdir(parents=True, exist_ok=True)
    # a suite entra no nome para uma rodada parcial não sobrescrever o relatório completo do dia
    suffix = (f"_{args.suite}" if args.suite != "all" else "") + (
        f"_{args.label}" if args.label else ""
    )
    path = args.out / f"{now:%Y-%m-%d}_{llm.model.replace('/', '-')}{suffix}.md"
    path.write_text(render(reports, meta), encoding="utf-8")
    (args.out / "latest.json").write_text(render_json(reports), encoding="utf-8")
    passed = sum(r.passed for r in reports)
    print(f"\naprovados: {passed}/{len(reports)} | relatório: {path}", file=sys.stderr)
    if experiment:
        print(f"experimento LangSmith: {experiment}", file=sys.stderr)

    # o portão: um caso da regressão que reprova faz a execução sair com 1; os de capacidade só medem
    broken = [r.case.id for r in reports if r.case.suite == "regression" and not r.passed]
    if broken:
        print(
            f"REGRESSÃO: a suite regression tem de passar 100%: {', '.join(broken)}",
            file=sys.stderr,
        )
        return 1

    if args.baseline:
        before = json.loads(args.baseline.read_text(encoding="utf-8"))
        regressions = [
            case for case, ok in pass_map(reports).items() if before.get(case) and not ok
        ]
        if regressions:
            print(f"REGRESSÃO em relação à referência: {', '.join(regressions)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
