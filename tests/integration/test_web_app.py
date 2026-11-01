"""A interface Streamlit de ponta a ponta, com o modelo roteirizado (sem navegador, sem rede)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from emporio.errors import ProviderUnavailable  # noqa: E402
from emporio.web import logic  # noqa: E402
from support.fakes import FakeLLM, say, use  # noqa: E402

APP = str(Path(__file__).resolve().parents[2] / "src" / "emporio" / "web" / "app.py")
EXAMPLE = logic.EXAMPLES[2]  # "Que horas a loja abre?"


@pytest.fixture(autouse=True)
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_privacy_notice_is_always_visible() -> None:
    at = start()
    assert not at.exception
    assert any("enviadas à OpenAI" in i.value for i in at.info)


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
    assert any(e.label == "Detalhes do turno" for e in at.expander)
    assert any("get_store_info" in m.value for m in at.markdown)
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
