"""Os passos de um turno que não dependem de como o turno é orquestrado.

O grafo (``graph.py``) chama estas funções: executar as tools e fechar o turno são código comum,
testável sem o LangGraph.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langsmith import traceable

from emporio.agent.grounding import Ledger
from emporio.agent.session import Session
from emporio.agent.state import WorkingState
from emporio.agent.types import (
    EventHook,
    Meter,
    Outcome,
    Toolbox,
    ToolFinished,
    ToolRecord,
    ToolStarted,
    TurnResult,
    failure,
)
from emporio.llm.base import Message, ToolCall

log = logging.getLogger(__name__)


@traceable(
    run_type="tool",
    process_inputs=lambda inputs: {
        "arguments": inputs.get("arguments")
    },  # sem máscara, de propósito
)
def _call_tool[C](toolbox: Toolbox[C], name: str, arguments: Any, ctx: C) -> dict[str, Any]:
    return toolbox.call(name, arguments, ctx)


def run_tools[C](
    toolbox: Toolbox[C],
    ctx: C,
    calls: tuple[ToolCall, ...],
    messages: list[Message],
    ledger: Ledger,
    meter: Meter,
    *,
    max_calls: int,
    on_event: EventHook | None,
    working: WorkingState | None = None,
) -> None:
    """Executa as tools que o modelo pediu, em sequência, e anexa os resultados a ``messages``."""
    # o texto que acompanha as chamadas é descartado: só a resposta final é entregue
    messages.append(Message("assistant", "", calls))
    for index, call in enumerate(calls):
        if index >= max_calls:
            result = failure(
                "too_many_calls",
                "chamadas demais de uma vez",
                f"Faça no máximo {max_calls} chamadas por vez.",
            )
            seconds = 0.0
        elif working is not None and working.tripped(call.name):
            result = failure(
                "repeated_failure",
                f"{call.name} já falhou duas vezes pelo mesmo motivo",
                "Essa chamada já falhou duas vezes pelo mesmo motivo: pare de tentar; explique "
                "ao cliente o que falta ou ofereça encaminhar para a equipe.",
            )
            seconds = 0.0
        else:
            if on_event:
                on_event(ToolStarted(call.name, call.arguments))
            began = time.perf_counter()
            try:
                result = _call_tool(
                    toolbox, call.name, call.arguments, ctx, langsmith_extra={"name": call.name}
                )
            except Exception:
                log.exception("falha inesperada na tool %s", call.name)
                result = failure("internal_error", "a consulta falhou por um problema nosso")
            seconds = time.perf_counter() - began
        ledger.absorb(result, call.name)
        meter.tool_results.append((call.name, result))
        if working is not None:
            working.absorb(call.name, call.arguments, result)
        content = json.dumps(result, ensure_ascii=False, default=str)
        messages.append(Message("tool", content, tool_call_id=call.id))
        error: dict[str, Any] = result.get("error") or {}
        meter.tools.append(
            ToolRecord(
                call.name,
                call.arguments,
                bool(result.get("ok")),
                error.get("code"),
                seconds,
                len(content),
            )
        )
        if on_event:
            on_event(ToolFinished(call.name, bool(result.get("ok")), seconds, result))


def finish(
    session: Session,
    user: Message,
    text: str,
    outcome: Outcome,
    meter: Meter,
    trace_id: str,
    started: float,
) -> TurnResult:
    """Fecha o turno: é o único ponto que altera a sessão, e só depois de um turno bem-sucedido."""
    answer = Message("assistant", text)
    session.history += [user, answer]
    if outcome is not Outcome.FALLBACK:
        session.approved_answers.append(text)  # só o que passou pela guarda alimenta a próxima
    return TurnResult(
        text=text,
        outcome=outcome,
        new_messages=(user, answer),
        tools=tuple(meter.tools),
        usage=meter.usage,
        llm_calls=meter.calls,
        seconds=time.perf_counter() - started,
        trace_id=trace_id,
        guard_blocked=meter.guard_blocked,
        guard_blocks=tuple(meter.guard_blocks),
        offers_removed=meter.offers_removed,
        working_products=len(session.working.products),
        route=meter.route,
        no_evidence_gate=meter.no_evidence_gate,
        detector_calls=meter.detector_calls,
    )
