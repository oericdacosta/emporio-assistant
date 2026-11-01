"""As tools que o agente pode chamar."""

from __future__ import annotations

from emporio.data.repository import CsvRepository
from emporio.tools import catalog, policy, store
from emporio.tools.registry import ToolRegistry


def build_registry(repo: CsvRepository) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [*catalog.tools(repo), *store.tools(), *policy.tools()]:
        registry.register(tool)
    return registry
