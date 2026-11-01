"""Métricas da aba "Métricas": funções puras sobre os runs do LangSmith, mais a leitura em si.

Os runs são lidos pelos campos reais de ``langsmith.schemas.Run`` (``start_time``, ``end_time``,
``status``, ``error``, ``total_cost``, ``prompt_tokens``/``completion_tokens``, ``extra['metadata']``).
Os tokens, as tools e o desfecho vêm do metadado que o turno grava (``observability.annotate``);
o custo vem do próprio LangSmith.
"""

from __future__ import annotations

import math
import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

DATASET: Final = "emporio-golden"
PERIOD_DAYS: Final = 7
PERIOD_LIMIT: Final = 100  # o máximo que a API do LangSmith aceita por consulta
SELECT: Final = (
    "id",
    "start_time",
    "end_time",
    "status",
    "error",
    "extra",
    "prompt_tokens",
    "completion_tokens",
    "total_cost",
    "feedback_stats",
)
NO_VERSION: Final = "sem versão"


def _default_client() -> Any:
    from langsmith import Client

    return Client()


# ponto substituível nos testes (a aba nunca fala com a rede neles)
client_factory: Callable[[], Any] = _default_client


@dataclass(frozen=True, slots=True)
class TurnRow:
    started: datetime
    outcome: str
    latency_s: float | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost: float | None
    tools: tuple[str, ...]
    guard_blocked: bool
    trace_id: str
    tool_errors: tuple[str, ...] = ()  # "nome:codigo"
    prompt_hash: str = NO_VERSION
    feedback_n: int = 0
    feedback_up: float = 0.0  # 👍 entre as avaliações do cliente


@dataclass(frozen=True, slots=True)
class Aggregates:
    turns: int
    p50_s: float | None
    p95_s: float | None
    total_cost: float
    fallback_rate: float | None


def _meta(run: Any) -> Mapping[str, Any]:
    return (getattr(run, "extra", None) or {}).get("metadata") or {}


def _latency(run: Any) -> float | None:
    start, end = getattr(run, "start_time", None), getattr(run, "end_time", None)
    return (end - start).total_seconds() if start and end else None


def _feedback(run: Any) -> tuple[int, float]:
    """(n, 👍) de ``feedback_stats['user_score']`` = ``{'n': .., 'avg': ..}``; ausente = (0, 0)."""
    try:
        stats = (getattr(run, "feedback_stats", None) or {}).get("user_score") or {}
        n = int(stats.get("n") or 0)
        return n, float(stats.get("avg") or 0.0) * n
    except (TypeError, ValueError, AttributeError):
        return 0, 0.0


def turn_row(run: Any) -> TurnRow:
    meta = _meta(run)
    fb_n, fb_up = _feedback(run)
    failed = getattr(run, "error", None) or getattr(run, "status", None) == "error"
    cost = getattr(run, "total_cost", None)
    return TurnRow(
        started=run.start_time,
        outcome="erro" if failed else str(meta.get("outcome", "?")),
        latency_s=_latency(run),
        input_tokens=int(meta.get("input_tokens", getattr(run, "prompt_tokens", 0) or 0)),
        output_tokens=int(meta.get("output_tokens", getattr(run, "completion_tokens", 0) or 0)),
        cached_tokens=int(meta.get("cached_tokens", 0)),
        cost=float(cost) if cost is not None else None,
        tools=tuple(meta.get("tools") or ()),
        guard_blocked=bool(meta.get("guard_blocked")),
        trace_id=str(run.id),
        tool_errors=tuple(meta.get("tool_errors") or ()),
        prompt_hash=str(meta.get("prompt_hash") or NO_VERSION),
        feedback_n=fb_n,
        feedback_up=fb_up,
    )


def rows(runs: Iterable[Any]) -> list[TurnRow]:
    """Do turno mais antigo para o mais novo."""
    return sorted((turn_row(r) for r in runs), key=lambda row: row.started)


def percentile(values: list[float], q: float) -> float | None:
    """Posto mais próximo: simples e sem inventar valores entre as amostras."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def aggregates(turns: list[TurnRow]) -> Aggregates:
    latencies = [t.latency_s for t in turns if t.latency_s is not None]
    return Aggregates(
        turns=len(turns),
        p50_s=percentile(latencies, 0.5),
        p95_s=percentile(latencies, 0.95),
        total_cost=sum(t.cost or 0.0 for t in turns),
        fallback_rate=(sum(t.outcome == "fallback" for t in turns) / len(turns)) if turns else None,
    )


def fmt_seconds(value: float | None) -> str:
    """``2,6 s``: vírgula decimal, como o resto da tela."""
    return "-" if value is None else f"{value:.1f}".replace(".", ",") + " s"


def fmt_usd(value: float | None) -> str:
    """``US$ 0,0069``: custos de um turno são frações de centavo, então quatro casas."""
    return "-" if value is None else "US$ " + f"{value:.4f}".replace(".", ",")


def fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def fmt_num(value: float | None) -> str:
    """``1,4``: uma casa, vírgula decimal."""
    return "-" if value is None else f"{value:.1f}".replace(".", ",")


def _rate(count: float, total: float) -> float | None:
    return count / total if total else None


@dataclass(frozen=True, slots=True)
class Quality:
    corrected_rate: float | None  # a guarda barrou uma vez e o modelo se corrigiu
    blocked_rate: float | None  # a guarda barrou ao menos uma vez
    tool_error_rate: float | None  # chamadas com erro / chamadas
    tools_per_turn: float | None
    cache_share: float | None  # tokens de entrada vindos do cache
    approval: float | None  # 👍 / (👍 + 👎)
    feedback_n: int


def quality(turns: list[TurnRow]) -> Quality:
    n, calls = len(turns), sum(len(t.tools) for t in turns)
    votes = sum(t.feedback_n for t in turns)
    return Quality(
        corrected_rate=_rate(sum(t.outcome == "corrected" for t in turns), n),
        blocked_rate=_rate(sum(t.guard_blocked for t in turns), n),
        tool_error_rate=_rate(sum(len(t.tool_errors) for t in turns), calls),
        tools_per_turn=_rate(calls, n),
        cache_share=_rate(sum(t.cached_tokens for t in turns), sum(t.input_tokens for t in turns)),
        approval=_rate(sum(t.feedback_up for t in turns), votes),
        feedback_n=votes,
    )


def by_tool(turns: list[TurnRow]) -> list[dict[str, Any]]:
    """Chamadas, erros e taxa de erro por tool, com mais erros primeiro."""
    calls: Counter[str] = Counter(name for t in turns for name in t.tools)
    errors: Counter[str] = Counter(e.split(":", 1)[0] for t in turns for e in t.tool_errors)
    lines = [
        {
            "tool": name,
            "chamadas": n,
            "erros": errors[name],
            "taxa de erro": fmt_pct(_rate(errors[name], n)),
        }
        for name, n in calls.items()
    ]
    return sorted(lines, key=lambda line: (-line["erros"], -line["chamadas"], line["tool"]))


def by_prompt(turns: list[TurnRow]) -> list[dict[str, Any]]:
    """Uma linha por versão do prompt, da mais recente para a mais antiga."""
    groups: dict[str, list[TurnRow]] = {}
    for t in turns:
        groups.setdefault(t.prompt_hash, []).append(t)
    lines = []
    for version, group in sorted(
        groups.items(), key=lambda kv: max(t.started for t in kv[1]), reverse=True
    ):
        latencies = [t.latency_s for t in group if t.latency_s is not None]
        costs = [t.cost for t in group if t.cost is not None]
        lines.append(
            {
                "versão do prompt": version,
                "turnos": len(group),
                "tempo mediano": fmt_seconds(percentile(latencies, 0.5)),
                "custo por turno": fmt_usd(_rate(sum(costs), len(group)) if costs else None),
                "corrigidas pela guarda": fmt_pct(
                    _rate(sum(t.outcome == "corrected" for t in group), len(group))
                ),
                "resposta segura": fmt_pct(
                    _rate(sum(t.outcome == "fallback" for t in group), len(group))
                ),
            }
        )
    return lines


def cost_per_turn(agg: Aggregates) -> float | None:
    return agg.total_cost / agg.turns if agg.turns else None


def table(turns: list[TurnRow]) -> list[dict[str, Any]]:
    """Uma linha por turno, com os rótulos da tela."""
    return [
        {
            "hora": t.started.astimezone().strftime("%H:%M:%S"),
            "desfecho": t.outcome,
            "latência (s)": round(t.latency_s, 2) if t.latency_s is not None else None,
            "tokens entrada": t.input_tokens,
            "tokens saída": t.output_tokens,
            "tokens cache": t.cached_tokens,
            "custo (US$)": round(t.cost, 5) if t.cost is not None else None,
            "tools": ", ".join(t.tools) or "-",
            "guarda barrou": t.guard_blocked,
            "erros de tool": ", ".join(t.tool_errors) or "-",
            "versão do prompt": t.prompt_hash,
            "trace": t.trace_id,
        }
        for t in turns
    ]


# --- leitura (cliente injetado por ``client_factory``) -----------------------------------------


def project_name(env: Mapping[str, str] | None = None) -> str:
    return (os.environ if env is None else env).get("LANGSMITH_PROJECT") or "default"


def fetch(client: Any, project: str, session_id: str) -> tuple[list[TurnRow], Aggregates]:
    """(turnos da sessão, agregados do período). Levanta o que o cliente levantar."""
    mine, period = fetch_rows(client, project, session_id)
    return mine, aggregates(period)


def fetch_rows(client: Any, project: str, session_id: str) -> tuple[list[TurnRow], list[TurnRow]]:
    """(turnos da sessão, turnos do período)."""
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)
    mine = client.list_runs(
        project_name=project,
        is_root=True,
        start_time=since,
        filter=(f'and(eq(metadata_key, "session_id"), eq(metadata_value, "{session_id}"))'),
        select=list(SELECT),
    )
    period = client.list_runs(
        project_name=project,
        is_root=True,
        start_time=since,
        select=list(SELECT),
        limit=PERIOD_LIMIT,
    )
    return rows(mine), rows(period)


def last_experiment(client: Any) -> tuple[str, str | None] | None:
    """Nome e link do experimento mais recente do dataset. Os placares ficam no LangSmith."""
    projects = list(client.list_projects(reference_dataset_name=DATASET, limit=50))
    if not projects:
        return None
    newest = max(projects, key=lambda p: p.start_time)
    return str(newest.name), getattr(newest, "url", None)
