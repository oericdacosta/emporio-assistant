"""LangSmith nos evals: montador de exemplos, avaliadores, pass@k/pass^k e o caminho com ``evaluate``
falso. Nenhuma chamada de rede."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from evals.cases import Case
from evals.langsmith_eval import (
    make_evaluators,
    make_summary,
    outputs_of,
    pass_metrics,
    run_experiment,
)
from evals.runner import run_case

from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from evals import run as run_module
from evals import sync_dataset
from support.fakes import FakeLLM, say, say_from_tool, use

ANSWER = lambda d: f"O {d['name']} sai por {d['effective_price_brl']}."  # noqa: E731
G01 = "G01-preco-takamine-gd20"


def passing_llm() -> FakeLLM:
    return FakeLLM(use("get_product", name="gd20"), say_from_tool(ANSWER))


def example(case: Case) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={"id": case.id, "suite": case.suite, "intent": case.intent}, outputs=None
    )


def test_build_examples_is_pure_and_stable(cases: list[Case]) -> None:
    first, second = sync_dataset.build_examples(cases), sync_dataset.build_examples(cases)
    assert first == second and len(first) == len(cases)
    one = first[0]
    assert one["inputs"] == {"turns": list(cases[0].turns), "now": cases[0].now}
    assert one["metadata"] == {
        "id": cases[0].id,
        "suite": cases[0].suite,
        "intent": cases[0].intent,
    }
    assert len({e["id"] for e in first}) == len(cases)


def _fake_client(existing: list[Any], calls: list[tuple[str, Any]], *, has: bool) -> Any:
    class Fake:
        def has_dataset(self, **kw: Any) -> bool:
            return has

        def create_dataset(self, name: str, **kw: Any) -> None:
            calls.append(("create", name))

        def create_examples(self, **kw: Any) -> None:
            ids = {str(e.id) for e in existing}
            assert not any(str(e["id"]) in ids for e in kw["examples"]), "409 no serviço real"
            calls.append(("new", len(kw["examples"])))

        def update_example(self, example_id: Any, **kw: Any) -> None:
            calls.append(("update", str(example_id)))

        def list_examples(self, **kw: Any) -> list[Any]:
            return existing

        def delete_examples(self, ids: Any) -> None:
            calls.append(("delete", list(ids)))

    return Fake()


def test_first_sync_creates_the_dataset_and_every_example(cases: list[Case]) -> None:
    calls: list[tuple[str, Any]] = []
    assert sync_dataset.sync(_fake_client([], calls, has=False), cases) == len(cases)
    assert calls == [("create", "emporio-golden"), ("new", len(cases))]


def test_a_second_sync_updates_instead_of_creating_and_drops_stale_examples(
    cases: list[Case],
) -> None:
    """Regressão real: o 2º create_examples com id fixo dava 409 e o eval caía em modo local."""
    calls: list[tuple[str, Any]] = []
    built = sync_dataset.build_examples(cases)
    existing = [SimpleNamespace(id=e["id"]) for e in built] + [SimpleNamespace(id="velho")]
    assert sync_dataset.sync(_fake_client(existing, calls, has=True), cases) == len(cases)
    assert [c for c, _ in calls].count("new") == 0
    assert [c for c, _ in calls].count("update") == len(cases)
    assert calls[-1] == ("delete", ["velho"])


def test_hard_and_soft_evaluators(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    case = by_id[G01]
    hard, soft = make_evaluators([case])
    good = SimpleNamespace(outputs=outputs_of(run_case(case, passing_llm(), repo, policies)))
    assert hard(good, example(case)) == {"key": "hard", "score": 1, "comment": "ok"}
    assert soft(good, example(case))["key"] == "soft"

    bad = SimpleNamespace(
        outputs=outputs_of(run_case(case, FakeLLM(say("Não sei.")), repo, policies))
    )
    result = hard(bad, example(case))
    assert result["score"] == 0 and "nenhuma das tools esperadas" in result["comment"]

    missing = hard(SimpleNamespace(outputs=None), example(case))
    assert missing["score"] == 0 and "falhou" in missing["comment"]


def test_soft_is_the_fraction_of_expected_phrases_found(by_id: dict[str, Case]) -> None:
    case = next(c for c in by_id.values() if c.soft)
    _, soft = make_evaluators([case])
    nothing = SimpleNamespace(outputs={"answers": ["x"], "tools": []})
    assert soft(nothing, example(case))["score"] == 0.0


def test_pass_at_k_and_pass_hat_k_per_suite_and_intent() -> None:
    rows = [
        ("a", "regression", "price", True),
        ("a", "regression", "price", False),  # instável: pass@2 sim, pass^2 não
        ("b", "regression", "hours", True),
        ("b", "regression", "hours", True),
        ("c", "capability", "price", False),
        ("c", "capability", "price", False),
    ]
    got = {r["key"]: r["score"] for r in pass_metrics(rows)}
    assert got["pass@k/todos"] == pytest.approx(2 / 3)
    assert got["pass^k/todos"] == pytest.approx(1 / 3)
    assert got["pass@k/suite:regression"] == 1.0 and got["pass^k/suite:regression"] == 0.5
    assert got["pass@k/suite:capability"] == 0.0
    assert got["pass@k/intent:price"] == 0.5 and got["pass^k/intent:hours"] == 1.0


def test_summary_evaluator_groups_the_runs_by_example(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    case = by_id[G01]
    outputs = outputs_of(run_case(case, passing_llm(), repo, policies))
    runs = [SimpleNamespace(outputs=outputs), SimpleNamespace(outputs=None)]
    out = make_summary([case])(runs, [example(case)] * 2)
    got = {r["key"]: r["score"] for r in out["results"]}
    assert got["pass@k/todos"] == 1.0 and got["pass^k/todos"] == 0.0


# --- run_experiment com o ``evaluate`` falso -----------------------------------------------------


class FakeClient:
    def list_examples(self, **kw: Any) -> list[Any]:
        return [SimpleNamespace(metadata={"id": G01}, inputs={})]


def fake_evaluate(seen: dict[str, Any], *, run_target: bool = True) -> Any:
    def evaluate(target: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        for ex in kwargs["data"] * kwargs["num_repetitions"] if run_target else ():
            target(ex.inputs, ex.metadata)
        return SimpleNamespace(experiment_name="exp-1", url="https://ls/exp-1")

    return evaluate


def test_experiment_passes_the_expected_arguments_and_builds_the_reports(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    seen: dict[str, Any] = {}
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(ANSWER),
        use("get_product", name="gd20"),
        say_from_tool(ANSWER),
    )
    reports, name = run_experiment(
        FakeClient(),
        [by_id[G01]],
        llm,
        repo,
        policies,
        repeat=2,
        metadata={"suite": "all"},
        prefix="m-all",
        evaluate=fake_evaluate(seen),
    )
    assert name == "exp-1 (https://ls/exp-1)"
    assert seen["max_concurrency"] == 1 and seen["num_repetitions"] == 2
    assert seen["experiment_prefix"] == "m-all" and seen["metadata"] == {"suite": "all"}
    assert len(seen["evaluators"]) == 2 and len(seen["summary_evaluators"]) == 1
    (report,) = reports
    assert report.passed and len(report.runs) == 2


def test_a_langsmith_failure_still_yields_the_reports_locally(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    def broken(target: Any, **kwargs: Any) -> Any:
        raise OSError("rede")

    reports, name = run_experiment(
        FakeClient(),
        [by_id[G01]],
        passing_llm(),
        repo,
        policies,
        repeat=1,
        metadata={},
        prefix="p",
        evaluate=broken,
    )
    assert name is None and reports[0].passed


# --- run.py com LangSmith ativo ----------------------------------------------------------------


def test_main_with_langsmith_active_uses_evaluate_and_writes_the_same_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import langsmith

    seen: dict[str, Any] = {}
    synced: list[int] = []
    monkeypatch.setattr(run_module.envfile, "load", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "_commit", lambda: "abc1234")
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    monkeypatch.setattr(run_module.observability, "setup", lambda: "ativo")
    monkeypatch.setattr(run_module.observability, "flush", lambda: synced.append(-1))
    monkeypatch.setattr(langsmith, "Client", lambda: FakeClient())
    monkeypatch.setattr(run_module, "sync", lambda client, golden: synced.append(len(golden)))
    monkeypatch.setattr(langsmith, "evaluate", fake_evaluate(seen))
    assert run_module.main(["--only", "G01", "--out", str(tmp_path)]) == 0
    assert synced[0] > 1 and synced[-1] == -1  # sincronizou o golden inteiro e deu flush
    assert seen["metadata"]["commit"] == "abc1234" and seen["metadata"]["suite"] == "all"
    (report,) = tmp_path.glob("*.md")
    assert "experimento LangSmith: exp-1 (https://ls/exp-1)" in report.read_text(encoding="utf-8")


def test_main_without_a_key_runs_locally_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run_module.envfile, "load", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    monkeypatch.setattr(run_module.observability, "setup", lambda: "sem_chave")
    assert run_module.main(["--only", "G01", "--out", str(tmp_path)]) == 0
    assert "Nenhum experimento foi criado" in capsys.readouterr().err
    (report,) = tmp_path.glob("*.md")
    assert "nenhum (rodada local)" in report.read_text(encoding="utf-8")
