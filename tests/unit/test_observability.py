from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from langsmith import Client, tracing_context
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import configure

from emporio import observability
from emporio.agent.graph import Agent
from emporio.agent.session import Session
from emporio.agent.types import Toolbox
from support.fakes import FakeLLM, StubToolbox, say, use


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(observability, "_warned", False)
    monkeypatch.setattr(observability, "_warmed", False)
    monkeypatch.setattr(observability, "_warm", lambda: None)  # nada de rede
    yield
    configure(enabled=None)  # desfaz o desligamento global do setup sem chave


def test_setup_without_key_warns_once_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    assert observability.setup({}) == "sem_chave"
    assert observability.setup({}) == "sem_chave"  # o Streamlit reroda o script
    from langsmith.utils import tracing_is_enabled

    assert tracing_is_enabled() is False
    err = capsys.readouterr().err
    assert err.count("LANGSMITH_API_KEY") == 1


def test_setup_with_tracing_off_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    assert observability.setup({}) == "desligado"  # o conftest força false
    assert capsys.readouterr().err == ""


def test_setup_with_key_is_active_and_defaults_tracing_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING")
    monkeypatch.setenv("LANGSMITH_API_KEY", "fake")
    assert observability.setup() == "ativo"
    assert capsys.readouterr().err == ""
    assert observability.os.environ["LANGSMITH_TRACING"] == "true"


def test_flush_returns_within_the_limit_when_the_client_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    class Stuck:
        def flush(self, timeout: float | None = None) -> None:
            release.wait(30)

    monkeypatch.setattr(observability, "_state", "ativo")
    monkeypatch.setattr(observability, "_client", lambda: Stuck())
    began = time.perf_counter()
    observability.flush(timeout=0.3)
    assert time.perf_counter() - began < 2
    release.set()


def test_flush_swallows_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise RuntimeError("sdk quebrado")

    monkeypatch.setattr(observability, "_state", "ativo")
    monkeypatch.setattr(observability, "_client", boom)
    observability.flush(timeout=0.3)


def _agent(llm: FakeLLM, toolbox: Toolbox[Any] | None = None) -> Agent[Any]:
    toolbox = toolbox or StubToolbox()
    return Agent(llm, toolbox, lambda s: None, lambda s: "prompt")


@pytest.mark.filterwarnings("ignore::langsmith.utils.LangSmithMissingAPIKeyWarning")
def test_sdk_failure_does_not_break_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("langsmith fora do ar")

    monkeypatch.setattr(Client, "create_run", boom)
    monkeypatch.setattr(Client, "update_run", boom)
    monkeypatch.setattr(observability, "_client", boom)
    toolbox = StubToolbox({"t": {"ok": True, "data": {}}})
    agent = _agent(FakeLLM(use("t"), say("Oi!")), toolbox)
    with tracing_context(enabled=True):
        result = agent.run_turn(Session("s1"), "oi")
    assert result.text == "Oi!"


def test_trace_id_is_the_uuid7_run_id_and_metadata_goes_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []

    def record(self: Client, name: str, inputs: Any, run_type: str, **kwargs: Any) -> None:
        created.append({"name": name, "run_type": run_type, "inputs": inputs, **kwargs})

    monkeypatch.setattr(Client, "create_run", record)
    monkeypatch.setattr(Client, "update_run", lambda self, run_id, **kw: None)
    toolbox = StubToolbox({"get_product": {"ok": True, "data": {"price": "2199.00"}}})
    agent = _agent(FakeLLM(use("get_product", name="gd20"), say("Consultei o produto.")), toolbox)
    with tracing_context(enabled=True, client=Client(api_key="fake", api_url="http://127.0.0.1:9")):
        result = agent.run_turn(Session("s1"), "quanto custa?")

    turn = next(r for r in created if r["name"] == "turno")
    assert str(turn["id"]) == result.trace_id
    assert uuid.UUID(result.trace_id).version == 7
    assert turn["inputs"] == {"text": "quanto custa?"}
    metadata = turn["extra"]["metadata"]
    assert metadata["session_id"] == "s1" and metadata["model"] == "fake-model"

    tool = next(r for r in created if r["run_type"] == "tool")
    assert tool["name"] == "get_product" and tool["inputs"] == {"arguments": {"name": "gd20"}}
    assert tool["parent_run_id"] is not None


def test_trace_id_is_uuid7_even_with_tracing_off() -> None:
    result = _agent(FakeLLM(say("Oi!"))).run_turn(Session("s1"), "oi")
    assert uuid.UUID(result.trace_id).version == 7
    assert get_current_run_tree() is None


def test_disabled_context_turns_tracing_off() -> None:
    with observability.disabled(), tracing_context(enabled=None):
        from langsmith.utils import tracing_is_enabled

        assert tracing_is_enabled() is False


def test_send_feedback_writes_idempotently_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []

    class Fake:
        def create_feedback(self, run_id: str, key: str, **kw: Any) -> None:
            calls.append(("create", (run_id, key, kw["score"], kw["feedback_id"])))
            if len([c for c in calls if c[0] == "create"]) > 1:
                raise RuntimeError("já existe")

        def update_feedback(self, feedback_id: Any, **kw: Any) -> None:
            calls.append(("update", (feedback_id, kw["score"])))

    monkeypatch.setattr(observability, "_client", lambda: Fake())
    monkeypatch.setattr(observability, "_state", "ativo")
    for score in (1.0, 0.0):
        worker = observability.send_feedback("t1", score)
        assert worker is not None and worker.daemon
        worker.join(2)
    first, _, second = calls
    assert first[1][:3] == ("t1", "user_score", 1.0) and second[0] == "update"
    assert second[1] == (first[1][3], 0.0)  # mesmo id: atualiza em vez de duplicar

    def boom() -> Any:
        raise OSError("rede")

    errors: list[str] = []
    monkeypatch.setattr(observability, "_client", boom)
    worker = observability.send_feedback("t1", 1.0, errors.append)
    assert worker is not None
    worker.join(2)
    assert errors == ["OSError"]


def test_send_feedback_is_a_no_op_unless_tracing_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "_state", "sem_chave")
    assert observability.send_feedback("t1", 1.0) is None


def test_turn_metadata_has_tool_errors_and_the_prompt_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from emporio.agent.prompt import prompt_version

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observability, "annotate", seen.append)
    error = {"ok": False, "error": {"code": "not_verified", "message": "x", "hint": "y"}}
    ok = {"ok": True, "data": {}}
    toolbox = StubToolbox({"verify_customer": error, "get_product": ok})
    llm = FakeLLM(use("verify_customer"), use("get_product", name="a"), say("Não verificado."))
    _agent(llm, toolbox).run_turn(Session("s1"), "oi")
    assert seen[0]["tool_errors"] == ["verify_customer:not_verified"]
    assert seen[0]["prompt_hash"] == prompt_version()
    clean = FakeLLM(say("Oi!"))
    _agent(clean).run_turn(Session("s2"), "oi")
    assert seen[1]["tool_errors"] == []
