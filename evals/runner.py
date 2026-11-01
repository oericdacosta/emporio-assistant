"""Executa os casos de referência contra um modelo e agrega o resultado."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from emporio.agent.session import Session
from emporio.agent.types import AgentConfig, ToolRecord
from emporio.app import build_agent
from emporio.clock import FixedClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.errors import LLMError, ProviderFatal
from emporio.llm.base import LLMClient, Usage
from evals.cases import Case
from evals.checks import Conversation, Verdict, check

EVAL_CONFIG = AgentConfig(temperature=0.0)  # nos evals o modelo não deve variar por sorteio


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Uma execução de um caso."""

    conversation: Conversation
    verdict: Verdict
    usage: Usage
    seconds: float


@dataclass(frozen=True, slots=True)
class CaseReport:
    case: Case
    runs: tuple[RunOutcome, ...]

    @property
    def passed(self) -> bool:
        return all(run.verdict.passed for run in self.runs)

    @property
    def flaky(self) -> bool:
        """Passou em algumas execuções e falhou em outras."""
        return len({run.verdict.passed for run in self.runs}) > 1


def run_case(
    case: Case,
    llm: LLMClient,
    repo: CsvRepository,
    policies: PolicyStore,
    config: AgentConfig = EVAL_CONFIG,
) -> RunOutcome:
    """Uma conversa nova, com o relógio do caso e sem estado de outros casos."""
    started = time.perf_counter()
    answers: list[str] = []
    tools: list[ToolRecord] = []
    fallback = guard_blocked = False
    totals = [0, 0, 0]
    error: str | None = None
    with tempfile.TemporaryDirectory() as scratch:
        agent = build_agent(
            llm,
            repo=repo,
            clock=FixedClock.parse(case.now),
            policies=policies,
            complaints_log=Path(scratch) / "complaints.jsonl",
            config=config,
        )
        session = Session(f"eval-{case.id}")
        try:
            for text in case.turns:
                result = agent.run_turn(session, text)
                answers.append(result.text)
                tools += result.tools
                fallback = fallback or result.fallback
                guard_blocked = guard_blocked or result.guard_blocked
                for index, amount in enumerate(
                    (
                        result.usage.input_tokens,
                        result.usage.output_tokens,
                        result.usage.cached_tokens,
                    )
                ):
                    totals[index] += amount
        except ProviderFatal:
            raise
        except LLMError as failure:
            error = f"{type(failure).__name__}: {failure}"
    conversation = Conversation(tuple(answers), tuple(tools), fallback, guard_blocked, error)
    return RunOutcome(
        conversation,
        check(case, conversation),
        Usage(*totals),
        time.perf_counter() - started,
    )


def run_suite(
    cases: Sequence[Case],
    llm: LLMClient,
    repo: CsvRepository,
    policies: PolicyStore,
    *,
    repeat: int = 1,
    on_case: Callable[[CaseReport], None] | None = None,
) -> list[CaseReport]:
    reports: list[CaseReport] = []
    for case in cases:
        runs = tuple(run_case(case, llm, repo, policies) for _ in range(repeat))
        report = CaseReport(case, runs)
        reports.append(report)
        if on_case:
            on_case(report)
    return reports
