from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from evals.cases import Case
from evals.report import Meta, Prices, pass_map, render
from evals.runner import run_case, run_suite

from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.errors import ConfigError, ProviderFatal, ProviderUnavailable
from emporio.llm.base import Usage
from evals import run as run_module
from support.fakes import FakeLLM, say, say_from_tool, use

ANSWER = lambda d: f"O {d['name']} sai por {d['effective_price_brl']}."  # noqa: E731


def passing_llm() -> FakeLLM:
    return FakeLLM(use("get_product", name="gd20"), say_from_tool(ANSWER))


def test_a_passing_conversation(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    outcome = run_case(by_id["G01-preco-takamine-gd20"], passing_llm(), repo, policies)
    assert outcome.verdict.passed and outcome.verdict.hard == []
    assert outcome.conversation.last == "O Takamine GD20 Dreadnought Natural sai por R$ 2.199,00."
    assert outcome.usage == Usage(20, 10, 0) and outcome.seconds >= 0


def test_the_case_clock_and_temperature_reach_the_agent(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    llm = FakeLLM(
        use("get_store_info"),
        say("Hoje não estamos abertos; abrimos segunda, 09:00. Rua 14 de Maio, 3200."),
    )
    outcome = run_case(by_id["G13-loja-fechada-no-domingo"], llm, repo, policies)
    assert "Loja fechada agora; reabre segunda-feira" in llm.calls[0].system
    assert llm.calls[0].temperature == 0.0
    assert outcome.verdict.passed


def test_a_wrong_answer_is_reported_with_the_reasons(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    llm = FakeLLM(say("Não sei dizer o preço."))
    outcome = run_case(by_id["G01-preco-takamine-gd20"], llm, repo, policies)
    assert not outcome.verdict.passed
    assert any("nenhuma das tools esperadas" in r for r in outcome.verdict.hard)
    assert any("faltou na resposta" in r for r in outcome.verdict.hard)


def test_a_provider_failure_fails_the_case_but_a_bad_key_stops_everything(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    outcome = run_case(
        by_id["G01-preco-takamine-gd20"], FakeLLM(ProviderUnavailable("502")), repo, policies
    )
    assert outcome.verdict.hard == ["o provedor falhou: ProviderUnavailable: 502"]
    with pytest.raises(ProviderFatal):
        run_case(by_id["G01-preco-takamine-gd20"], FakeLLM(ProviderFatal("chave")), repo, policies)


def test_repeats_flag_unstable_cases(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(ANSWER),
        say("Não sei."),
    )
    seen = []
    reports = run_suite(
        [by_id["G01-preco-takamine-gd20"]], llm, repo, policies, repeat=2, on_case=seen.append
    )
    (report,) = reports
    assert report.flaky and not report.passed and len(report.runs) == 2 and seen == reports


def test_multi_turn_cases_run_all_the_turns(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(lambda d: f"Sai por {d['effective_price_brl']}."),
        use("get_product", name="gd20"),
        say_from_tool(lambda d: f"No PIX fica {d['pix_price_brl']}."),
        use("calculate_payment_options", product_id=95),
        say_from_tool(lambda d: f"Em 12x: {d['installments'][-1]['plan']}."),
    )
    outcome = run_case(by_id["M01-pix-e-12x-em-sequencia"], llm, repo, policies)
    assert len(outcome.conversation.answers) == 3
    assert outcome.verdict.passed, outcome.verdict.hard


def test_report_summarises_results_cost_and_failures(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    good = run_suite([by_id["G01-preco-takamine-gd20"]], passing_llm(), repo, policies)
    bad = run_suite([by_id["G09-parcelamento-12x"]], FakeLLM(say("Não sei.")), repo, policies)
    reports = [*good, *bad]
    meta = Meta(
        "modelo-x", datetime(2026, 3, 23, 10, 0), "abc1234", "deadbeef00", 1, Prices(1.0, 0.5, 4.0)
    )
    text = render(reports, meta)
    assert "modelo: `modelo-x`" in text and "commit: `abc1234`" in text
    assert "**aprovados: 1/2**" in text
    assert "| G01-preco-takamine-gd20 | product_price | PASS |" in text
    assert "| G09-parcelamento-12x | payment | FAIL |" in text
    assert "## Falha: G09-parcelamento-12x" in text and "> Não sei." in text
    assert "custo estimado: US$ 0.0001" in text
    assert pass_map(reports) == {"G01-preco-takamine-gd20": True, "G09-parcelamento-12x": False}


def test_cost_is_not_invented_without_prices(
    by_id: dict[str, Case], repo: CsvRepository, policies: PolicyStore
) -> None:
    reports = run_suite([by_id["G01-preco-takamine-gd20"]], passing_llm(), repo, policies)
    text = render(reports, Meta("m", datetime(2026, 3, 23), "n/a", "x", 1))
    assert "custo: não estimado" in text and "US$" not in text


def test_price_math() -> None:
    prices = Prices(input=1.0, cached=0.5, output=4.0)
    assert prices.cost(Usage(1_000_000, 500_000, 400_000)) == pytest.approx(0.6 + 0.2 + 2.0)


# --- a linha de comando --------------------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(run_module.envfile, "load", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "_commit", lambda: "abc1234")
    return tmp_path


def test_main_writes_the_report_and_the_pass_map(
    monkeypatch: pytest.MonkeyPatch, cli: Path
) -> None:
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    code = run_module.main(["--only", "G01", "--out", str(cli)])
    assert code == 0
    (report,) = cli.glob("*.md")
    assert report.name.endswith("_fake-model.md") and "aprovados: 1/1" in report.read_text(
        encoding="utf-8"
    )
    assert json.loads((cli / "latest.json").read_text(encoding="utf-8")) == {
        "G01-preco-takamine-gd20": True
    }


def test_a_label_keeps_two_reports_of_the_same_day_apart(
    monkeypatch: pytest.MonkeyPatch, cli: Path
) -> None:
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    assert run_module.main(["--only", "G01", "--out", str(cli)]) == 0
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    assert run_module.main(["--only", "G01", "--out", str(cli), "--label", "depois-do-ajuste"]) == 0
    names = sorted(p.name for p in cli.glob("*.md"))
    assert (
        len(names) == 2
        and names[0].endswith("_fake-model.md")
        and names[1].endswith("_fake-model_depois-do-ajuste.md")
    )


def test_main_fails_on_regression_against_the_baseline(
    monkeypatch: pytest.MonkeyPatch, cli: Path
) -> None:
    monkeypatch.setattr(run_module, "make_llm", lambda model, env: FakeLLM(say("Não sei.")))
    baseline = cli / "baseline.json"
    baseline.write_text(json.dumps({"G01-preco-takamine-gd20": True}), encoding="utf-8")
    assert run_module.main(["--only", "G01", "--out", str(cli), "--baseline", str(baseline)]) == 1

    baseline.write_text(json.dumps({"G01-preco-takamine-gd20": False}), encoding="utf-8")
    assert run_module.main(["--only", "G01", "--out", str(cli), "--baseline", str(baseline)]) == 0


def test_main_configuration_errors_exit_with_3(
    monkeypatch: pytest.MonkeyPatch, cli: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(model: str | None, env: object) -> None:
        raise ConfigError("falta o modelo da OpenAI")

    monkeypatch.setattr(run_module, "make_llm", broken)
    assert run_module.main(["--out", str(cli)]) == 3
    assert "falta o modelo" in capsys.readouterr().err

    monkeypatch.setattr(run_module, "make_llm", lambda model, env: passing_llm())
    assert run_module.main(["--only", "nenhum-caso", "--out", str(cli)]) == 3
    assert "nenhum caso" in capsys.readouterr().err
