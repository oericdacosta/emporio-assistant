"""O agente montado de verdade (tools reais, CSVs reais, relógio fixo) com um modelo roteirizado."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from emporio.agent.loop import Agent, Outcome
from emporio.agent.session import Session, SQLiteSessionStore, new_session_id
from emporio.app import build_agent
from emporio.clock import FixedClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.identity import IdentityVerifier
from emporio.tools.deps import ToolContext
from support.fakes import FakeLLM, say, say_from_tool, use

ANA_PHONE_END = "7821"  # cliente 2, pedido 8 (enviado)

Make = Callable[[FakeLLM], Agent[ToolContext]]


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock.parse("2026-03-23T10:00:00")


@pytest.fixture
def identity() -> IdentityVerifier:
    return IdentityVerifier()


def agent_for(
    llm: FakeLLM,
    repo: CsvRepository,
    clock: FixedClock,
    policies: PolicyStore,
    identity: IdentityVerifier,
    tmp_path: Path,
) -> Agent[ToolContext]:
    return build_agent(
        llm,
        repo=repo,
        clock=clock,
        policies=policies,
        identity=identity,
        complaints_log=tmp_path / "complaints.jsonl",
    )


@pytest.fixture
def make(
    repo: CsvRepository,
    clock: FixedClock,
    policies: PolicyStore,
    identity: IdentityVerifier,
    tmp_path: Path,
):
    def _make(llm: FakeLLM) -> Agent[ToolContext]:
        return agent_for(llm, repo, clock, policies, identity, tmp_path)

    return _make


def test_price_question_is_answered_from_the_real_tool(make: Make) -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(
            lambda d: (
                f"O {d['name']} sai por {d['effective_price_brl']}, ou {d['pix_price_brl']} no PIX."
            )
        ),
    )
    result = make(llm).run_turn(Session("s1"), "Quanto custa o Takamine GD20?")

    assert (
        result.text
        == "O Takamine GD20 Dreadnought Natural sai por R$ 2.199,00, ou R$ 2.089,05 no PIX."
    )
    assert result.outcome is Outcome.ANSWERED and result.tools[0].name == "get_product"


def test_an_invented_price_never_reaches_the_customer(make: Make) -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say("O GD20 sai por R$ 1.999,00."),
        say("O GD20 sai por R$ 1.999,00."),
    )
    result = make(llm).run_turn(Session("s1"), "Quanto custa o Takamine GD20?")
    assert result.fallback and "1.999" not in result.text


def test_ambiguous_name_hands_the_choice_back_to_the_customer(make: Make) -> None:
    def ask_which(messages: list[Any]) -> Any:
        data = json.loads(next(m for m in reversed(messages) if m.role == "tool").content)["data"]
        names = ", ".join(c["name"] for c in data["candidates"])
        return say(f"Tenho mais de um Takamine: {names}. Qual deles?")

    result = make(FakeLLM(use("get_product", name="takamine"), ask_which)).run_turn(
        Session("s1"), "Quero um Takamine"
    )
    assert result.outcome is Outcome.ANSWERED
    for model in ("GD20", "GC5CE", "GN51CE", "GC1CE"):
        assert model in result.text


def test_verification_across_turns_unlocks_the_order_and_the_customer_name(make: Make) -> None:
    session = Session("s1")
    llm = FakeLLM(
        say(
            "Pra eu ver seu pedido, me passa o número e o e-mail ou os 4 últimos dígitos do telefone."
        ),
        use("verify_customer", order_id=8, proof=ANA_PHONE_END),
        use("get_order_status", order_id=8),
        say_from_tool(
            lambda d: f"Seu pedido está {d['status_pt']}. Rastreio: {d['tracking_code']}."
        ),
        use("get_order_status", order_id=8),
        say_from_tool(lambda d: f"Continua {d['status_pt']}."),
    )
    agent = make(llm)

    agent.run_turn(session, "Cadê meu pedido?")
    assert session.guard.customer_id is None

    result = agent.run_turn(session, f"Pedido 8, telefone final {ANA_PHONE_END}")
    assert result.text == "Seu pedido está enviado, a caminho. Rastreio: BRJL5544332BR."
    assert [t.name for t in result.tools] == ["verify_customer", "get_order_status"]
    assert session.guard.customer_id == 2

    systems = [call.system for call in llm.calls]
    assert "Cliente verificado" not in systems[0] and "Cliente verificado" not in systems[1]
    assert "Cliente verificado: Ana." in systems[2]  # o nome aparece logo depois da verificação

    again = agent.run_turn(session, "E agora?")  # sem pedir a prova de novo
    assert again.tools[0].ok and again.outcome is Outcome.ANSWERED
    llm.assert_exhausted()


def test_a_new_session_must_verify_again(make: Make) -> None:
    llm = FakeLLM(use("get_order_status", order_id=8), say("Preciso confirmar seus dados antes."))
    result = make(llm).run_turn(Session("outra"), "status do pedido 8")
    assert result.tools[0].ok is False and result.tools[0].error_code == "not_verified"
    assert result.outcome is Outcome.ANSWERED


def test_order_lockout_holds_across_conversations(make: Make) -> None:
    for guess in ("1111", "2222", "3333", "4444", "5555"):
        llm = FakeLLM(
            use("verify_customer", order_id=8, proof=guess), say("Não consegui confirmar.")
        )
        make(llm).run_turn(Session(new_session_id()), f"pedido 8, final {guess}")

    llm = FakeLLM(
        use("verify_customer", order_id=8, proof=ANA_PHONE_END), say("Não consegui confirmar.")
    )
    result = make(llm).run_turn(Session(new_session_id()), "pedido 8, final certo")
    assert (
        result.tools[0].ok is False
    )  # a prova certa também é negada enquanto o pedido está bloqueado


def test_history_is_stored_without_the_verification(make: Make, tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    session = Session("s1")
    llm = FakeLLM(
        use("verify_customer", order_id=8, proof=ANA_PHONE_END),
        say("Prontinho, Ana, confirmei seus dados."),
    )
    result = make(llm).run_turn(session, "pedido 8, telefone final 7821, meu email é ana@jmail.com")
    store.save(session, result.new_messages)

    resumed = store.load("s1")
    assert resumed.guard.customer_id is None and len(resumed.history) == 2
    assert (
        "ana@jmail.com" not in resumed.history[0].content
        and "[email]" in resumed.history[0].content
    )

    again = FakeLLM(use("get_order_status", order_id=8), say("Preciso confirmar de novo."))
    outcome = make(again).run_turn(resumed, "e o rastreio?")
    assert outcome.tools[0].error_code == "not_verified"
    store.close()


def test_complaint_is_registered_with_a_contact(make: Make, tmp_path: Path) -> None:
    llm = FakeLLM(
        use(
            "register_complaint",
            summary="Violão chegou riscado, ligue (67) 99999-1234",
            contact="ana@jmail.com",
        ),
        say_from_tool(lambda d: f"Registrei! Protocolo {d['ticket_id']}, {d['sla']}."),
    )
    result = make(llm).run_turn(Session("s1"), "Meu violão chegou riscado!")

    record = json.loads((tmp_path / "complaints.jsonl").read_text(encoding="utf-8"))
    assert record["ticket_id"] in result.text and "retorno em até 24 horas úteis" in result.text
    assert record["summary"] == "Violão chegou riscado, ligue [telefone]"


def test_the_prompt_reflects_the_store_state_at_the_clock_time(make: Make) -> None:
    llm = FakeLLM(say("Oi!"))
    make(llm).run_turn(Session("s1"), "oi")
    assert "Loja aberta agora; fecha hoje às 18:00." in llm.calls[0].system
