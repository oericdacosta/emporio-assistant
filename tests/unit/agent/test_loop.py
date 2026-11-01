from __future__ import annotations

import json
from typing import Any

import pytest

from emporio.agent.grounding import FALLBACK_MESSAGE
from emporio.agent.loop import (
    Agent,
    AgentConfig,
    Event,
    Outcome,
    ToolFinished,
    ToolStarted,
)
from emporio.agent.session import Session
from emporio.errors import ProviderUnavailable
from emporio.llm.base import Message, Usage
from support.fakes import FakeLLM, StubToolbox, say, say_from_tool, use, use_many

PRODUCT = {
    "ok": True,
    "data": {
        "name": "Takamine GD20",
        "effective_price_brl": "R$ 2.199,00",
        "pix_price_brl": "R$ 2.089,05",
    },
}


def make_agent(
    llm: FakeLLM, toolbox: StubToolbox | None = None, config: AgentConfig | None = None
) -> tuple[Agent[str], StubToolbox]:
    box = toolbox or StubToolbox({"get_product": PRODUCT})
    config = config or AgentConfig()
    agent = Agent(llm, box, lambda session: f"ctx-{session.id}", lambda session: "SISTEMA", config)
    return agent, box


def test_happy_path_consults_the_tool_and_answers() -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(lambda d: f"O {d['name']} sai por {d['effective_price_brl']}."),
    )
    agent, box = make_agent(llm)
    session = Session("s1")
    events: list[Event] = []

    result = agent.run_turn(session, "  quanto custa o gd20?  ", events.append)

    assert result.text == "O Takamine GD20 sai por R$ 2.199,00."
    assert result.outcome is Outcome.ANSWERED and not result.guard_blocked and not result.fallback
    assert [t.name for t in result.tools] == ["get_product"] and result.tools[0].ok
    assert result.llm_calls == 2 and result.usage == Usage(20, 10, 0) and result.trace_id
    assert box.calls == [("get_product", {"name": "gd20"}, "ctx-s1")]
    llm.assert_exhausted()

    first, second = llm.calls
    assert first.system == "SISTEMA" and first.tool_choice == "auto" and first.tools
    assert first.temperature == 0.2 and first.max_tokens == 600
    assert [m.role for m in second.messages] == ["system", "user", "assistant", "tool"]
    assert second.messages[1].content == "quanto custa o gd20?"
    assert json.loads(second.messages[3].content) == PRODUCT

    assert [type(e) for e in events] == [ToolStarted, ToolFinished]
    assert isinstance(events[1], ToolFinished) and events[1].ok and events[1].result == PRODUCT
    assert session.history == list(result.new_messages)
    assert session.approved_answers == [result.text]


def test_invented_price_is_corrected_with_one_retry() -> None:
    llm = FakeLLM(
        use("get_product", name="gd20"),
        say("O GD20 sai por R$ 1.999,00."),
        say("O GD20 sai por R$ 2.199,00."),
    )
    agent, _ = make_agent(llm)
    session = Session("s1")

    result = agent.run_turn(session, "quanto custa o gd20?")

    assert result.text == "O GD20 sai por R$ 2.199,00."
    assert result.outcome is Outcome.CORRECTED and result.guard_blocked
    retry_messages = llm.calls[2].messages
    assert retry_messages[-2] == Message("assistant", "O GD20 sai por R$ 1.999,00.")
    assert "R$ 1.999,00" in retry_messages[-1].content and retry_messages[-1].role == "user"
    assert session.approved_answers == ["O GD20 sai por R$ 2.199,00."]
    assert all("1.999" not in m.content for m in session.history)  # a resposta barrada não fica


def test_persistent_invention_gets_the_safe_fallback() -> None:
    llm = FakeLLM(say("Sai por R$ 1.999,00."), say("Sai por R$ 1.888,00."))
    agent, _ = make_agent(llm)
    session = Session("s1")

    result = agent.run_turn(session, "quanto custa o gd20?")

    assert result.text == FALLBACK_MESSAGE and result.fallback and result.guard_blocked
    assert "R$" not in result.text
    assert session.history[-1] == Message("assistant", FALLBACK_MESSAGE)
    assert session.approved_answers == []  # o fallback não alimenta a guarda do próximo turno


def test_a_tool_error_goes_back_to_the_model_as_data() -> None:
    error = {"ok": False, "error": {"code": "product_not_found", "message": "x", "hint": "y"}}
    llm = FakeLLM(use("get_product", name="xyz"), say("Não achei esse modelo. Qual é?"))
    agent, _ = make_agent(llm, StubToolbox({"get_product": error}))

    result = agent.run_turn(Session("s1"), "tem o xyz?")

    assert result.outcome is Outcome.ANSWERED
    assert result.tools[0].ok is False and result.tools[0].error_code == "product_not_found"
    assert json.loads(llm.calls[1].messages[-1].content) == error


def test_an_exception_in_a_tool_never_reaches_the_loop() -> None:
    box = StubToolbox(raises={"get_product": RuntimeError("segredo interno")})
    llm = FakeLLM(use("get_product", name="gd20"), say("Não consegui consultar agora."))
    agent, _ = make_agent(llm, box)

    result = agent.run_turn(Session("s1"), "quanto custa?")

    seen = llm.calls[1].messages[-1].content
    assert json.loads(seen)["error"]["code"] == "internal_error" and "segredo" not in seen
    assert result.tools[0].error_code == "internal_error" and not result.fallback


def test_iteration_limit_forces_a_final_answer_without_tools() -> None:
    config = AgentConfig(max_iterations=3)
    llm = FakeLLM(
        use("get_product", name="a"),
        use("get_product", name="b"),
        say("Com o que consultei, o GD20 sai por R$ 2.199,00."),
    )
    agent, _ = make_agent(llm, config=config)

    result = agent.run_turn(Session("s1"), "me ajuda")

    assert result.llm_calls == 3 and result.outcome is Outcome.ANSWERED
    last = llm.calls[-1]
    assert last.tools == [] and last.tool_choice == "none"
    assert "Responda agora" in last.messages[-1].content
    assert all(call.tool_choice == "auto" and call.tools for call in llm.calls[:-1])


def test_a_model_that_keeps_asking_for_tools_ends_in_the_fallback() -> None:
    llm = FakeLLM(use("get_product", name="a"), use("get_product", name="b"))
    agent, _ = make_agent(llm, config=AgentConfig(max_iterations=2))
    result = agent.run_turn(Session("s1"), "me ajuda")
    assert result.fallback  # na última volta pediu tool de novo e não escreveu resposta


def test_follow_up_reuses_approved_answers_but_a_new_session_does_not() -> None:
    session = Session("s1")
    first = FakeLLM(
        use("get_product", name="gd20"),
        say_from_tool(lambda d: f"Sai por {d['effective_price_brl']}."),
    )
    make_agent(first)[0].run_turn(session, "quanto custa o gd20?")

    repeated = FakeLLM(say("Continua R$ 2.199,00."))
    assert make_agent(repeated)[0].run_turn(session, "e o preço?").outcome is Outcome.ANSWERED

    # o PIX nunca passou por uma tool nesta conversa: não vale de memória
    invented = FakeLLM(say("No PIX fica R$ 2.089,05."), say("No PIX fica R$ 2.089,05."))
    assert make_agent(invented)[0].run_turn(session, "e no pix?").fallback

    stranger = FakeLLM(say("Continua R$ 2.199,00."), say("Continua R$ 2.199,00."))
    assert make_agent(stranger)[0].run_turn(Session("outra"), "e o preço?").fallback


def test_the_budget_the_customer_quoted_is_not_a_hallucination() -> None:
    ok = FakeLLM(say("Dentro do seu teto de R$ 1.000,00 tenho estas opções."))
    assert (
        make_agent(ok)[0].run_turn(Session("s1"), "violões até R$1000").outcome is Outcome.ANSWERED
    )

    invented = FakeLLM(say("Tenho um por R$ 999,00."), say("Tenho um por R$ 999,00."))
    result = make_agent(invented)[0].run_turn(Session("s2"), "violões até R$1000")
    assert result.fallback and result.guard_blocked


def test_a_provider_failure_leaves_the_session_untouched() -> None:
    llm = FakeLLM(use("get_product", name="gd20"), ProviderUnavailable("fora do ar"))
    agent, _ = make_agent(llm)
    session = Session("s1")

    with pytest.raises(ProviderUnavailable):
        agent.run_turn(session, "quanto custa?")

    assert session.history == [] and session.approved_answers == []


def test_empty_reply_gets_one_retry_then_the_fallback() -> None:
    recovered = FakeLLM(say("  "), say("Oi! Em que posso ajudar?"))
    result = make_agent(recovered)[0].run_turn(Session("s1"), "oi")
    assert result.outcome is Outcome.CORRECTED and not result.guard_blocked

    hopeless = FakeLLM(say(""), say(""))
    assert make_agent(hopeless)[0].run_turn(Session("s2"), "oi").fallback


def test_empty_customer_message_is_rejected() -> None:
    with pytest.raises(ValueError, match="vazia"):
        make_agent(FakeLLM())[0].run_turn(Session("s1"), "   ")


def test_calls_beyond_the_per_step_cap_are_refused_but_answered() -> None:
    calls = [("get_product", {"name": str(i)}) for i in range(5)]
    llm = FakeLLM(use_many(*calls), say("Pronto."))
    agent, box = make_agent(llm)

    result = agent.run_turn(Session("s1"), "compara tudo")

    assert len(box.calls) == 3
    seen = [m for m in llm.calls[1].messages if m.role == "tool"]
    assert len(seen) == 5  # toda chamada recebe um resultado
    assert json.loads(seen[-1].content)["error"]["code"] == "too_many_calls"
    assert [t.error_code for t in result.tools][-2:] == ["too_many_calls", "too_many_calls"]


def test_text_that_comes_with_tool_calls_is_dropped() -> None:
    llm = FakeLLM(
        use_many(("get_product", {"name": "a"}), text="Vou ver, sai por R$ 1,00"), say("Pronto.")
    )
    agent, _ = make_agent(llm)
    assert agent.run_turn(Session("s1"), "oi").text == "Pronto."
    assistant = next(m for m in llm.calls[1].messages if m.role == "assistant")
    assert assistant.content == "" and assistant.tool_calls


def test_system_prompt_is_rebuilt_on_every_call() -> None:
    seen: list[str] = []

    def prompt(session: Session) -> str:
        seen.append(f"v{len(seen)}")
        return seen[-1]

    llm = FakeLLM(use("get_product", name="a"), say("Pronto."))
    agent = Agent(llm, StubToolbox({"get_product": PRODUCT}), lambda s: None, prompt)
    agent.run_turn(Session("s1"), "oi")
    assert [call.system for call in llm.calls] == ["v0", "v1"]


def test_only_the_recent_window_is_sent() -> None:
    session = Session("s1")
    for i in range(15):
        session.history += [Message("user", f"p{i}"), Message("assistant", f"r{i}")]
    llm = FakeLLM(say("ok"))
    make_agent(llm)[0].run_turn(session, "nova")
    sent = llm.calls[0].messages
    assert sent[0].role == "system" and sent[1].content == "p5" and sent[-1].content == "nova"
    assert len(sent) == 1 + 20 + 1


def test_events_report_every_tool_with_its_result() -> None:
    llm = FakeLLM(
        use_many(("get_product", {"name": "a"}), ("get_product", {"name": "b"})), say("Pronto.")
    )
    events: list[Any] = []
    make_agent(llm)[0].run_turn(Session("s1"), "oi", events.append)
    assert [type(e).__name__ for e in events] == ["ToolStarted", "ToolFinished"] * 2
    assert events[0].arguments == {"name": "a"} and events[3].seconds >= 0
