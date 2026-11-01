"""Relatório em Markdown de uma avaliação: o que passou, o que falhou e quanto custou."""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from emporio.agent.types import ToolRecord
from emporio.llm.base import Usage
from evals.runner import CaseReport


@dataclass(frozen=True, slots=True)
class Prices:
    """Dólares por milhão de tokens. Quem roda informa; o código não presume preço."""

    input: float
    cached: float
    output: float

    def cost(self, usage: Usage) -> float:
        fresh = usage.input_tokens - usage.cached_tokens
        return (
            fresh * self.input
            + usage.cached_tokens * self.cached
            + usage.output_tokens * self.output
        ) / 1e6


@dataclass(frozen=True, slots=True)
class Meta:
    model: str
    when: datetime
    commit: str
    prompt_hash: str
    repeat: int
    prices: Prices | None = None


def _total(reports: list[CaseReport]) -> Usage:
    runs = [run for report in reports for run in report.runs]
    return Usage(
        sum(r.usage.input_tokens for r in runs),
        sum(r.usage.output_tokens for r in runs),
        sum(r.usage.cached_tokens for r in runs),
    )


def pass_map(reports: list[CaseReport]) -> dict[str, bool]:
    return {report.case.id: report.passed for report in reports}


def _share(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "-"


def _suite_line(reports: list[CaseReport], suite: str) -> str:
    chosen = [r for r in reports if r.case.suite == suite]
    return _share(sum(r.passed for r in chosen), len(chosen))


def render(reports: list[CaseReport], meta: Meta) -> str:
    passed = sum(r.passed for r in reports)
    flaky = [r.case.id for r in reports if r.flaky]
    runs = [run for report in reports for run in report.runs]
    seconds = [run.seconds for run in runs]
    soft = sum(bool(run.verdict.soft) for run in runs)
    blocked = sum(run.conversation.guard_blocked for run in runs)
    fallbacks = sum(run.conversation.fallback for run in runs)
    usage = _total(reports)

    lines = [
        f"# Relatório de avaliação — {meta.when:%Y-%m-%d %H:%M}",
        "",
        f"- modelo: `{meta.model}` | commit: `{meta.commit}` | hash do prompt: `{meta.prompt_hash}` | execuções por caso: {meta.repeat}",
        f"- **aprovados: {passed}/{len(reports)}** (reprovam só as verificações HARD)",
        f"- **regressão: {_suite_line(reports, 'regression')}** (tem de ficar em 100%) | "
        f"capacidade: {_suite_line(reports, 'capability')} (casos difíceis, ainda não dominados)",
        f"- casos com aviso SOFT: {soft}/{len(runs)} execuções | instáveis (passam e falham): {', '.join(flaky) or 'nenhum'}",
        f"- guarda de fundamentação: barrou {blocked} execução(ões); resposta segura entregue em {fallbacks}",
        f"- latência por caso: mediana {statistics.median(seconds):.1f}s, máxima {max(seconds):.1f}s",
        f"- tokens: {usage.input_tokens} de entrada ({usage.cached_tokens} em cache) e {usage.output_tokens} de saída",
    ]
    if meta.prices:
        lines.append(
            f"- custo estimado: US$ {meta.prices.cost(usage):.4f} (estimativa com os preços informados; confira na página do provedor)"
        )
    else:
        lines.append(
            "- custo: não estimado (informe `--price-input`, `--price-cached` e `--price-output`)"
        )
    lines += [
        "",
        f"| caso | intenção | HARD | SOFT | tools | tokens (ent/saída) | s | suite | "
        f"ok/{meta.repeat} | pass@{meta.repeat} | pass^{meta.repeat} |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        first = report.runs[0]
        tools = ", ".join(dict.fromkeys(r.name for r in first.conversation.tools)) or "-"
        mark = "PASS" if report.passed else "FAIL"
        if report.flaky:
            mark += " (instável)"
        softs = len(first.verdict.soft) or "-"
        lines.append(
            f"| {report.case.id} | {report.case.intent} | {mark} | {softs} | {tools} | "
            f"{first.usage.input_tokens}/{first.usage.output_tokens} | {first.seconds:.1f} | "
            f"{report.case.suite} | {report.passes}/{len(report.runs)} | "
            f"{int(report.passed_any)} | {int(report.passed)} |"
        )

    lines += _by_intent(reports, meta.repeat)

    for report in reports:
        if report.passed and not any(run.verdict.soft for run in report.runs):
            continue
        lines += ["", f"## {'Falha' if not report.passed else 'Aviso'}: {report.case.id}", ""]
        lines.append("Perguntas: " + " → ".join(f"«{t}»" for t in report.case.turns))
        for number, run in enumerate(report.runs, 1):
            lines += ["", f"**execução {number}**", ""]
            lines += [f"- HARD: {reason}" for reason in run.verdict.hard] or ["- HARD: ok"]
            lines += [f"- SOFT: {reason}" for reason in run.verdict.soft]
            lines.append(f"- tools: {_calls(run.conversation.tools)}")
            for block in run.conversation.guard_blocks:
                lines.append(f"- guarda barrou {', '.join(block.violations)}: «{block.draft}»")
            for answer in run.conversation.answers[-1:]:
                lines += ["", f"> {answer}"]
    return "\n".join(lines) + "\n"


def _calls(tools: Sequence[ToolRecord]) -> str:
    """Cada chamada com os argumentos e se deu certo: ``get_product({"name": "gd20"}) ✓``."""
    shown = (
        f"{r.name}({json.dumps(r.arguments, ensure_ascii=False)}) "
        + ("✓" if r.ok else f"✗ {r.error_code}")
        for r in tools
    )
    return " → ".join(shown) or "nenhuma"


def _by_intent(reports: list[CaseReport], k: int) -> list[str]:
    """pass@1 é a taxa por execução; pass@k e pass^k contam casos (ver o README dos evals)."""
    lines = [
        "",
        "## Por intenção",
        "",
        f"| intenção | casos | pass@1 (execuções) | pass@{k} (ao menos uma) | pass^{k} (todas) |",
        "|---|---|---|---|---|",
    ]
    for intent in sorted({r.case.intent for r in reports}):
        chosen = [r for r in reports if r.case.intent == intent]
        runs = sum(len(r.runs) for r in chosen)
        lines.append(
            f"| {intent} | {len(chosen)} | {_share(sum(r.passes for r in chosen), runs)} | "
            f"{_share(sum(r.passed_any for r in chosen), len(chosen))} | "
            f"{_share(sum(r.passed for r in chosen), len(chosen))} |"
        )
    return lines


def render_json(reports: list[CaseReport]) -> str:
    return json.dumps(pass_map(reports), indent=1, sort_keys=True) + "\n"
