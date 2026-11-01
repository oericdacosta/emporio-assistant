"""Roda os casos de referência contra o modelo configurado e grava um relatório versionável.

Uso:  uv run python -m evals.run --model <modelo> [--repeat 3] [--only G0] [--baseline evals/reports/baseline.json]
A chave vem de OPENAI_API_KEY (ou do arquivo .env). Custa chamadas da API: não roda no CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from emporio import envfile
from emporio.agent.prompt import build_system_prompt
from emporio.app import load_data
from emporio.clock import FixedClock
from emporio.data.repository import DataLoadError
from emporio.domain.hours import store_status
from emporio.errors import ConfigError, ProviderFatal
from emporio.llm.factory import make_llm
from emporio.paths import default_data_dir
from evals.cases import DEFAULT_NOW, CaseError, load_cases
from evals.facts import FactError, Facts
from evals.report import Meta, Prices, pass_map, render, render_json
from evals.runner import CaseReport, run_suite

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


def _prompt_hash() -> str:
    now = FixedClock.parse(DEFAULT_NOW).now()
    return hashlib.sha256(build_system_prompt(now, store_status(now)).encode()).hexdigest()[:10]


def _progress(report: CaseReport) -> None:
    seconds = sum(run.seconds for run in report.runs)
    mark = "PASS" if report.passed else "FAIL"
    print(f"[{mark}] {report.case.id} ({seconds:.1f}s)", file=sys.stderr, flush=True)
    for run in report.runs:
        for reason in run.verdict.hard:
            print(f"        - {reason}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run", description=__doc__)
    parser.add_argument("--model", help="modelo da OpenAI (padrão: LLM_MODEL)")
    parser.add_argument("--cases", type=Path, default=GOLDEN)
    parser.add_argument("--only", default="", help="só os casos cujo id começa com este texto")
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
        cases = [c for c in load_cases(args.cases, facts) if c.id.startswith(args.only)]
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
    try:
        reports = run_suite(cases, llm, repo, policies, repeat=args.repeat, on_case=_progress)
    except ProviderFatal as error:
        print(
            f"Erro: o provedor recusou a requisição ({str(error)[:120]}). Confira a chave e o modelo.",
            file=sys.stderr,
        )
        return 3

    now = datetime.now()
    meta = Meta(llm.model, now, _commit(), _prompt_hash(), args.repeat, prices)
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.label}" if args.label else ""
    path = args.out / f"{now:%Y-%m-%d}_{llm.model.replace('/', '-')}{suffix}.md"
    path.write_text(render(reports, meta), encoding="utf-8")
    (args.out / "latest.json").write_text(render_json(reports), encoding="utf-8")
    passed = sum(r.passed for r in reports)
    print(f"\naprovados: {passed}/{len(reports)} | relatório: {path}", file=sys.stderr)

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
