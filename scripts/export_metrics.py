"""Exporta uma foto das métricas do LangSmith para ``evals/reports/metrics_snapshot.json``.

Uso:  uv run python scripts/export_metrics.py
Lê (com a sua chave, do .env) os turnos do projeto, os das conversas de exemplo e os
experimentos do golden, e grava só números: desfecho, tokens, custo, latência, tools. Nenhum texto. A aba
"Resultados" do Streamlit lê esse arquivo, então quem clonar o projeto vê os números sem chave.
As rodadas sem experimento no LangSmith entram pelo cabeçalho do relatório ``.md``.
"""

from __future__ import annotations

import json
import re
import warnings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from emporio import envfile
from emporio.web import metrics

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
EXAMPLES = ROOT / "examples" / "conversations"
OUT = REPORTS / "metrics_snapshot.json"
MIN_FULL_RUN = 60  # execuções de um golden inteiro; abaixo disso é reexecução de poucos casos
REASON_CHARS = 300
PERIOD_DAYS = 14  # o que o LangSmith guarda dos traces no plano gratuito


def _label(path: Path) -> str:
    suffix = path.stem.split("gpt-4.1-mini", 1)[-1].lstrip("_")
    return suffix or "inicial"


def _reports() -> list[tuple[str, Path, str]]:
    """(rótulo, arquivo, texto) dos relatórios ``.md``, do mais antigo ao mais novo."""
    found = []
    for path in sorted(REPORTS.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        stamp = re.search(r"# Relatório de avaliação — (\S+ \S+)", text)
        found.append((stamp.group(1) if stamp else "", path, text))
    return [(_label(p), p, t) for _, p, t in sorted(found)]


def _num(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None


def round_from_report(label: str, text: str) -> dict[str, Any]:
    """O que o cabeçalho do relatório guarda, para a rodada sem experimento no LangSmith."""
    header = re.search(
        r"modelo: `([^`]+)` \| commit: `([^`]+)` \| hash do prompt: `([^`]+)` \| "
        r"execuções por caso: (\d+)",
        text,
    )
    passed = re.search(r"aprovados: (\d+)/(\d+)", text)
    when = re.search(r"# Relatório de avaliação — (\S+) (\S+)", text)
    if not (header and passed and when):
        raise SystemExit(f"cabeçalho do relatório não reconhecido: {label}")
    return {
        "label": label,
        "source": "relatorio",
        "date": when.group(1),
        "time": when.group(2),
        "model": header.group(1),
        "commit": header.group(2),
        "prompt_hash": header.group(3),
        "repeat": int(header.group(4)),
        "cases": int(passed.group(2)),
        "passed_cases": int(passed.group(1)),
        "tokens_in": _num(text, r"tokens: (\d+) de entrada"),
        "tokens_out": _num(text, r"de entrada .*? e (\d+) de saída"),
        "cost_usd": None,
        "latency_p50_s": _num(text, r"mediana ([\d.]+)s"),
        "latency_max_s": _num(text, r"máxima ([\d.]+)s"),
        "case_results": [],
    }


def round_from_langsmith(client: Any, project: Any, label: str) -> dict[str, Any]:
    meta = (project.extra or {}).get("metadata", {})
    examples = {
        str(ex.id): ex.metadata for ex in client.list_examples(dataset_name=metrics.DATASET)
    }
    per_case: dict[str, dict[str, Any]] = {}
    runs = client.list_runs(
        project_id=project.id,
        is_root=True,
        select=["id", "reference_example_id", "feedback_stats"],
    )
    for run in runs:
        info = examples.get(str(run.reference_example_id))
        if not info:
            continue
        hard = (run.feedback_stats or {}).get("hard") or {}
        ok = (hard.get("avg") or 0) >= 1
        case = per_case.setdefault(
            info["id"],
            {
                "id": info["id"],
                "intent": info.get("intent"),
                "suite": info.get("suite"),
                "runs": 0,
                "passes": 0,
                "reason": None,
            },
        )
        case["runs"] += 1
        case["passes"] += ok
        if not ok and not case["reason"]:
            comments = hard.get("comments") or []
            case["reason"] = (comments[0] if comments else "")[:REASON_CHARS] or None
    results = sorted(per_case.values(), key=lambda c: c["id"])
    started = project.start_time.astimezone()
    soft = (project.feedback_stats or {}).get("soft") or {}
    return {
        "label": label,
        "source": "langsmith",
        "experiment": project.name,
        "date": started.strftime("%Y-%m-%d"),
        "time": started.strftime("%H:%M"),
        "model": meta.get("model"),
        "commit": meta.get("commit"),
        "prompt_hash": meta.get("prompt_hash"),
        "repeat": meta.get("repeat"),
        "cases": len(results),
        "passed_cases": sum(c["passes"] == c["runs"] for c in results),
        "soft_pass_rate": soft.get("avg"),
        "tokens_in": project.prompt_tokens,
        "tokens_out": project.completion_tokens,
        "cost_usd": project.total_cost,
        "latency_p50_s": project.latency_p50.total_seconds() if project.latency_p50 else None,
        "latency_p99_s": project.latency_p99.total_seconds() if project.latency_p99 else None,
        "case_results": results,
    }


def rounds(client: Any) -> list[dict[str, Any]]:
    projects = list(
        client.list_projects(reference_dataset_name=metrics.DATASET, limit=100, include_stats=True)
    )
    full = {
        p.name: p
        for p in projects
        if (p.run_count or 0) >= MIN_FULL_RUN and p.name in "".join(t for _, _, t in _reports())
    }
    out = []
    for label, _path, text in _reports():
        name = next((n for n in full if n in text), None)
        out.append(
            round_from_langsmith(client, full[name], label)
            if name
            else round_from_report(label, text)
        )
    return out


def examples(client: Any, project: str) -> list[dict[str, Any]]:
    """Os turnos da gravação mais recente de cada conversa, pelo ``session_id`` do gravador."""
    since = datetime.now(UTC) - timedelta(days=14)
    out = []
    for path in sorted(EXAMPLES.glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        session = f"example-{item['slug']}"
        found = client.list_runs(
            project_name=project,
            is_root=True,
            start_time=since,
            filter=f'and(eq(metadata_key, "session_id"), eq(metadata_value, "{session}"))',
            select=list(metrics.SELECT),
        )
        turns = metrics.rows(found)[-len(item["turns"]) :]
        recorded = [[x["name"] for x in t["tools"]] for t in item["turns"]]
        if len(turns) != len(item["turns"]) or [list(t.tools) for t in turns] != recorded:
            raise SystemExit(f"os traces de {session} não batem com {path.name}: regrave o exemplo")
        out.append(
            {
                "slug": item["slug"],
                "title": item["title"],
                "turns": [metrics.row_to_json(t) for t in turns],
            }
        )
    return out


def period_turns(client: Any, project: str) -> list[dict[str, Any]]:
    """Todos os turnos do projeto no período que o LangSmith ainda guarda: os testes do autor."""
    since = datetime.now(UTC) - timedelta(days=PERIOD_DAYS)
    found = client.list_runs(
        project_name=project, is_root=True, start_time=since, select=list(metrics.SELECT)
    )
    return [metrics.row_to_json(t) for t in metrics.rows(found)]


def main() -> int:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    envfile.load(Path(".env"))
    client = metrics.client_factory()
    project = metrics.project_name()
    snapshot = {
        "exported_on": date.today().isoformat(),
        "source": f"LangSmith (projeto {project}, dataset {metrics.DATASET}) e relatórios de evals/reports",
        "turns": period_turns(client, project),
        "rounds": rounds(client),
        "examples": examples(client, project),
    }
    OUT.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=float) + "\n", encoding="utf-8"
    )
    print(
        f"gravado {OUT.relative_to(ROOT)}: {len(snapshot['rounds'])} rodadas, "
        f"{len(snapshot['examples'])} conversas"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
