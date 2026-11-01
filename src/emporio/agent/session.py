"""Sessão de conversa e histórico em SQLite.

Grava só o texto das mensagens do cliente e do assistente, já sem dados pessoais. Nunca grava
quem foi verificado, as falhas de verificação nem os resultados das tools: ao retomar uma
conversa, o cliente verifica a identidade de novo. O agente não carrega nem salva nada; quem
faz isso é a CLI, e só depois de um turno bem-sucedido.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from pathlib import Path
from typing import Final
from uuid import uuid4

from emporio.clock import Clock, SystemClock
from emporio.identity import SessionGuard
from emporio.llm.base import Message
from emporio.privacy import redact

WINDOW: Final = 20  # mensagens que voltam ao carregar uma sessão
TTL_DAYS: Final = 30

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (session_id, id);
"""


def new_session_id() -> str:
    return uuid4().hex[:8]


def default_history_path(env: Mapping[str, str] = os.environ) -> Path:
    """Fora do pacote e ignorado pelo git (``history/``)."""
    return Path(env.get("EMPORIO_HISTORY_DB") or "history/sessions.db")


@dataclass(slots=True)
class Session:
    id: str
    history: list[Message] = field(default_factory=list)  # só texto de usuário e assistente
    guard: SessionGuard = field(default_factory=SessionGuard)  # só em memória, nunca gravado
    approved_answers: list[str] = field(default_factory=list)  # alimenta a guarda de fundamentação


class SQLiteSessionStore:
    def __init__(self, path: Path, ttl_days: int = TTL_DAYS, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._ttl = timedelta(days=ttl_days)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)
        self.purge()

    def _now(self) -> str:
        return self._clock.now().astimezone(UTC).isoformat()

    def load(self, session_id: str) -> Session:
        """As últimas mensagens da sessão, começando numa fala do cliente."""
        rows = self._db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, WINDOW),
        ).fetchall()
        history = [Message(role, content) for role, content in reversed(rows)]
        while history and history[0].role != "user":
            history.pop(0)
        return Session(
            session_id,
            history,
            approved_answers=[m.content for m in history if m.role == "assistant"],
        )

    def save(self, session: Session, new_messages: Sequence[Message]) -> None:
        """Grava o texto das falas novas, sem dados pessoais. Chamadas de tool e seus
        resultados não são gravados."""
        now = self._now()
        rows = [
            (session.id, m.role, redact(m.content), now)
            for m in new_messages
            if m.role in ("user", "assistant") and m.content and not m.tool_calls
        ]
        with self._db:
            self._db.executemany(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                rows,
            )

    def delete(self, session_id: str) -> int:
        with self._db:
            return self._db.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            ).rowcount

    def purge(self) -> int:
        """Apaga as mensagens mais velhas que o prazo de retenção."""
        cutoff = (self._clock.now().astimezone(UTC) - self._ttl).isoformat()
        with self._db:
            return self._db.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,)).rowcount

    def close(self) -> None:
        self._db.close()
