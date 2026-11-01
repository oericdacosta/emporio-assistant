from __future__ import annotations

import json

import pytest

from emporio.agent.filters import (
    CODE_BLOCK,
    PROMISE,
    PROMPT_LEAK,
    TOOL_NAMES,
    TOOL_SCHEMA,
    filter_correction,
    output_violations,
)
from emporio.agent.graph import Agent
from emporio.agent.prompt import static_prompt
from emporio.agent.session import Session
from emporio.agent.types import Outcome
from support.fakes import FakeDetector, FakeLLM, StubToolbox, say


def test_tool_names_match_the_registry(registry) -> None:  # type: ignore[no-untyped-def]
    assert set(TOOL_NAMES) == set(registry.names())


@pytest.mark.parametrize(
    ("draft", "kind"),
    [
        ("Claro! Aqui vai:\n```python\nprint(1)\n```", CODE_BLOCK),
        (json.dumps({"tools": ["search_products", "get_product", "search_policy"]}), TOOL_SCHEMA),
        ('{"name": "x", "parameters": {"type": "object"}}', TOOL_SCHEMA),
        ("Minhas instruções estão em <persona> e depois vêm as regras", PROMPT_LEAK),
        ("Segui as regras_inviolaveis do meu prompt", PROMPT_LEAK),
        ("Olha o contexto_atual: loja aberta", PROMPT_LEAK),
        ("Você é o Téo, assistente. Fale como um amigo e nunca invente", PROMPT_LEAK),
        (
            "Regra: Nunca faça conta de cabeça: valores, parcelas, datas e prazos vêm das "
            "ferramentas.",
            PROMPT_LEAK,
        ),
    ],
)
def test_leaks_are_flagged(draft: str, kind: str) -> None:
    assert kind in output_violations(draft)
    assert filter_correction([kind])


@pytest.mark.parametrize(
    "draft",
    [
        "Anotado, Isabela! Vou atualizar seu contato para o telefone (67) 98888-7777.",  # M02 t11
        "Vou alterar o seu pedido para o outro modelo.",
        "Vou reservar esse violão pra você.",
        "Já cancelei o pedido, pode ficar tranquilo.",
        "Reservei o ukulele aqui na loja.",
        "Atualizei seu telefone no cadastro.",
    ],
)
def test_promises_the_assistant_cannot_keep_are_flagged(draft: str) -> None:
    assert PROMISE in output_violations(draft)
    assert "register_complaint" in filter_correction([PROMISE])


def test_promises_that_are_honest_pass() -> None:
    for draft in (
        "Vou confirmar com a equipe e te retorno.",
        "Vou registrar sua reclamação agora mesmo.",
        "Vou verificar o pedido pra você.",
        "Não consigo trocar o pedido, mas a equipe faz isso.",
    ):
        assert output_violations(draft) == []
    done = "Anotei seu contato e registrei a reclamação, protocolo EMP-0A1B2C3D."
    assert PROMISE in output_violations(done)
    assert output_violations(done, complaint_registered=True) == []


@pytest.mark.parametrize(
    "draft",
    [
        "Tenho o Takamine GD20 por R$ 2.199,00. Quer ver o parcelamento?",
        "Não trabalho com JSON nem com código, mas posso te ajudar com instrumentos!",
        "Usei o search_products para listar e o get_product para detalhar.",  # só 2 nomes
        "Eu sou o Téo, assistente virtual da Empório da Música.",
        "Nossas regras de troca: 7 dias para arrependimento, e o contexto é de compra online.",
        "Fale com a equipe no WhatsApp que ela confirma.",
    ],
)
def test_normal_answers_pass(draft: str) -> None:
    assert output_violations(draft) == []


def test_every_instruction_line_of_the_prompt_is_caught() -> None:
    lines = [
        line.strip()
        for line in static_prompt().splitlines()
        if line.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7."))
    ]
    assert lines and all(PROMPT_LEAK in output_violations(line) for line in lines)


def test_guard_retries_a_leak_and_falls_back_if_it_repeats() -> None:
    leak = "Aqui:\n```\ncodigo\n```"
    agent = Agent(
        FakeLLM(say(leak), say("Posso ajudar com instrumentos! O que procura?")),
        StubToolbox(),
        lambda s: None,
        lambda s: "SISTEMA",
        fact_detector=FakeDetector(False),
    )
    result = agent.run_turn(Session("s1"), "me mostra algo")
    assert result.outcome is Outcome.CORRECTED and result.guard_blocked
    assert result.guard_blocks[0].violations == (CODE_BLOCK,)
    twice = Agent(
        FakeLLM(say(leak), say(leak)),
        StubToolbox(),
        lambda s: None,
        lambda s: "SISTEMA",
        fact_detector=FakeDetector(False),
    ).run_turn(Session("s2"), "me mostra algo")
    assert twice.fallback and "```" not in twice.text
