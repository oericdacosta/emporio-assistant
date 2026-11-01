r"""O turno do agente, como um grafo LangGraph.

    START -> router --(rota)--> END
                  \--(domínio)--> resume_flows -> model <-> tools -> guard -> compose -> END
                                                      guard --(barrou, 1ª vez)--> model
                                                      guard --(barrou de novo)--> fallback -> END

``router`` responde sem o modelo o que as regras de ``router.py`` reconhecem. ``resume_flows``
faz antes do modelo o que ele não faz de forma confiável, anexando às mensagens o par sintético
chamada/resultado (que também entra no ``Ledger`` e nos ``ToolRecord``): a reclamação que esperava
um contato ou a queixa já dita (sem pedido verificado nem protocolo) vira ``register_complaint``
quando a fala traz o contato; e a fala com a prova (e-mail ou 4 dígitos) mais um pedido conhecido
(da fala ou ``last_order_id``) ainda não verificado vira ``verify_customer``. O modelo só segue. O disjuntor (``steps.run_tools``) barra a
chamada que já falhou duas vezes pelo mesmo motivo. ``guard`` junta o
``Ledger`` (números sem origem; pedir o pedido que o cliente já deu), os filtros de saída
(``filters.py``), a tool certa para o assunto (``intents.py``: frete não se sustenta com
``get_store_info``) e o portão "sem tool, sem fato" (``claims.py``: negativa sobre serviço da loja
é barrada sem o detector): 1 retry, depois ``fallback``. ``tools`` também atualiza o ``WorkingState``
da sessão; ``compose`` tira da resposta aprovada o gancho final (oferta) que o cliente já
recebeu; ``fallback`` monta a resposta com o que as tools do turno devolveram (ou a mensagem de
"não tenho isso confirmado", quando foi o portão). O retry do portão oferece todas as tools com
``tool_choice="auto"``: obrigar uma tool sequestrava o contexto.

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
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import traceable, uuid7

from emporio import observability
from emporio.agent.claims import (
    NO_EVIDENCE_CORRECTION,
    NO_EVIDENCE_MESSAGE,
    SERVICE_CORRECTION,
    FactClaimDetector,
    LLMFactClaimDetector,
    about_conversation,
    needs_check,
    service_claim,
)
from emporio.agent.filters import filter_correction, output_violations
from emporio.agent.grounding import Ledger, correction_message, fallback_from_tools
from emporio.agent.intents import wrong_tool_correction
from emporio.agent.prompt import prompt_version
from emporio.agent.router import route
from emporio.agent.session import Session
from emporio.agent.state import compose
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
from emporio.llm.base import LLMClient, Message, ToolCall, ToolChoice, ToolSpec
from emporio.privacy import extract_contact

log = logging.getLogger(__name__)

BLOCKED_CODES = frozenset({"unsupported_filter", "repeated_failure"})  # barradas, não executadas

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
    routed: bool  # o roteador já respondeu: o modelo não entra
    fallback_text: str  # resposta segura específica (portão); vazio = a das tools


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
        fact_detector: FactClaimDetector | None = None,
    ) -> None:
        self._detector = fact_detector or LLMFactClaimDetector(llm)
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
        graph.add_node("router", self._route)
        graph.add_node("resume_flows", self._resume_flows)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._run_tools)
        graph.add_node("guard", self._guard)
        graph.add_node("compose", self._compose)
        graph.add_node("fallback", self._fallback)
        graph.add_edge(START, "router")
        graph.add_conditional_edges(
            "router",
            lambda state: "end" if state["routed"] else "resume",
            {"end": END, "resume": "resume_flows"},
        )
        graph.add_edge("resume_flows", "model")
        graph.add_conditional_edges(
            "model", self._after_model, {"tools": "tools", "guard": "guard"}
        )
        graph.add_edge("tools", "model")
        graph.add_conditional_edges(
            "guard", self._after_guard, {"end": "compose", "model": "model", "fallback": "fallback"}
        )
        graph.add_edge("compose", END)
        graph.add_edge("fallback", END)
        return graph.compile()

    @staticmethod
    def _route(state: TurnState) -> dict[str, Any]:
        routed = route(state["messages"][-1].content)
        if routed is None:
            return {"routed": False}
        state["meter"].route = routed.route
        return {"routed": True, "text": routed.reply, "outcome": Outcome.ANSWERED}

    def _resume_flows(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        """O orquestrador faz antes do modelo o que ele não faz de forma confiável.

        Verificação: a fala traz a prova (e-mail ou 4 dígitos) e há pedido conhecido ainda não
        verificado: executa ``verify_customer`` e o modelo segue (ou vê o ``not_verified``). O
        bloqueio por tentativas fica na própria tool, que nega sem contar quando está bloqueada.

        Reclamação com contato: registra e o modelo só redige a confirmação. Vale para a reclamação que esperava um contato (``pending_complaint``) e para a queixa
        dita antes (ou agora) quando a fala traz e-mail ou telefone, sem pedido verificado e
        sem protocolo na conversa.
        """
        session = runtime.context.session
        working = session.working
        text = state["messages"][-1].content
        working.note_user_text(text)
        contact = extract_contact([text])
        pending = working.complaint_to_register(text) if contact else None
        if pending and contact:
            working.reset_failures("register_complaint")  # há informação nova: tenta de novo
            call = ToolCall(
                f"resume-{uuid4().hex[:8]}", "register_complaint", {**pending, "contact": contact}
            )
        elif verification := working.verification_to_run(text):
            call = ToolCall(f"resume-{uuid4().hex[:8]}", "verify_customer", verification)
        else:
            return {}
        messages = list(state["messages"])
        run_tools(
            self._toolbox,
            self._make_ctx(session),
            (call,),
            messages,
            state["ledger"],
            state["meter"],
            max_calls=self._config.max_calls_per_step,
            on_event=runtime.context.on_event,
            working=working,
        )
        return {"messages": messages}

    def _model(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        forced = state["step"] == self._config.max_iterations - 1
        messages = state["messages"]
        if forced:
            messages = [*messages, Message("user", FORCE_ANSWER_MESSAGE)]
        tool_choice: ToolChoice = "none" if forced else "auto"
        response = self._llm.chat(
            [Message("system", self._system_prompt(runtime.context.session)), *messages],
            () if forced else self._tools,
            tool_choice=tool_choice,
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
            working=runtime.context.session.working,
        )
        return {"messages": messages}

    def _guard(self, state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        draft = state["reply"].content.strip()
        meter = state["meter"]
        session = runtime.context.session
        state["ledger"].order_known = session.working.last_order_id is not None
        violations = state["ledger"].violations(draft)
        leaks = output_violations(
            draft,
            complaint_registered=any(t.name == "register_complaint" and t.ok for t in meter.tools),
        )
        corrections = [correction_message(violations)] if violations else []
        if leaks:
            corrections.append(filter_correction(leaks))
        if not draft:
            corrections.append(EMPTY_REPLY_MESSAGE)
        if corrections:
            meter.guard_blocked = meter.guard_blocked or bool(violations or leaks)
            if violations or leaks:
                meter.guard_blocks.append(
                    GuardBlock(draft, tuple(dict.fromkeys(violations + leaks)))
                )
            return self._block(state, draft, " ".join(corrections))
        problem, reason = self._unsupported(state, draft, session)
        if problem:
            meter.no_evidence_gate = meter.guard_blocked = True
            meter.guard_blocks.append(GuardBlock(draft, (reason,)))
            # a nova chamada precisa poder oferecer tools: não pode ser a última iteração
            can_retry = state["step"] <= self._config.max_iterations - 2
            return self._block(state, draft, problem, gate=True, can_retry=can_retry)
        outcome = Outcome.CORRECTED if state["retried"] else Outcome.ANSWERED
        return {"verdict": "ok", "text": draft, "outcome": outcome}

    def _unsupported(self, state: TurnState, draft: str, session: Session) -> tuple[str, str]:
        """(correção, motivo) se o rascunho não tem a evidência que o assunto exige; senão vazio.

        Tool errada para o assunto (frete com ``get_store_info``) e, sem nenhuma tool no turno,
        negativa ou afirmação sobre serviço da loja (determinístico) ou fatos que o detector acha.
        Perguntas sobre a própria conversa ("o primeiro que eu falei") não passam pelo portão:
        a fonte é a conversa. O detector recebe o estado de trabalho como fatos já confirmados.
        """
        meter = state["meter"]
        if wrong := wrong_tool_correction(
            session.current_text, draft, [t.name for t in meter.tools]
        ):
            return wrong, "tool errada para o assunto"
        if meter.tools or about_conversation(session.current_text):
            return "", ""
        if service_claim(draft):
            return SERVICE_CORRECTION, "afirma serviço da loja sem consultar tool"
        if not needs_check(draft):
            return "", ""
        recent = [m.content for m in state["messages"] if m.role == "assistant" and m.content]
        detection = self._detector.detect(
            recent, draft, session.working.summary_lines(session.user_texts())
        )
        meter.detector_calls += 1
        meter.add_usage(detection.usage)
        if detection.claims_facts:
            return NO_EVIDENCE_CORRECTION, "afirma fatos sem consultar tool"
        return "", ""

    def _block(
        self,
        state: TurnState,
        draft: str,
        problem: str,
        *,
        gate: bool = False,
        can_retry: bool = True,
    ) -> dict[str, Any]:
        log.info(
            "resposta barrada", extra={"trace_id": state["trace_id"], "retry": not state["retried"]}
        )
        # a nova tentativa gasta uma iteração: sem iteração sobrando, vai direto à resposta segura
        if state["retried"] or state["step"] >= self._config.max_iterations or not can_retry:
            return {
                "verdict": "fallback",
                "fallback_text": NO_EVIDENCE_MESSAGE if gate else "",
            }
        messages = [
            *state["messages"],
            Message("assistant", draft or "(vazio)"),
            Message("user", problem),
        ]
        return {
            "verdict": "retry",
            "retried": True,
            "messages": messages,
        }

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
    def _compose(state: TurnState, runtime: Runtime[TurnContext]) -> dict[str, Any]:
        text, removed = compose(state["text"], runtime.context.session.working)
        state["meter"].offers_removed += removed
        return {"text": text}

    @staticmethod
    def _fallback(state: TurnState) -> dict[str, Any]:
        return {
            "text": state["fallback_text"] or fallback_from_tools(state["meter"].tool_results),
            "outcome": Outcome.FALLBACK,
        }

    # --- o turno -------------------------------------------------------------------------------

    def run_turn(
        self, session: Session, text: str, on_event: EventHook | None = None
    ) -> TurnResult:
        text = text.strip()
        if not text:
            raise ValueError("a mensagem do cliente está vazia")
        session.current_text = text  # as tools leem a fala do turno por aqui; nunca é gravada
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
            "ledger": Ledger.for_turn(
                text, session.approved_answers, session.working.summary_lines(session.user_texts())
            ),
            "meter": meter,
            "trace_id": trace_id,
            "step": 0,
            "forced": False,
            "reply": Message("assistant"),
            "retried": False,
            "verdict": "ok",
            "text": "",
            "outcome": Outcome.ANSWERED,
            "routed": False,
            "fallback_text": "",
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
                "offers_removed": result.offers_removed,
                "working_products": result.working_products,
                "route": result.route,
                "no_evidence_gate": result.no_evidence_gate,
                "detector_calls": result.detector_calls,
                "tool_blocked": sum(t.error_code in BLOCKED_CODES for t in result.tools),
                "relaxed_search": any(
                    name == "search_products" and (res.get("data") or {}).get("relaxed")
                    for name, res in meter.tool_results
                ),
            }
        )
        return result
