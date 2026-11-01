"""O agente montado de verdade (tools reais, CSVs reais, relógio fixo) com um modelo roteirizado."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from emporio.agent.graph import Agent
from emporio.agent.session import Session, SQLiteSessionStore, new_session_id
from emporio.agent.types import Outcome
from emporio.app import build_agent
from emporio.clock import FixedClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.identity import IdentityVerifier
from emporio.tools.deps import ToolContext
from support.fakes import FakeDetector, FakeLLM, say, say_from_tool, use

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
        fact_detector=FakeDetector(),
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
        # turno 2: o orquestrador verifica (pedido e prova na fala) antes do modelo
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
    assert "Cliente verificado" not in systems[0]
    assert "Cliente verificado: Ana." in systems[1]  # o nome aparece logo depois da verificação

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


def test_search_policy_sees_the_customer_text_through_the_tool_context(make: Make) -> None:
    llm = FakeLLM(
        use("search_policy", topics=["empresa"]),
        say("O manual não fala disso; vou confirmar com a equipe e te retorno."),
    )
    result = make(llm).run_turn(Session("s1"), "Vocês têm estacionamento?")
    tool_message = next(m for m in llm.calls[1].messages if m.role == "tool")
    data = json.loads(tool_message.content)["data"]
    assert data["coverage"] == "none" and "estacionamento" in data["unmatched_terms"]
    assert result.outcome is Outcome.ANSWERED


def test_c18_complaint_resumes_when_the_customer_gives_a_phone(make: Make, tmp_path: Path) -> None:
    llm = FakeLLM(
        say("Sinto muito pelo defeito. Qual o número do pedido?"),
        use("register_complaint", summary="Ukulele chegou com defeito"),
        say("Sem o pedido posso registrar com um e-mail ou telefone. Qual prefere?"),
        # turno 3: o orquestrador registra antes do modelo; ele só redige
        say_from_tool(lambda d: f"Pronto! Protocolo {d['ticket_id']}, {d['sla']}."),
    )
    agent = make(llm)
    session = Session("c18")
    agent.run_turn(session, "Meu ukulele chegou com defeito")
    agent.run_turn(session, "não tenho o número do pedido")
    assert session.working.pending_complaint is not None
    assert any("Sistema: o cliente não tem o número" in c.system for c in llm.calls[1:3])

    result = agent.run_turn(session, "pode me ligar no (67) 99999-1234")

    (record,) = [json.loads(x) for x in (tmp_path / "complaints.jsonl").read_text().splitlines()]
    assert record["contact"] == "(67) 99999-1234"
    assert result.outcome is Outcome.ANSWERED and record["ticket_id"] in result.text
    assert [t.name for t in result.tools] == ["register_complaint"] and result.tools[0].ok
    assert session.working.pending_complaint is None and session.working.complaint_ticket
    assert len(llm.calls) == 4  # um só chamada do modelo no turno 3
    llm.assert_exhausted()


def test_resume_does_nothing_when_the_message_has_no_contact(make: Make) -> None:
    llm = FakeLLM(say("Pode me passar um contato?"))
    session = Session("s")
    session.working.pending_complaint = {"summary": "defeito", "order_id": None}
    result = make(llm).run_turn(session, "ainda não achei o pedido")
    assert result.tools == () and session.working.pending_complaint is not None


def test_an_invented_price_cap_is_blocked_and_traced(make: Make) -> None:
    llm = FakeLLM(
        use("search_products", category="violoes", max_price_brl=800),
        say("Qual faixa de preço você procura?"),
    )
    result = make(llm).run_turn(Session("s"), "violão pra iniciante, barato, nylon")
    assert [t.error_code for t in result.tools] == ["unsupported_filter"]


def test_trace_counts_blocked_calls_and_relaxed_searches(
    make: Make, monkeypatch: pytest.MonkeyPatch
) -> None:
    from emporio import observability

    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(observability, "annotate", seen.append)
    blocked = FakeLLM(use("search_products", max_price_brl=800), say("Qual faixa de preço?"))
    make(blocked).run_turn(Session("a"), "violão barato")
    relaxed = FakeLLM(
        use("search_products", query="violão zzxqw", category="violoes"),
        say_from_tool(lambda d: f"Achei {d['total_matches']} violões."),
    )
    make(relaxed).run_turn(Session("b"), "violão zzxqw")
    assert (seen[0]["tool_blocked"], seen[0]["relaxed_search"]) == (1, False)
    assert (seen[1]["tool_blocked"], seen[1]["relaxed_search"]) == (0, True)


def records(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in (tmp_path / "complaints.jsonl").read_text().splitlines()]


def test_c01_complaint_said_earlier_is_registered_when_the_email_arrives(
    make: Make, tmp_path: Path
) -> None:
    llm = FakeLLM(
        say("Sinto muito pelo transtorno! Você tem o número do pedido?"),
        say("Sem o pedido eu registro com um e-mail ou telefone. Qual prefere?"),
        say_from_tool(lambda d: f"Pronto! Protocolo {d['ticket_id']}, {d['sla']}."),
    )
    agent, session = make(llm), Session("c01")
    agent.run_turn(session, "O teclado que comprei não liga, chegou assim.")
    agent.run_turn(session, "Não sei o número do pedido")
    result = agent.run_turn(session, "Pode me responder no e-mail ana.teste@exemplo.com.br")

    (record,) = records(tmp_path)
    assert record["contact"] == "ana.teste@exemplo.com.br" and "teclado" in record["summary"]
    assert [t.name for t in result.tools] == ["register_complaint"]
    assert record["ticket_id"] in result.text and len(llm.calls) == 3
    llm.assert_exhausted()


def test_c02_complaint_and_phone_in_the_same_message_are_registered_by_the_orchestrator(
    make: Make, tmp_path: Path
) -> None:
    llm = FakeLLM(say_from_tool(lambda d: f"Registrado! Protocolo {d['ticket_id']}."))
    result = make(llm).run_turn(
        Session("c02"), "Recebi o violão com a tarraxa quebrada, pode me ligar no (67) 98888-7777?"
    )
    (record,) = records(tmp_path)
    assert record["contact"] == "(67) 98888-7777" and "[telefone]" in record["summary"]
    assert [t.name for t in result.tools] == ["register_complaint"] and len(llm.calls) == 1


def test_a_message_without_complaint_does_not_register_anything(make: Make) -> None:
    llm = FakeLLM(say("Claro! Qual violão você procura para o seu filho iniciante?"))
    result = make(llm).run_turn(Session("s"), "Meu telefone é (67) 98888-7777, quero um violão")
    assert result.tools == ()


def test_two_registration_requests_with_the_same_contact_make_one_ticket(
    make: Make, tmp_path: Path
) -> None:
    llm = FakeLLM(
        say_from_tool(lambda d: f"Protocolo {d['ticket_id']}."),
        use("register_complaint", summary="Teclado que não liga", contact="(67) 98888-7777"),
        say_from_tool(lambda d: f"Já existe: {d['ticket_id']}."),
    )
    agent, session = make(llm), Session("dup")
    agent.run_turn(session, "O teclado não liga, me ligue no (67) 98888-7777")
    second = agent.run_turn(session, "Registra de novo aí por favor")
    assert len(records(tmp_path)) == 1
    assert second.tools[0].ok and session.working.complaint_ticket in second.text


def test_m02_promise_to_update_the_contact_is_blocked(make: Make) -> None:
    promise = "Anotado, Isabela! Vou atualizar seu contato para o telefone (67) 98888-7777."
    llm = FakeLLM(
        say(promise),
        say("Isso a equipe faz: vou pedir que entrem em contato. Algo mais?"),
    )
    result = make(llm).run_turn(Session("m02"), "Atualiza meu telefone pra (67) 98888-7777")
    assert result.outcome is Outcome.CORRECTED and "atualizar seu contato" not in result.text
    assert "register_complaint" in llm.calls[1].messages[-1].content


def test_g31_order_and_proof_in_one_message_are_verified_by_the_orchestrator(
    make: Make,
) -> None:
    llm = FakeLLM(
        use("get_order_status", order_id=17),
        say_from_tool(lambda d: f"Seu pedido 17 está {d['status_pt']}."),
    )
    session = Session("g31")
    result = make(llm).run_turn(session, "Status do 17? matheus.correia@hayoo.com.br")
    assert [t.name for t in result.tools] == ["verify_customer", "get_order_status"]
    assert all(t.ok for t in result.tools) and session.working.verified_orders == [17]
    # o par sintético está nas mensagens que o modelo viu
    assistant = next(m for m in llm.calls[0].messages if m.tool_calls)
    assert assistant.tool_calls[0].name == "verify_customer"
    assert any(m.role == "tool" and '"verified": true' in m.content for m in llm.calls[0].messages)
    assert "cancelado" in result.text
    llm.assert_exhausted()


def test_g32_proof_in_a_later_turn_uses_the_last_cited_order(make: Make) -> None:
    llm = FakeLLM(
        say("Qual o e-mail do pedido 3?"),
        say("Qual o e-mail ou final do telefone do pedido 4?"),
        use("get_order_status", order_id=4),
        say_from_tool(lambda d: f"Seu pedido 4 está {d['status_pt']}."),
    )
    agent, session = make(llm), Session("g32")
    agent.run_turn(session, "Pedido 3, isabela.moreira@jmail.com")  # tem prova: verifica o 3
    assert session.working.verified_orders == [3]
    agent.run_turn(session, "Agora o pedido 4")  # sem prova: não dispara
    assert session.working.verified_orders == [3]
    result = agent.run_turn(session, "O e-mail é lucas.mendes@jmail.com")
    assert [t.name for t in result.tools] == ["verify_customer", "get_order_status"]
    assert session.working.verified_orders == [3, 4]


def test_orchestrator_verification_failure_reaches_the_model_and_the_ledger(make: Make) -> None:
    llm = FakeLLM(say("Não consegui confirmar o pedido 4 com esse e-mail."))
    result = make(llm).run_turn(Session("f"), "Pedido 4, isabela.moreira@jmail.com")
    assert [(t.name, t.ok, t.error_code) for t in result.tools] == [
        ("verify_customer", False, "not_verified")
    ]
    assert result.outcome is Outcome.ANSWERED  # o Ledger viu a falha: a frase tem origem
    assert any(m.role == "tool" and "not_verified" in m.content for m in llm.calls[0].messages)


def test_no_proof_or_already_verified_order_does_not_verify_again(make: Make) -> None:
    llm = FakeLLM(say("Me passa o e-mail do pedido."), say("Pronto."), say("Certo."))
    agent, session = make(llm), Session("n")
    assert agent.run_turn(session, "Status do pedido 4").tools == ()
    assert agent.run_turn(session, "lucas.mendes@jmail.com").tools[0].name == "verify_customer"
    assert agent.run_turn(session, "é lucas.mendes@jmail.com mesmo").tools == ()
