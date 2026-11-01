"""Lógica da interface web, sem Streamlit: relógio, montagem do agente, um turno e erros amigáveis.

Tudo aqui é testável sem navegador. ``web/app.py`` só desenha.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from emporio.agent import turn_log
from emporio.agent.failures import describe
from emporio.agent.graph import Agent
from emporio.agent.session import Session, SQLiteSessionStore, new_session_id
from emporio.agent.types import Event, ToolFinished, TurnResult
from emporio.app import build_agent
from emporio.cli.text import UNEXPECTED_MESSAGE, format_moment, scrub, summarize
from emporio.clock import Clock, FixedClock, SystemClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository, DataLoadError
from emporio.errors import ConfigError, LLMError
from emporio.identity import IdentityVerifier
from emporio.llm.base import LLMClient
from emporio.llm.factory import make_llm

# ponto substituível nos testes (a UI nunca chama a OpenAI neles)
make_llm_for_ui: Callable[[str | None, Mapping[str, str]], LLMClient] = make_llm

DEMO_NOW: Final = "2026-03-23T10:00"
IN_MEMORY: Final = ":memory:"
NOT_SAVED: Final = "Turno não gravado; pode reenviar a mensagem."
EXAMPLES: Final = (
    "Quais violões até R$ 1000?",
    "Quanto custa o Takamine GD20?",
    "Que horas a loja abre?",
    "Quero devolver um pedido",
)

Kind = Literal["answer", "fallback", "error"]


def parse_clock(text: str | None) -> Clock:
    """Vazio = relógio real; senão um instante ISO 8601 (ex.: 2026-03-23T10:00)."""
    if not text:
        return SystemClock()
    try:
        return FixedClock.parse(text)
    except ValueError as error:
        raise ConfigError(
            f"Data ou hora inválida: {text!r}. Use o formato 2026-03-23T10:00."
        ) from error


def show_path(path: Path | str) -> str:
    """O caminho como a tela o mostra: relativo à pasta de onde o app roda, se estiver dentro dela."""
    path = Path(path)
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def hide_path(message: str, folder: str) -> str:
    """Tira da mensagem o caminho absoluto da pasta: o texto da tela não precisa dele."""
    return message.replace(f" em {folder}", "").replace(folder, show_path(folder))


def resolve_path(text: str) -> str:
    """O inverso de ``show_path``: o que o usuário digitou vira caminho absoluto (``:memory:`` fica)."""
    if text == ":memory:":
        return text
    path = Path(text).expanduser()
    return str(path if path.is_absolute() else Path.cwd() / path)


@dataclass(slots=True)
class Runtime:
    agent: Agent[Any]
    session: Session
    clock: Clock
    model: str
    db: str  # ':memory:' = nada é gravado


def build_runtime(
    *,
    model: str,
    api_key: str,
    clock: Clock,
    repo: CsvRepository,
    policies: PolicyStore,
    identity: IdentityVerifier,
    db: str,
    resume_id: str | None = None,
) -> Runtime:
    if not model.strip() or not api_key.strip():
        raise ConfigError(
            "Para conversar, informe o modelo e a chave da OpenAI na barra lateral "
            "(ou deixe-os no arquivo .env)."
        )
    llm = make_llm_for_ui(model.strip(), {"OPENAI_API_KEY": api_key.strip()})
    agent = build_agent(llm, repo=repo, clock=clock, policies=policies, identity=identity)
    session = Session(new_session_id())
    if resume_id:
        with _store(db, clock) as store:
            if store:
                session = store.load(resume_id)
    return Runtime(agent, session, clock, llm.model, db)


class _store:  # abre e fecha o SQLite a cada uso; ':memory:' dá None
    def __init__(self, db: str, clock: Clock) -> None:
        self._db, self._clock = db, clock
        self._store: SQLiteSessionStore | None = None

    def __enter__(self) -> SQLiteSessionStore | None:
        if self._db != IN_MEMORY:
            self._store = SQLiteSessionStore(Path(self._db), clock=self._clock)
        return self._store

    def __exit__(self, *exc: object) -> None:
        if self._store:
            self._store.close()


@dataclass(frozen=True, slots=True)
class SavedSession:
    id: str
    last_at: str


def list_sessions(db: str) -> list[SavedSession]:
    """Conversas salvas, da mais recente para a mais antiga. Sem arquivo, lista vazia."""
    if db == IN_MEMORY or not Path(db).is_file():
        return []
    try:
        with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
            rows = conn.execute(
                "SELECT session_id, MAX(created_at) AS last FROM messages "
                "GROUP BY session_id ORDER BY last DESC"
            ).fetchall()
    except sqlite3.Error:
        return []
    return [SavedSession(sid, last[:16].replace("T", " ")) for sid, last in rows]


def forget(runtime: Runtime) -> None:
    """Apaga o histórico gravado e zera a sessão (inclusive a verificação de identidade)."""
    with _store(runtime.db, runtime.clock) as store:
        if store:
            store.delete(runtime.session.id)
    runtime.session = Session(runtime.session.id)


_configured = False


def ensure_turn_log(env: Mapping[str, str] = os.environ) -> None:
    """Liga o registro de turnos uma única vez por processo (o Streamlit reroda o script)."""
    global _configured
    if not _configured:
        turn_log.configure(Path(env.get("EMPORIO_TURN_LOG", "logs/turns.jsonl")))
        _configured = True


# --- um turno ----------------------------------------------------------------------------------


@dataclass(slots=True)
class WebReply:
    text: str
    kind: Kind
    details: dict[str, Any] | None = None
    saved: bool = False
    error: BaseException | None = field(default=None, repr=False)


def turn_details(result: TurnResult, finished: list[ToolFinished]) -> dict[str, Any]:
    tools = []
    for i, record in enumerate(result.tools):
        summary = summarize(turn_log.mask(finished[i].result)) if i < len(finished) else ""
        tools.append(
            {
                "name": record.name,
                "ok": record.ok,
                "error": record.error_code,
                "ms": round(record.seconds * 1000),
                "arguments": turn_log.mask(record.arguments),
                "summary": summary,
            }
        )
    return {
        "tools": tools,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cached_tokens": result.usage.cached_tokens,
        "llm_calls": result.llm_calls,
        "ms": round(result.seconds * 1000),
        "trace_id": result.trace_id,
        "fallback": result.fallback,
        "guard_blocks": [
            {"draft": turn_log.mask(b.draft), "violations": list(b.violations)}
            for b in result.guard_blocks
        ],
    }


def friendly_error(error: BaseException) -> str:
    """Mensagem em português, sem jargão, sem nomes de flag e sem pedaços de chave."""
    if isinstance(error, ConfigError):
        return scrub(str(error), 300)
    if isinstance(error, DataLoadError):
        return f"Não consegui carregar os dados da loja: {scrub(str(error), 300)}"
    if isinstance(error, LLMError):
        failure = describe(error)
        if failure.misconfigured:
            return (
                f"A OpenAI recusou a requisição ({scrub(str(error))}). "
                "Confira a chave e o modelo na barra lateral."
            )
        return f"{failure.message} {NOT_SAVED}"
    return f"{UNEXPECTED_MESSAGE} {NOT_SAVED}"


def run_one_turn(
    runtime: Runtime,
    text: str,
    on_event: Callable[[Event], None] | None = None,
    *,
    log_turns: bool = False,
) -> WebReply:
    """Um turno, como ``Chat.turn``: só um turno bem-sucedido é gravado e entra no histórico."""
    session, finished = runtime.session, []

    def hook(event: Event) -> None:
        if isinstance(event, ToolFinished):
            finished.append(event)
        if on_event:
            on_event(event)

    try:
        result = runtime.agent.run_turn(session, text, hook)
        with _store(runtime.db, runtime.clock) as store:
            if store:
                store.save(session, result.new_messages)
    except LLMError as error:
        if log_turns:
            turn_log.write(turn_log.failure_record(session.id, error, runtime.clock.now()))
        return WebReply(friendly_error(error), "error", error=error)
    except Exception as error:
        return WebReply(friendly_error(error), "error", error=error)
    if log_turns:
        turn_log.write(turn_log.turn_record(session.id, result, runtime.clock.now()))
    kind: Kind = "fallback" if result.fallback else "answer"
    return WebReply(
        result.text, kind, turn_details(result, finished), saved=runtime.db != IN_MEMORY
    )


def clock_label(clock: Clock) -> str:
    suffix = "relógio real" if isinstance(clock, SystemClock) else "data de demonstração"
    return f"{format_moment(clock.now())} ({suffix})"
