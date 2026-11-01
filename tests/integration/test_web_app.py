"""A interface Streamlit de ponta a ponta, com o modelo roteirizado (sem navegador, sem rede)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from emporio import observability  # noqa: E402
from emporio.errors import ProviderUnavailable  # noqa: E402
from emporio.web import examples, logic, metrics, results  # noqa: E402
from support.fakes import FakeLLM, say, use  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "src" / "emporio" / "web" / "app.py")
EXAMPLE = logic.EXAMPLES[2]  # "Que horas a loja abre?"


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(results, "SNAPSHOT", tmp_path / "sem-resultados.json")
    monkeypatch.setattr(examples, "EXAMPLES_DIR", tmp_path / "sem-exemplos")  # só o chat conta
    monkeypatch.chdir(tmp_path)  # history/ e logs/ ficam no tmp, e o .env real não é lido
    for name in ("OPENAI_API_KEY", "LLM_MODEL", "AGENT_NOW", "EMPORIO_HISTORY_DB"):
        monkeypatch.delenv(name, raising=False)


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    monkeypatch.setattr(logic, "make_llm_for_ui", lambda model, env: llm)


def start() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [w.value for w in at.warning]
    return "\n".join(parts + [i.value for i in at.info] + [e.value for e in at.error])


def test_the_page_has_no_privacy_banner() -> None:
    at = start()
    assert not at.exception
    assert not any("enviadas à OpenAI" in i.value for i in at.info)


def test_without_key_shows_friendly_message_and_disables_chat() -> None:
    at = start()
    assert any("informe o modelo e a chave" in w.value for w in at.warning)
    assert at.chat_input[0].disabled
    assert "--" not in texts(at)


def test_example_click_shows_the_answer_and_tool_details(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, FakeLLM(use("get_store_info"), say("Abrimos às 9h.")))
    at = start()
    assert not at.chat_input[0].disabled
    at.button(key=f"ex-{EXAMPLE}").click().run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert "Abrimos às 9h." in texts(at)
    assert not any("Detalhes" in e.label for e in at.expander)  # o chat fica limpo
    assert [t.label for t in at.tabs] == ["Conversa", "Exemplos", "Resultados"]
    assert not [b for b in at.main.button if b.label in logic.EXAMPLES]  # exemplos somem


def test_provider_failure_warns_without_duplicating(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, FakeLLM(ProviderUnavailable("rede"), say("Oi!")))
    at = start()
    at.chat_input[0].set_value("oi").run()
    assert any("Turno não gravado" in w.value for w in at.warning)
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    at.chat_input[0].set_value("oi").run()  # reenviar
    assert not any("Turno não gravado" in w.value for w in at.warning)
    assert [m.name for m in at.chat_message] == ["user", "assistant"]
    assert not at.exception


def test_new_chat_clears_the_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, FakeLLM(say("Oi!")))
    at = start()
    at.chat_input[0].set_value("olá").run()
    assert len(at.chat_message) == 2
    next(b for b in at.sidebar.button if b.label == "Nova conversa").click().run()
    assert len(at.chat_message) == 0
    assert not at.exception


# --- modo avaliador e aba Métricas -------------------------------------------------------------


def refresh(at: AppTest) -> AppTest:
    """Os dados do LangSmith só são lidos depois do clique em Atualizar."""
    return at.button(key="refresh-metrics").click().run()


def evaluator_mode(at: AppTest) -> AppTest:
    next(t for t in at.sidebar.toggle if t.label == "Modo avaliador").set_value(True)
    return at.run()


class FakeRuns:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.calls = error, []

    def list_runs(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        t0 = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
        return [
            SimpleNamespace(
                id="run-1",
                start_time=t0,
                end_time=t0 + timedelta(seconds=2),
                status="success",
                error=None,
                prompt_tokens=10,
                completion_tokens=5,
                total_cost=0.001,
                extra={"metadata": {"outcome": "answered", "tools": ["get_store_info"]}},
            )
        ]

    def list_projects(self, **kwargs: Any) -> list[Any]:
        return [SimpleNamespace(name="exp-9", start_time=datetime(2026, 3, 1), url=None)]


def test_evaluator_mode_off_shows_only_the_chat_tab() -> None:
    at = start()
    assert [t.label for t in at.tabs] == ["Conversa", "Exemplos", "Resultados"]


def test_evaluator_mode_adds_the_metrics_tab_and_moves_the_details_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_llm(monkeypatch, FakeLLM(use("get_store_info"), say("Abrimos às 9h.")))
    monkeypatch.setattr(observability, "setup", lambda *a, **k: "ativo")
    fake = FakeRuns()
    monkeypatch.setattr(metrics, "client_factory", lambda: fake)
    at = start()
    at.button(key=f"ex-{EXAMPLE}").click().run()
    at = evaluator_mode(at)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Conversa", "Exemplos", "Resultados", "Métricas"]
    assert not fake.calls  # nada é lido do LangSmith antes do clique
    assert any("Clique em **Atualizar**" in i.value for i in at.info)
    at = refresh(at)
    assert any("Clique em **Atualizar**" in i.value for i in at.info)  # a mensagem não some
    assert any(e.label.startswith("Detalhes das consultas") for e in at.expander)
    assert any("get_store_info" in m.value for m in at.markdown)
    assert fake.calls and fake.calls[0]["is_root"] is True
    assert "session_id" in fake.calls[0]["filter"]
    titles = [h.value for h in at.subheader]
    assert titles.index("Turnos desta conversa") < titles.index(
        "Resumo do período"
    )  # a conversa vem antes
    labels = {m.label: m.value for m in at.metric}
    assert {"Turnos", "Tempo mediano", "Tempo dos mais lentos", "Custo total"} <= set(labels)
    assert "," in labels["Custo total"] and labels["Custo total"].startswith("US$ ")
    assert any("exp-9" in c.value for c in at.caption)


def test_without_a_langsmith_key_the_metrics_tab_says_so() -> None:
    at = evaluator_mode(start())
    assert any("LangSmith não configurado" in i.value for i in at.info)
    assert not at.exception


def test_a_client_error_becomes_a_warning_and_the_chat_keeps_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_llm(monkeypatch, FakeLLM(say("Oi!"), say("Oi de novo!")))
    monkeypatch.setattr(observability, "setup", lambda *a, **k: "ativo")
    monkeypatch.setattr(metrics, "client_factory", lambda: FakeRuns(error=OSError("rede")))
    at = refresh(evaluator_mode(start()))
    assert any("Não consegui ler o LangSmith" in w.value for w in at.warning)
    at.chat_input[0].set_value("olá").run()
    assert not at.exception
    assert [m.name for m in at.chat_message] == ["user", "assistant"]


# --- métricas novas e feedback do cliente ------------------------------------------------------


class RichRuns(FakeRuns):
    def list_runs(self, **kwargs: Any) -> list[Any]:
        runs = super().list_runs(**kwargs)
        runs[0].feedback_stats = {"user_score": {"n": 4, "avg": 0.75}}
        runs[0].extra = {
            "metadata": {
                "outcome": "corrected",
                "guard_blocked": True,
                "tools": ["verify_customer", "get_order"],
                "tool_errors": ["verify_customer:not_verified"],
                "input_tokens": 100,
                "cached_tokens": 50,
                "prompt_hash": "abc1234567",
            }
        }
        return runs


def test_metrics_tab_shows_the_new_metrics_and_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, FakeLLM(say("Oi!")))
    monkeypatch.setattr(observability, "setup", lambda *a, **k: "ativo")
    monkeypatch.setattr(metrics, "client_factory", lambda: RichRuns())
    at = refresh(evaluator_mode(start()))
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels["Corrigidas pela guarda"] == "100%"
    assert labels["Guarda barrou"] == "100%"
    assert labels["Aprovação do cliente"] == "75%"
    assert labels["Erros de tool"] == "50%" and labels["Consultas por turno"] == "2,0"
    assert labels["Entrada vinda do cache"] == "50%"
    subheaders = [s.value for s in at.subheader]
    assert "Por tool" in subheaders and "Por versão do prompt" in subheaders
    assert any(
        d.value["tool"].tolist() == ["verify_customer", "get_order"]
        for d in at.dataframe
        if "tool" in d.value
    )


def test_metrics_without_feedback_show_a_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, FakeLLM(say("Oi!")))
    monkeypatch.setattr(observability, "setup", lambda *a, **k: "ativo")
    monkeypatch.setattr(metrics, "client_factory", lambda: FakeRuns())
    at = refresh(evaluator_mode(start()))
    assert {m.label: m.value for m in at.metric}["Aprovação do cliente"] == "-"


def test_feedback_widget_only_with_active_tracing_and_callback_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_llm(monkeypatch, FakeLLM(say("Oi!"), say("Oi!")))
    monkeypatch.setattr(observability, "setup", lambda *a, **k: "sem_chave")
    at = start()
    at.chat_input[0].set_value("oi").run()
    assert len(at.feedback) == 0

    sent: list[tuple[str, float]] = []

    def fake_send(trace_id: str, score: float, on_error: Any = None) -> None:
        sent.append((trace_id, score))
        on_error("OSError")

    monkeypatch.setattr(observability, "setup", lambda *a, **k: "ativo")
    monkeypatch.setattr(observability, "send_feedback", fake_send)
    at = start()
    at.chat_input[0].set_value("oi").run()
    assert len(at.feedback) == 1 and not at.exception
    at.feedback[0].set_value(1).run()
    assert not at.exception and len(sent) == 1 and sent[0][1] == 1.0
    assert any("Não consegui registrar sua avaliação" in c.value for c in at.caption)
    assert at.feedback[0].value == 1  # o estado fica na mensagem


def test_the_examples_tab_shows_the_recorded_conversations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        examples, "EXAMPLES_DIR", Path(__file__).resolve().parents[2] / "examples" / "conversations"
    )
    at = start()
    assert not at.exception
    assert [t.label for t in at.tabs][:3] == ["Conversa", "Exemplos", "Resultados"]
    assert len(examples.load_examples()) >= 3
    assert any(e.label.startswith("Busca no catálogo") for e in at.expander)


def test_the_results_tab_shows_the_exported_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        results,
        "SNAPSHOT",
        Path(__file__).resolve().parents[2] / "evals" / "reports" / "metrics_snapshot.json",
    )
    snapshot = results.load_snapshot()
    assert snapshot is not None and snapshot["turns"]
    at = start()
    assert not at.exception
    assert any(m.label == "Tempo mediano" for m in at.metric)  # as mesmas métricas da aba Métricas
    subheaders = {h.value for h in at.subheader}
    assert {"Resumo do período", "Por tool"} <= subheaders
    assert not {"Turnos desta conversa", "Por versão do prompt", "Rodadas do golden"} & subheaders
    labels = {m.label for m in at.metric}
    assert not {"Resposta segura", "Aprovação do cliente"} & labels


def test_a_missing_snapshot_is_not_an_error() -> None:
    assert results.load_snapshot(Path("nao-existe.json")) is None
    assert not start().exception
