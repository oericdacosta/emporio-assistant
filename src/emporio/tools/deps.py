"""Tudo o que uma tool pode usar. O modelo nunca escolhe nada daqui: vem da aplicação."""

from __future__ import annotations

from dataclasses import dataclass

from emporio.clock import Clock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository


@dataclass(frozen=True, slots=True)
class ToolContext:
    repo: CsvRepository
    clock: Clock
    policies: PolicyStore
