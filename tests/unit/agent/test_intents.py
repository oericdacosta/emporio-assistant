from __future__ import annotations

import pytest

from emporio.agent.graph import Agent
from emporio.agent.intents import wrong_tool_correction
from emporio.agent.session import Session
from emporio.agent.types import Outcome
from support.fakes import FakeDetector, FakeLLM, StubToolbox, say, use

# G12, execução 2
G12_DRAFT = (
    "Pra Dourados o frete depende do CEP e do peso dos instrumentos, então não tenho um valor "
    "fixo aqui. A gente trabalha com Correios e Jadlog."
)
G12 = "Quanto fica o frete pra Dourados?"


@pytest.mark.parametrize(
    ("tools", "wrong"),
    [
        (["get_store_info"], True),
        ([], True),
        (["estimate_shipping"], False),
        (["search_policy"], False),
        (["get_store_info", "estimate_shipping"], False),
        (["get_order_status"], False),  # "enviado" do pedido é outro assunto
    ],
)
def test_shipping_needs_the_shipping_tools(tools: list[str], wrong: bool) -> None:
    correction = wrong_tool_correction(G12, G12_DRAFT, tools)
    assert (correction is not None) is wrong
    if correction:
        assert "estimate_shipping" in correction and "frete_entrega" in correction


def test_opening_hours_accept_get_store_info() -> None:
    draft = "Aos sábados a loja abre às 9h e fecha às 13h, conforme o horário de funcionamento."
    assert wrong_tool_correction("Que horas abrem sábado?", draft, ["get_store_info"]) is None
    assert wrong_tool_correction("Que horas abrem sábado?", draft, ["search_products"])


@pytest.mark.parametrize(
    "draft",
    [
        "Qual é a sua cidade?",
        "Pode me passar a cidade e o CEP para eu calcular o frete do produto?",
        "Não consigo ajudar com isso.",
    ],
)
def test_drafts_that_only_ask_or_refuse_are_not_a_violation(draft: str) -> None:
    assert wrong_tool_correction(G12, draft, []) is None


def test_questions_about_the_conversation_are_exempt() -> None:
    assert wrong_tool_correction("Qual frete eu perguntei antes?", G12_DRAFT, []) is None


def test_guard_retries_when_only_get_store_info_backs_a_shipping_answer() -> None:
    toolbox = StubToolbox(
        {
            "get_store_info": {"ok": True, "data": {}},
            "estimate_shipping": {"ok": True, "data": {}},
        }
    )
    llm = FakeLLM(
        use("get_store_info"),
        say(G12_DRAFT),
        use("estimate_shipping", city="Dourados"),
        say("Para Dourados o cálculo usa PAC, SEDEX ou Jadlog; me passa o CEP?"),
    )
    detector = FakeDetector(False)
    agent = Agent(llm, toolbox, lambda s: None, lambda s: "S", fact_detector=detector)
    result = agent.run_turn(Session("g12"), G12)
    assert result.outcome is Outcome.CORRECTED and not detector.calls
    assert "estimate_shipping" in llm.calls[2].messages[-1].content
