"""O loop do agente: um turno de conversa, do que o cliente disse até a resposta validada.

O modelo entende a intenção, escolhe as tools, preenche os argumentos e redige; todo o resto é
código. Um turno repete até ``max_iterations`` vezes: chama o modelo, executa as tools que ele
pediu (em sequência) e devolve os resultados. A resposta candidata passa pela guarda de
fundamentação: se citar um valor que nenhuma tool nem o cliente produziu, o modelo tem uma
chance de corrigir; se insistir, o cliente recebe uma resposta segura, sem valores.

Este módulo conhece o contrato de tools (``Toolbox``), não as tools concretas: quem monta o
agente (a CLI, os evals) escolhe qual conjunto entregar. A sessão só é alterada quando o turno
termina bem; uma falha do provedor deixa tudo como estava, e o cliente reenvia a mensagem.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol
from uuid import uuid4

from emporio.agent.grounding import FALLBACK_MESSAGE, Ledger, correction_message
from emporio.agent.session import Session
from emporio.llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolSpec, Usage

log = logging.getLogger(__name__)

EMPTY_REPLY_MESSAGE: Final = "Sua resposta veio vazia. Responda ao cliente com o que você já sabe."
FORCE_ANSWER_MESSAGE: Final = (
    "Você já consultou o que precisava. Responda agora ao cliente com as informações que tem; "
    "se faltar algum dado, diga que vai confirmar com a equipe."
)


class Toolbox[C](Protocol):
    """O que o agente precisa de um conjunto de tools. ``C`` é o contexto de cada chamada."""

    def specs(self) -> list[dict[str, Any]]: ...

    def call(self, name: str, raw_args: Any, ctx: C) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AgentConfig:
    max_iterations: int = 5  # a última não oferece tools
    max_calls_per_step: int = 3
    max_tokens: int = 600
    temperature: float = 0.2


class Outcome(StrEnum):
    ANSWERED = "answered"
    CORRECTED = "corrected"  # a guarda barrou a primeira resposta e a segunda passou
    FALLBACK = "fallback"  # o cliente recebeu a resposta segura


@dataclass(frozen=True, slots=True)
class ToolStarted:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolFinished:
    name: str
    ok: bool
    seconds: float
    result: dict[str, Any]  # exatamente o que o modelo viu


Event = ToolStarted | ToolFinished
EventHook = Callable[[Event], None]


@dataclass(frozen=True, slots=True)
class ToolRecord:
    name: str
    arguments: dict[str, Any]
    ok: bool
    error_code: str | None
    seconds: float
    result_chars: int


@dataclass(frozen=True, slots=True)
class TurnResult:
    text: str
    outcome: Outcome
    new_messages: tuple[Message, Message]  # a fala do cliente e a resposta, para gravar
    tools: tuple[ToolRecord, ...]
    usage: Usage
    llm_calls: int
    seconds: float
    trace_id: str
    guard_blocked: bool  # a guarda barrou alguma resposta neste turno

    @property
    def fallback(self) -> bool:
        return self.outcome is Outcome.FALLBACK


class _Meter:
    """Soma o que o turno consumiu."""

    def __init__(self) -> None:
        self.input_tokens = self.output_tokens = self.cached_tokens = self.calls = 0
        self.tools: list[ToolRecord] = []
        self.guard_blocked = False

    def add(self, response: LLMResponse) -> None:
        self.calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        self.cached_tokens += response.usage.cached_tokens

    @property
    def usage(self) -> Usage:
        return Usage(self.input_tokens, self.output_tokens, self.cached_tokens)


def _failure(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "hint": hint}}


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

    def run_turn(
        self, session: Session, text: str, on_event: EventHook | None = None
    ) -> TurnResult:
        text = text.strip()
        if not text:
            raise ValueError("a mensagem do cliente está vazia")
        started = time.perf_counter()
        trace_id = uuid4().hex[:12]
        user = Message("user", text)
        ledger = Ledger.for_turn(text, session.approved_answers)
        meter = _Meter()
        messages = [*session.window(), user]  # sem o prompt de sistema: ele é refeito a cada volta
        retried = False

        for step in range(self._config.max_iterations):
            forced = step == self._config.max_iterations - 1
            if forced:
                messages.append(Message("user", FORCE_ANSWER_MESSAGE))
            reply = self._ask(session, messages, meter, offer_tools=not forced).message

            if reply.tool_calls and not forced:
                self._run_tools(session, reply.tool_calls, messages, ledger, meter, on_event)
                continue

            draft = reply.content.strip()
            violations = ledger.violations(draft)
            if violations:
                problem: str | None = correction_message(violations)
            elif not draft:
                problem = EMPTY_REPLY_MESSAGE
            else:
                problem = None
            if problem is None:
                outcome = Outcome.CORRECTED if retried else Outcome.ANSWERED
                return self._finish(session, user, draft, outcome, meter, trace_id, started)
            meter.guard_blocked = meter.guard_blocked or bool(violations)
            log.info("resposta barrada", extra={"trace_id": trace_id, "retry": not retried})
            if retried:
                break
            retried = True
            messages += [Message("assistant", draft or "(vazio)"), Message("user", problem)]

        return self._finish(
            session, user, FALLBACK_MESSAGE, Outcome.FALLBACK, meter, trace_id, started
        )

    def _ask(
        self, session: Session, messages: list[Message], meter: _Meter, *, offer_tools: bool
    ) -> LLMResponse:
        response = self._llm.chat(
            [Message("system", self._system_prompt(session)), *messages],
            self._tools if offer_tools else (),
            tool_choice="auto" if offer_tools else "none",
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        meter.add(response)
        return response

    def _run_tools(
        self,
        session: Session,
        calls: tuple[ToolCall, ...],
        messages: list[Message],
        ledger: Ledger,
        meter: _Meter,
        on_event: EventHook | None,
    ) -> None:
        # o texto que acompanha as chamadas é descartado: só a resposta final é entregue
        messages.append(Message("assistant", "", calls))
        ctx = self._make_ctx(session)
        for index, call in enumerate(calls):
            if index >= self._config.max_calls_per_step:
                result = _failure(
                    "too_many_calls",
                    "chamadas demais de uma vez",
                    f"Faça no máximo {self._config.max_calls_per_step} chamadas por vez.",
                )
                seconds = 0.0
            else:
                if on_event:
                    on_event(ToolStarted(call.name, call.arguments))
                began = time.perf_counter()
                try:
                    result = self._toolbox.call(call.name, call.arguments, ctx)
                except Exception:
                    log.exception("falha inesperada na tool %s", call.name)
                    result = _failure("internal_error", "a consulta falhou por um problema nosso")
                seconds = time.perf_counter() - began
            ledger.absorb(result)
            content = json.dumps(result, ensure_ascii=False, default=str)
            messages.append(Message("tool", content, tool_call_id=call.id))
            error = result.get("error") or {}
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

    def _finish(
        self,
        session: Session,
        user: Message,
        text: str,
        outcome: Outcome,
        meter: _Meter,
        trace_id: str,
        started: float,
    ) -> TurnResult:
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
        )
