"""Aba "Resultados": o resumo do período e o "Por tool" dos testes do autor, lidos de
``evals/reports/metrics_snapshot.json``.

O arquivo é uma foto exportada do LangSmith por ``scripts/export_metrics.py`` (só números, sem
texto de conversa). Diferente da aba Métricas, que mostra o tráfego de quem está usando agora e
pede a chave do LangSmith, esta funciona sem chave e sem rede.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from emporio.web import metrics
from emporio.web.metrics_view import summary_section

SNAPSHOT = Path(__file__).resolve().parents[3] / "evals" / "reports" / "metrics_snapshot.json"


def load_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    """A foto exportada, ou ``None`` se não existe ou está ilegível."""
    try:
        data = json.loads((path or SNAPSHOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("turns"), list):
        return data
    return None


def results_tab() -> None:
    snapshot = load_snapshot()
    if snapshot is None:
        st.info("There are no exported results yet in evals/reports/metrics_snapshot.json.")
        return
    period = [metrics.row_from_json(t) for t in snapshot["turns"]]
    if not period:
        st.info("The exported snapshot has no turns.")
        return
    st.info(
        "These are the results measured by the author of the project, exported from their LangSmith on "
        f"{snapshot.get('exported_on', '?')} and saved in the repository. Whoever downloads the project sees "
        "the same numbers, with no key needed. To see the metrics of your own "
        "conversations, turn on evaluator mode and open the Metrics tab."
    )
    summary_section(
        period,
        f"All {len(period)} turns of the test project: development conversations, "
        "stress tests and examples.",
        full=False,
    )
