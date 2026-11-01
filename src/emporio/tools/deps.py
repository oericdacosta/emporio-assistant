"""Tudo o que uma tool pode usar. O modelo nunca escolhe nada daqui: vem da aplicação."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from emporio.clock import Clock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.identity import IdentityVerifier, SessionGuard


@dataclass(frozen=True, slots=True)
class ToolContext:
    repo: CsvRepository
    clock: Clock
    policies: PolicyStore
    session: SessionGuard = field(default_factory=SessionGuard)  # quem está falando, por sessão
    identity: IdentityVerifier = field(default_factory=IdentityVerifier)  # compartilhado
    complaints_log: Path = Path("logs/complaints.jsonl")
    user_text: str = ""  # a fala do cliente neste turno
    user_texts: tuple[str, ...] = ()  # todas as falas do cliente na conversa, a atual por último
