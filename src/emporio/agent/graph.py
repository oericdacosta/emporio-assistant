r"""O turno do agente, como um grafo LangGraph.

    START -> model --(pediu tools)--> tools --> model
                  \--(respondeu)---> guard --(ok)-----------> END
                                           \--(barrou, 1ª vez)--> model
                                           \--(barrou de novo)--> fallback --> END

O modelo é o ``LLMClient``, as tools vêm do ``Toolbox``, a guarda é o ``Ledger`` e a execução das
tools e o fechamento do turno estão em ``steps``: o LangGraph só orquestra, e o prompt que chega ao
provedor é o mesmo que os cassetes gravaram.
O grafo é compilado sem checkpointer de propósito: o histórico é o do ``SQLiteSessionStore``, que
nunca grava resultado de tool nem a verificação do cliente. A sessão só muda em ``finish``, depois
de um ``invoke`` bem-sucedido; uma falha do provedor sobe como exceção e deixa tudo como estava.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import traceable, uuid7

from emporio import observability
from emporio.agent.grounding import FALLBACK_MESSAGE, Ledger, correction_message
from emporio.agent.prompt import prompt_version
from emporio.agent.session import Session
from emporio.agent.steps import finish, run_tools
from emporio.agent.types import (
    EMPTY_REPLY_MESSAGE,
    FORCE_ANSWER_MESSAGE,
    AgentConfig,
    EventHook,
    GuardBlock,
    Meter,
    Outcome,
    Toolbox,
    TurnResult,
)
from emporio.llm.base import LLMClient, Message, ToolSpec

log = logging.getLogger(__name__)

Verdict = Literal["ok", "retry", "fallback"]


class TurnState(TypedDict):
    """O que o turno carrega. ``ledger`` e ``meter`` são mutáveis (não há checkpointer)."""

    messages: list[Message]  # sem o prompt de sistema: ele é refeito a cada chamada
    ledger: Ledger
    meter: Meter
    trace_id: str
    step: int  # chamadas ao modelo já feitas; é o limite real de iterações
    forced: bool  # a última chamada não oferece tools
    reply: Message
    retried: bool
    verdict: Verdict
    text: str
    outcome: Outcome


@dataclass(frozen=True, slots=True)
class TurnContext:
    """O que vale só para este turno e não é estado: sessão e quem ouve os eventos."""

    session: Session
    on_event: EventHook | None


class Agent[C]:
    def __init__(
        self,
        llm: LLMClient,
        toolbox: Toolbox[C],
        make_ctx: Callable[[Session], C],
        system_prompt: Callable[[Session], str],
        config: AgentConfig = AgentConfig(),  # noqa: B008
    ) -> None:
        self._llm = llm
        self._toolbox = toolbox
        self._make_ctx = make_ctx
        self._system_prompt = system_prompt
        self._config = config
        self._tools = tuple(ToolSpec(**spec) for spec in toolbox.specs())
        self._graph = self._build()

    # --- o grafo -------------------------------------------------------------------------------

    def _build(self) -> Any:
        graph = StateGraph(TurnState, context_schema=TurnContext)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._run_tools)
        graph.add_node("guard", self._guard)
        graph.add_node("fallback", self._fallback)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model", self._after_model, {"tools": "tools", "guard": "guard"}
        )
        graph.add_edge("tools", "model")
        graph.add_conditional_edges(
            "guard", self._after_guard, {"end": END, "model": "model", "fallback": "fallback"}
        )
        graph.add_edge("fallback", END)
        return graph.compile()

    def _model(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        forced = state["step"] == self._config.max_iterations - 1
        messages = state["messages"]
        if forced:
            messages = [*messages, Message("user", FORCE_ANSWER_MESSAGE)]
        offer_tools = not forced
        response = self._llm.chat(
            [Message("system", self._system_prompt(runtime.context.session)), *messages],
            self._tools if offer_tools else (),
            tool_choice="auto" if offer_tools else "none",
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        state["meter"].add(response)
        return {
            "messages": messages,
            "reply": response.message,
            "forced": forced,
            "step": state["step"] + 1,
        }

    @staticmethod
    def _after_model(state: TurnState) -> Literal["tools", "guard"]:
        return "tools" if state["reply"].tool_calls and not state["forced"] else "guard"

    def _run_tools(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        messages = list(state["messages"])
        run_tools(
            self._toolbox,
            self._make_ctx(runtime.context.session),
            state["reply"].tool_calls,
            messages,
            state["ledger"],
            state["meter"],
            max_calls=self._config.max_calls_per_step,
            on_event=runtime.context.on_event,
        )
        return {"messages": messages}

    def _guard(self, state: TurnState) -> dict[str, Any]:
        draft = state["reply"].content.strip()
        violations = state["ledger"].violations(draft)
        if violations:
            problem: str | None = correction_message(violations)
        elif not draft:
            problem = EMPTY_REPLY_MESSAGE
        else:
            problem = None
        if problem is None:
            outcome = Outcome.CORRECTED if state["retried"] else Outcome.ANSWERED
            return {"verdict": "ok", "text": draft, "outcome": outcome}
        meter = state["meter"]
        meter.guard_blocked = meter.guard_blocked or bool(violations)
        if violations:
            meter.guard_blocks.append(GuardBlock(draft, tuple(dict.fromkeys(violations))))
        log.info(
            "resposta barrada", extra={"trace_id": state["trace_id"], "retry": not state["retried"]}
        )
        # a nova tentativa gasta uma iteração: sem iteração sobrando, vai direto à resposta segura
        if state["retried"] or state["step"] >= self._config.max_iterations:
            return {"verdict": "fallback"}
        messages = [
            *state["messages"],
            Message("assistant", draft or "(vazio)"),
            Message("user", problem),
        ]
        return {"verdict": "retry", "retried": True, "messages": messages}

    @staticmethod
    def _after_guard(state: TurnState) -> Literal["end", "model", "fallback"]:
        match state["verdict"]:
            case "ok":
                return "end"
            case "retry":
                return "model"
            case _:
                return "fallback"

    @staticmethod
    def _fallback(state: TurnState) -> dict[str, Any]:
        return {"text": FALLBACK_MESSAGE, "outcome": Outcome.FALLBACK}

    # --- o turno -------------------------------------------------------------------------------

    def run_turn(
        self, session: Session, text: str, on_event: EventHook | None = None
    ) -> TurnResult:
        text = text.strip()
        if not text:
            raise ValueError("a mensagem do cliente está vazia")
        run_id = uuid7()  # o id do run no LangSmith é o trace_id do turno
        return self._traced_turn(
            session,
            text,
            on_event,
            str(run_id),
            langsmith_extra={"run_id": run_id, "metadata": self._turn_metadata(session)},
        )

    def _turn_metadata(self, session: Session) -> dict[str, str]:
        metadata = {"session_id": session.id, "model": str(getattr(self._llm, "model", "?"))}
        with suppress(Exception):
            clock = getattr(self._make_ctx(session), "clock", None)
            if clock is not None:
                metadata["clock"] = clock.now().isoformat(timespec="seconds")
        return metadata

    @traceable(
        name="turno",
        run_type="chain",
        process_inputs=lambda inputs: {"text": inputs.get("text")},
        process_outputs=lambda result: {"text": getattr(result, "text", None)},
    )
    def _traced_turn(
        self, session: Session, text: str, on_event: EventHook | None, trace_id: str
    ) -> TurnResult:
        started = time.perf_counter()
        user = Message("user", text)
        meter = Meter()
        initial: TurnState = {
            "messages": [*session.window(), user],
            "ledger": Ledger.for_turn(text, session.approved_answers),
            "meter": meter,
            "trace_id": trace_id,
            "step": 0,
            "forced": False,
            "reply": Message("assistant"),
            "retried": False,
            "verdict": "ok",
            "text": "",
            "outcome": Outcome.ANSWERED,
        }
        final: TurnState = self._graph.invoke(
            initial,
            # o contador `step` encerra antes; este limite é só a rede de segurança do LangGraph
            config={"recursion_limit": 3 * self._config.max_iterations + 5},
            context=TurnContext(session, on_event),
        )
        result = finish(session, user, final["text"], final["outcome"], meter, trace_id, started)
        observability.annotate(
            {
                "outcome": result.outcome.value,
                "guard_blocked": result.guard_blocked,
                "llm_calls": result.llm_calls,
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cached_tokens": result.usage.cached_tokens,
                "tools": [t.name for t in result.tools],
                "tool_errors": [f"{t.name}:{t.error_code}" for t in result.tools if not t.ok],
                "prompt_hash": prompt_version(),
            }
        )
        return result
