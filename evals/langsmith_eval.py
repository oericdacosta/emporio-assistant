"""O experimento no LangSmith: ``langsmith.evaluate()`` com as verificações do ``checks.py``.

Os avaliadores são código (nada de LLM como juiz): ``hard`` (0/1 e os motivos) e ``soft`` (fração
das frases esperadas). O resultado local (``CaseReport``) sai da MESMA execução, então o relatório
e o código de saída não dependem de o LangSmith responder.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from statistics import mean
from typing import Any

from emporio.agent.types import ToolRecord
from emporio.errors import ProviderFatal
from emporio.llm.base import LLMClient
from evals.cases import Case
from evals.checks import Conversation, Verdict, _matches, check, norm
from evals.runner import CaseReport, RunOutcome, run_case
from evals.sync_dataset import DATASET, example_id

NO_OUTPUT = "a execução não produziu saída (o alvo falhou)"


def outputs_of(outcome: RunOutcome) -> dict[str, Any]:
    """O que o alvo devolve ao LangSmith: o suficiente para os avaliadores refazerem o ``check``."""
    c = outcome.conversation
    return {
        "answers": list(c.answers),
        "tools": [
            {"name": t.name, "arguments": t.arguments, "ok": t.ok, "error_code": t.error_code}
            for t in c.tools
        ],
        "fallback": c.fallback,
        "guard_blocked": c.guard_blocked,
        "error": c.error,
    }


def conversation_from(outputs: Mapping[str, Any] | None) -> Conversation:
    if not outputs:
        return Conversation((), error=NO_OUTPUT)
    tools = tuple(
        ToolRecord(t["name"], t["arguments"], t["ok"], t["error_code"], 0.0, 0)
        for t in outputs.get("tools", ())
    )
    return Conversation(
        tuple(outputs.get("answers", ())),
        tools,
        bool(outputs.get("fallback")),
        bool(outputs.get("guard_blocked")),
        (),
        outputs.get("error"),
    )


def _grade(cases: Mapping[str, Case], run: Any, example: Any) -> tuple[Case, Verdict]:
    case = cases[example.metadata["id"]]
    return case, check(case, conversation_from(run.outputs))


def make_evaluators(cases: Sequence[Case]) -> list[Callable[[Any, Any], dict[str, Any]]]:
    by_id = {c.id: c for c in cases}

    def hard(run: Any, example: Any) -> dict[str, Any]:
        _, verdict = _grade(by_id, run, example)
        return {
            "key": "hard",
            "score": int(verdict.passed),
            "comment": "; ".join(verdict.hard) or "ok",
        }

    def soft(run: Any, example: Any) -> dict[str, Any]:
        case, verdict = _grade(by_id, run, example)
        last = norm(conversation_from(run.outputs).last)
        found = sum(_matches(group, last) for group in case.soft)
        score = found / len(case.soft) if case.soft else 1.0
        return {"key": "soft", "score": score, "comment": "; ".join(verdict.soft) or "ok"}

    return [hard, soft]


def pass_metrics(rows: Sequence[tuple[str, str, str, bool]]) -> list[dict[str, Any]]:
    """``rows``: (id do caso, suite, intenção, passou) por execução. pass@k e pass^k contam casos."""
    per_case: dict[str, list[bool]] = defaultdict(list)
    scopes: dict[str, set[str]] = defaultdict(set)
    for case_id, suite, intent, passed in rows:
        per_case[case_id].append(passed)
        for scope in ("todos", f"suite:{suite}", f"intent:{intent}"):
            scopes[scope].add(case_id)
    results = []
    for scope, ids in sorted(scopes.items()):
        for key, agg in (("pass@k", any), ("pass^k", all)):
            score = mean(agg(per_case[i]) for i in ids)
            results.append(
                {"key": f"{key}/{scope}", "score": score, "comment": f"{len(ids)} casos"}
            )
    return results


def make_summary(cases: Sequence[Case]) -> Any:
    by_id = {c.id: c for c in cases}

    def summary(runs: Sequence[Any], examples: Sequence[Any]) -> dict[str, Any]:
        rows = []
        for run, example in zip(runs, examples, strict=True):
            case, verdict = _grade(by_id, run, example)
            rows.append((case.id, case.suite, case.intent, verdict.passed))
        return {"results": pass_metrics(rows)}

    return summary


def _tag(case: Case) -> None:
    with suppress(Exception):
        from langsmith.run_helpers import get_current_run_tree

        if tree := get_current_run_tree():
            tree.add_tags([f"suite:{case.suite}", f"case:{case.id}"])


def run_experiment(
    client: Any,
    cases: Sequence[Case],
    llm: LLMClient,
    repo: Any,
    policies: Any,
    *,
    repeat: int,
    metadata: dict[str, Any],
    prefix: str,
    evaluate: Callable[..., Any] | None = None,
) -> tuple[list[CaseReport], str | None]:
    """Roda o experimento e devolve os ``CaseReport`` (da mesma execução) e o nome/link dele."""
    if evaluate is None:
        from langsmith import evaluate as evaluate_
    else:
        evaluate_ = evaluate
    by_id = {c.id: c for c in cases}
    outcomes: dict[str, list[RunOutcome]] = defaultdict(list)
    fatal: list[ProviderFatal] = []

    def target(inputs: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        case = by_id[metadata["id"]]
        _tag(case)
        if fatal:
            raise fatal[0]
        try:
            outcome = run_case(case, llm, repo, policies)
        except ProviderFatal as error:
            fatal.append(error)
            raise
        outcomes[case.id].append(outcome)
        return outputs_of(outcome)

    name: str | None = None
    try:
        ids = [example_id(c.id) for c in cases]
        examples = list(client.list_examples(dataset_name=DATASET, example_ids=ids))
        results = evaluate_(
            target,
            data=examples,
            evaluators=make_evaluators(cases),
            summary_evaluators=[make_summary(cases)],
            metadata=metadata,
            experiment_prefix=prefix,
            num_repetitions=repeat,
            max_concurrency=1,
            client=client,
        )
        name = results.experiment_name
        with suppress(Exception):
            name = f"{name} ({results.url})" if results.url else name
    except Exception as error:  # o portão não depende do LangSmith: o que faltou roda aqui
        print(
            f"Aviso: o LangSmith falhou ({type(error).__name__}); completando localmente.",
            file=sys.stderr,
        )
    if fatal:
        raise fatal[0]
    for case in cases:
        while len(outcomes[case.id]) < repeat:
            outcomes[case.id].append(run_case(case, llm, repo, policies))
    return [CaseReport(c, tuple(outcomes[c.id])) for c in cases], name
