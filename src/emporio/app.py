"""Montagem do agente: liga o loop, o modelo e as tools reais.

É o único lugar que junta ``agent`` com ``tools``. A CLI, os evals e o gravador de conversas
chamam ``build_agent`` em vez de repetir essa fiação.
"""

from __future__ import annotations

from pathlib import Path

from emporio.agent.loop import Agent, AgentConfig
from emporio.agent.prompt import build_system_prompt
from emporio.agent.session import Session
from emporio.clock import Clock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.domain.hours import store_status
from emporio.identity import IdentityVerifier
from emporio.llm.base import LLMClient
from emporio.tools import build_registry
from emporio.tools.deps import ToolContext


def build_agent(
    llm: LLMClient,
    *,
    repo: CsvRepository,
    clock: Clock,
    policies: PolicyStore,
    complaints_log: Path = Path("logs/complaints.jsonl"),
    identity: IdentityVerifier | None = None,
    config: AgentConfig = AgentConfig(),  # noqa: B008
) -> Agent[ToolContext]:
    # um só verificador para todas as sessões: o bloqueio por pedido vale entre elas
    shared_identity = identity or IdentityVerifier()

    def make_ctx(session: Session) -> ToolContext:
        return ToolContext(
            repo,
            clock,
            policies,
            session=session.guard,
            identity=shared_identity,
            complaints_log=complaints_log,
        )

    def system_prompt(session: Session) -> str:
        now = clock.now()
        customer = repo.customer(session.guard.customer_id) if session.guard.customer_id else None
        first_name = customer.name.split()[0] if customer and customer.name.split() else None
        return build_system_prompt(now, store_status(now), first_name)

    return Agent(llm, build_registry(repo), make_ctx, system_prompt, config)
