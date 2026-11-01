from __future__ import annotations

from pathlib import Path

import pytest

from emporio.agent.session import Session, SQLiteSessionStore
from emporio.agent.types import GuardBlock, Outcome, ToolFinished, ToolRecord, TurnResult
from emporio.clock import FixedClock, SystemClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository, DataLoadError
from emporio.errors import ConfigError, ProviderFatal, ProviderUnavailable
from emporio.identity import IdentityVerifier
from emporio.llm.base import Message, Usage
from emporio.web import logic
from support.fakes import FakeLLM, say, use


def test_parse_clock() -> None:
    assert isinstance(logic.parse_clock(None), SystemClock)
    assert isinstance(logic.parse_clock(""), SystemClock)
    assert logic.parse_clock("2026-03-23T10:00").now().hour == 10
    with pytest.raises(ConfigError, match="inválida") as info:
        logic.parse_clock("ontem")
    assert "--" not in str(info.value)


def test_friendly_error() -> None:
    assert "Data" in logic.friendly_error(ConfigError("Data ou hora inválida"))
    assert "dados da loja" in logic.friendly_error(DataLoadError("x"))
    unavailable = logic.friendly_error(ProviderUnavailable("boom"))
    assert "Turno não gravado" in unavailable
    fatal = logic.friendly_error(ProviderFatal("chave sk-abcdefghijklmnopqrstuvwxyz0123"))
    assert "sk-abcdefghij" not in fatal and "chave" in fatal
    assert "Turno não gravado" in logic.friendly_error(RuntimeError("segredo"))
    assert "segredo" not in logic.friendly_error(RuntimeError("segredo"))
    for error in (ConfigError("x"), ProviderFatal("x"), ProviderUnavailable("x"), ValueError()):
        assert "--" not in logic.friendly_error(error)


def _result(blocks: tuple[GuardBlock, ...] = ()) -> TurnResult:
    return TurnResult(
        "oi",
        Outcome.FALLBACK if blocks else Outcome.ANSWERED,
        (Message("user", "a"), Message("assistant", "oi")),
        (ToolRecord("verify_customer", {"proof": "a@b.com", "order_id": 7}, True, None, 0.012, 9),),
        Usage(10, 5, 2),
        2,
        0.5,
        "abc123",
        bool(blocks),
        blocks,
    )


def test_turn_details_without_and_with_guard_block() -> None:
    finished = [ToolFinished("verify_customer", True, 0.012, {"ok": True, "data": {"x": 1}})]
    details = logic.turn_details(_result(), finished)
    assert details["guard_blocks"] == [] and not details["fallback"]
    tool = details["tools"][0]
    assert tool["arguments"] == {"proof": "***", "order_id": 7}
    assert tool["summary"] == '{"x":1}' and tool["ms"] == 12
    assert (details["input_tokens"], details["output_tokens"], details["cached_tokens"]) == (
        10,
        5,
        2,
    )

    blocked = logic.turn_details(_result((GuardBlock("custa R$ 9", ("R$ 9",)),)), [])
    assert blocked["fallback"]
    assert blocked["guard_blocks"] == [{"draft": "custa R$ 9", "violations": ["R$ 9"]}]
    assert blocked["tools"][0]["summary"] == ""


def test_list_sessions(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    assert logic.list_sessions(str(db)) == []
    assert logic.list_sessions(logic.IN_MEMORY) == []
    clock = FixedClock.parse("2026-03-23T10:00")
    store = SQLiteSessionStore(db, clock=clock)
    store.save(Session("a1"), [Message("user", "oi"), Message("assistant", "olá")])
    store.close()
    assert logic.list_sessions(str(db)) == [logic.SavedSession("a1", "2026-03-23 14:00")]


@pytest.fixture
def make_runtime(
    repo: CsvRepository, policies: PolicyStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    def _make(llm: FakeLLM, db: str | None = None) -> logic.Runtime:
        monkeypatch.setattr(logic, "make_llm_for_ui", lambda model, env: llm)
        return logic.build_runtime(
            model="m",
            api_key="k",
            clock=FixedClock.parse("2026-03-23T10:00"),
            repo=repo,
            policies=policies,
            identity=IdentityVerifier(),
            db=db or str(tmp_path / "h.db"),
        )

    return _make


def test_build_runtime_requires_model_and_key(repo: CsvRepository, policies: PolicyStore) -> None:
    with pytest.raises(ConfigError, match="barra lateral"):
        logic.build_runtime(
            model="",
            api_key="",
            clock=SystemClock(),
            repo=repo,
            policies=policies,
            identity=IdentityVerifier(),
            db=logic.IN_MEMORY,
        )


def test_successful_turn_is_saved(make_runtime, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeLLM(use("get_store_info"), say("Abrimos às 9h."))
    runtime = make_runtime(llm)
    seen: list[str] = []
    reply = logic.run_one_turn(runtime, "Que horas abre?", lambda e: seen.append(type(e).__name__))
    assert reply.kind in ("answer", "fallback") and reply.saved
    assert reply.details and reply.details["tools"][0]["name"] == "get_store_info"
    assert seen == ["ToolStarted", "ToolFinished"]
    assert [m.role for m in runtime.session.history] == ["user", "assistant"]
    assert [x.id for x in logic.list_sessions(str(tmp_path / "h.db"))] == [runtime.session.id]


def test_failed_turn_is_not_saved(make_runtime, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    runtime = make_runtime(FakeLLM(ProviderUnavailable("rede"), say("Abrimos às 9h.")))
    reply = logic.run_one_turn(runtime, "oi", None)
    assert reply.kind == "error" and not reply.saved and "Turno não gravado" in reply.text
    assert runtime.session.history == []
    assert logic.list_sessions(str(tmp_path / "h.db")) == []


def test_in_memory_saves_nothing(make_runtime) -> None:  # type: ignore[no-untyped-def]
    runtime = make_runtime(FakeLLM(say("Oi!")), db=logic.IN_MEMORY)
    reply = logic.run_one_turn(runtime, "oi")
    assert not reply.saved and len(runtime.session.history) == 2


def test_forget_clears_history(make_runtime, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    runtime = make_runtime(FakeLLM(say("Oi!")))
    logic.run_one_turn(runtime, "oi")
    logic.forget(runtime)
    assert runtime.session.history == []
    assert logic.list_sessions(str(tmp_path / "h.db")) == []


def test_paths_show_relative_to_where_the_app_runs_and_resolve_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    inside = tmp_path / "data" / "raw"
    assert logic.show_path(inside) == str(Path("data") / "raw")
    assert logic.resolve_path("data/raw") == str(inside)
    outside = Path("/opt/elsewhere/raw")
    assert logic.show_path(outside) == str(outside) and logic.resolve_path(str(outside)) == str(
        outside
    )
    assert logic.resolve_path(":memory:") == ":memory:"


def test_the_data_check_message_does_not_show_the_absolute_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    folder = str(tmp_path / "data" / "raw")
    ok = f"dados ok em {folder}: 65 produtos, 50 clientes"
    assert logic.hide_path(ok, folder) == "dados ok: 65 produtos, 50 clientes"
    bad = f"2 linha(s) rejeitada(s) nos CSVs de {folder}"
    assert (
        logic.hide_path(bad, folder)
        == f"2 linha(s) rejeitada(s) nos CSVs de {Path('data') / 'raw'}"
    )
