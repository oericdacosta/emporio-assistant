from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from emporio.web import metrics

T0 = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)


def run(i: int, seconds: float, **meta: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"r{i}",
        start_time=T0 + timedelta(minutes=i),
        end_time=T0 + timedelta(minutes=i, seconds=seconds),
        status="success",
        error=None,
        prompt_tokens=100,
        completion_tokens=20,
        total_cost=0.01,
        extra={"metadata": meta},
    )


def test_turn_row_prefers_the_turn_metadata_and_falls_back_to_the_run_fields() -> None:
    row = metrics.turn_row(
        run(
            0,
            2,
            outcome="answered",
            input_tokens=7,
            output_tokens=3,
            cached_tokens=2,
            tools=["get_product"],
            guard_blocked=True,
        )
    )
    assert (row.input_tokens, row.output_tokens, row.cached_tokens) == (7, 3, 2)
    assert row.tools == ("get_product",) and row.guard_blocked and row.latency_s == 2
    bare = metrics.turn_row(run(0, 1))
    assert (bare.input_tokens, bare.output_tokens, bare.outcome) == (100, 20, "?")


def test_a_failed_run_is_an_error_and_a_running_one_has_no_latency() -> None:
    failed = run(0, 1, outcome="answered")
    failed.error = "boom"
    unfinished = run(1, 1)
    unfinished.end_time = None
    assert metrics.turn_row(failed).outcome == "erro"
    assert metrics.turn_row(unfinished).latency_s is None


def test_aggregates_percentiles_cost_and_fallback_rate() -> None:
    turns = metrics.rows(
        [
            run(i, s, outcome="fallback" if i == 3 else "answered")
            for i, s in enumerate((1, 2, 3, 10))
        ]
    )
    agg = metrics.aggregates(turns)
    assert (agg.turns, agg.p50_s, agg.p95_s) == (4, 2, 10)
    assert agg.total_cost == pytest.approx(0.04) and agg.fallback_rate == 0.25
    empty = metrics.aggregates([])
    assert empty.p50_s is None and empty.fallback_rate is None and empty.total_cost == 0


def test_rows_are_sorted_and_the_table_has_the_screen_columns() -> None:
    turns = metrics.rows([run(2, 1, tools=["a", "b"]), run(1, 1)])
    assert [t.trace_id for t in turns] == ["r1", "r2"]
    line = metrics.table(turns)[1]
    assert line["tools"] == "a, b" and line["trace"] == "r2" and line["custo (US$)"] == 0.01


def test_fetch_filters_by_session_and_last_experiment_picks_the_newest() -> None:
    calls: list[dict[str, object]] = []

    class Client:
        def list_runs(self, **kw: object) -> list[SimpleNamespace]:
            calls.append(kw)
            return [run(0, 1, outcome="answered")]

        def list_projects(self, **kw: object) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(name="velho", start_time=T0, url=None),
                SimpleNamespace(name="novo", start_time=T0 + timedelta(days=1), url="u"),
            ]

    mine, agg = metrics.fetch(Client(), "proj", "sess-1")
    assert len(mine) == 1 and agg.turns == 1
    assert calls[0]["project_name"] == "proj" and "sess-1" in str(calls[0]["filter"])
    assert calls[1]["limit"] == metrics.PERIOD_LIMIT == 100  # a API recusa mais de 100 por consulta
    assert metrics.last_experiment(Client()) == ("novo", "u")
    assert metrics.project_name({}) == "default"
    assert metrics.project_name({"LANGSMITH_PROJECT": "p"}) == "p"


def test_formatters_use_the_brazilian_decimal_comma_and_dash_for_missing() -> None:
    assert metrics.fmt_seconds(2.6) == "2,6 s" and metrics.fmt_seconds(None) == "-"
    assert metrics.fmt_usd(0.0069) == "US$ 0,0069" and metrics.fmt_usd(None) == "-"
    assert metrics.fmt_pct(0.0) == "0%" and metrics.fmt_pct(0.25) == "25%"
    assert metrics.cost_per_turn(metrics.Aggregates(4, 1.0, 2.0, 0.008, 0.0)) == 0.002
    assert metrics.cost_per_turn(metrics.Aggregates(0, None, None, 0.0, None)) is None


def test_quality_rates_cache_tool_errors_and_approval() -> None:
    turns = metrics.rows(
        [
            run(
                0,
                1,
                outcome="corrected",
                guard_blocked=True,
                tools=["a", "b"],
                tool_errors=["a:not_verified"],
                input_tokens=100,
                cached_tokens=40,
            ),
            run(1, 1, outcome="fallback", guard_blocked=True, tools=["a"], input_tokens=100),
            run(2, 1, outcome="answered", tools=[]),
            run(3, 1, outcome="answered"),
        ]
    )
    turns[0] = replace(turns[0], feedback_n=2, feedback_up=1.0)
    q = metrics.quality(turns)
    assert (q.corrected_rate, q.blocked_rate, q.tools_per_turn) == (0.25, 0.5, 0.75)
    assert q.tool_error_rate == pytest.approx(1 / 3) and q.cache_share == 0.1
    assert (q.approval, q.feedback_n) == (0.5, 2)
    empty = metrics.quality([])
    assert empty.approval is None and empty.cache_share is None and empty.tool_error_rate is None


def test_by_tool_counts_calls_and_errors_ordered_by_errors() -> None:
    turns = metrics.rows(
        [
            run(0, 1, tools=["a", "b", "b"], tool_errors=["b:x", "b:y"]),
            run(1, 1, tools=["a"], tool_errors=["a:x"]),
            run(2, 1),  # run antigo, sem campos novos
        ]
    )
    assert metrics.by_tool(turns) == [
        {"tool": "b", "chamadas": 2, "erros": 2, "taxa de erro": "100%"},
        {"tool": "a", "chamadas": 2, "erros": 1, "taxa de erro": "50%"},
    ]


def test_by_prompt_groups_versions_and_old_turns_have_no_version() -> None:
    turns = metrics.rows(
        [
            run(0, 2, prompt_hash="abc", outcome="corrected"),
            run(1, 4, prompt_hash="abc", outcome="answered"),
            run(2, 1),
        ]
    )
    lines = metrics.by_prompt(turns)
    assert [x["versão do prompt"] for x in lines] == [metrics.NO_VERSION, "abc"]
    abc = lines[1]
    assert abc["turnos"] == 2 and abc["tempo mediano"] == "2,0 s"
    assert abc["custo por turno"] == "US$ 0,0100" and abc["corrigidas pela guarda"] == "50%"
    assert abc["resposta segura"] == "0%"


def test_feedback_stats_are_read_and_missing_means_no_votes() -> None:
    voted = run(0, 1)
    voted.feedback_stats = {"user_score": {"n": 4, "avg": 0.75}}
    row = metrics.turn_row(voted)
    assert (row.feedback_n, row.feedback_up) == (4, 3.0)
    for stats in (None, {}, {"outro": {"n": 1}}, {"user_score": {"n": "x"}}):
        bare = run(1, 1)
        bare.feedback_stats = stats
        assert metrics.turn_row(bare).feedback_n == 0
    assert metrics.turn_row(run(2, 1)).feedback_n == 0  # sem o atributo
    assert "feedback_stats" in metrics.SELECT and metrics.fmt_num(1.44) == "1,4"
    assert metrics.fmt_num(None) == "-"
