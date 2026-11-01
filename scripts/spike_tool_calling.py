"""Confere se o modelo escolhido chama as tools certas, com os argumentos certos, sem inventar valor.

Faz as quatro perguntas do enunciado contra as tools reais e mostra, por pergunta, as tools
chamadas, a latência, os tokens e se a resposta contém algum valor sem origem (a guarda de
fundamentação). Os tokens e a latência medidos aqui alimentam o custo do README.

Uso:  uv run python scripts/spike_tool_calling.py --model <modelo>
A chave vem da variável OPENAI_API_KEY (veja .env.example).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from emporio.agent.grounding import Ledger
from emporio.clock import SystemClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository, default_data_dir
from emporio.errors import ConfigError, EmporioError
from emporio.llm.base import LLMClient, Message, ToolSpec, Usage
from emporio.llm.factory import make_llm
from emporio.tools import build_registry
from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry

MAX_ITERATIONS = 5
SYSTEM = (
    "Você é o Téo, atendente da Empório da Música, uma loja de instrumentos musicais em "
    "Campo Grande. Responda em português, de forma curta e simpática. Nunca diga preço, "
    "estoque, horário ou prazo sem consultar as ferramentas. Para dados de um pedido, peça ao "
    "cliente o número do pedido e o e-mail ou os 4 últimos dígitos do telefone."
)


@dataclass
class Case:
    question: str
    expect: str  # o que conferir
    check: Callable[[list[tuple[str, dict]], str], bool]  # (tools chamadas, resposta final)


def _called(calls: list[tuple[str, dict]], name: str) -> list[dict]:
    return [args for tool, args in calls if tool == name]


CASES = [
    Case(
        "Quais opções de violões disponíveis custando até R$1000?",
        "search_products com max_price_brl=1000",
        lambda calls, answer: any(
            float(a.get("max_price_brl", 0)) == 1000 for a in _called(calls, "search_products")
        ),
    ),
    Case(
        "Qual o endereço da loja?",
        "get_store_info",
        lambda calls, answer: bool(_called(calls, "get_store_info")),
    ),
    Case(
        "Quanto custa o Takamine GD20?",
        "get_product e R$ 2.199,00",
        lambda calls, answer: (
            bool(_called(calls, "get_product") or _called(calls, "search_products"))
            and "2.199" in answer
        ),
    ),
    Case(
        "Me arrependi da minha compra, posso devolver meu pedido?",
        "pede o número do pedido e o e-mail, sem consultar pedido",
        lambda calls, answer: (
            not _called(calls, "get_order_status")
            and not _called(calls, "check_return_eligibility")
            and "pedido" in answer.lower()
            and ("e-mail" in answer.lower() or "email" in answer.lower())
        ),
    ),
]


@dataclass
class Outcome:
    answer: str = ""
    calls: list[tuple[str, dict]] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    seconds: float = 0.0
    usage: Usage = field(default_factory=Usage)


def ask(llm: LLMClient, registry: ToolRegistry, ctx: ToolContext, question: str) -> Outcome:
    tools = [ToolSpec(**spec) for spec in registry.specs()]
    messages = [Message("system", SYSTEM), Message("user", question)]
    ledger = Ledger.for_turn(question)
    outcome = Outcome()
    started = time.perf_counter()
    input_tokens = output_tokens = cached_tokens = 0
    for step in range(MAX_ITERATIONS):
        last = step == MAX_ITERATIONS - 1
        response = llm.chat(messages, tools, tool_choice="none" if last else "auto")
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        cached_tokens += response.usage.cached_tokens
        messages.append(response.message)
        if not response.message.tool_calls:
            outcome.answer = response.message.content
            break
        for call in response.message.tool_calls:
            result = registry.call(call.name, call.arguments, ctx)
            ledger.absorb(result)
            outcome.calls.append((call.name, call.arguments))
            messages.append(
                Message("tool", json.dumps(result, ensure_ascii=False), tool_call_id=call.id)
            )
    outcome.seconds = time.perf_counter() - started
    outcome.usage = Usage(input_tokens, output_tokens, cached_tokens)
    outcome.invented = ledger.violations(outcome.answer)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model")
    args = parser.parse_args()
    try:
        llm = make_llm(args.model, os.environ)
    except ConfigError as error:
        print(f"erro de configuração: {error}", file=sys.stderr)
        return error.exit_code

    data = default_data_dir()
    repo = CsvRepository.load(data)
    ctx = ToolContext(repo, SystemClock(), PolicyStore.load(data.parent))
    registry = build_registry(repo)

    print(f"modelo: {llm.model}\n")
    passed = 0
    total = Usage()
    for case in CASES:
        try:
            outcome = ask(llm, registry, ctx, case.question)
        except EmporioError as error:
            print(f"ERRO em {case.question!r}: {error}", file=sys.stderr)
            return 1
        right_tool = case.check(outcome.calls, outcome.answer)
        ok = right_tool and not outcome.invented
        passed += ok
        total = Usage(
            total.input_tokens + outcome.usage.input_tokens,
            total.output_tokens + outcome.usage.output_tokens,
            total.cached_tokens + outcome.usage.cached_tokens,
        )
        print(f"[{'OK' if ok else 'FALHOU'}] {case.question}")
        print(f"   esperado: {case.expect}")
        for name, call_args in outcome.calls:
            print(f"   tool: {name}({json.dumps(call_args, ensure_ascii=False)})")
        if outcome.invented:
            print(f"   valores sem origem: {outcome.invented}")
        print(f"   resposta: {outcome.answer}")
        print(
            f"   {outcome.seconds:.1f}s | tokens: {outcome.usage.input_tokens} entrada "
            f"({outcome.usage.cached_tokens} em cache), {outcome.usage.output_tokens} saída\n"
        )
    print(
        f"resultado: {passed}/{len(CASES)} | tokens totais: {total.input_tokens} entrada "
        f"({total.cached_tokens} em cache), {total.output_tokens} saída"
    )
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
