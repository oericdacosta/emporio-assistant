"""As seções da aba Métricas, desenhadas a partir de turnos. Servem à aba Métricas (turnos lidos do
LangSmith agora) e à aba Resultados (os mesmos turnos, lidos da foto exportada)."""

from __future__ import annotations

import streamlit as st

from emporio.web import metrics


def turns_section(turns: list[metrics.TurnRow]) -> None:
    st.subheader("Turns of this conversation")
    if turns:
        st.dataframe(metrics.table(turns), hide_index=True)
    else:
        st.caption("No turn sent to LangSmith yet (the upload takes a few seconds).")


def summary_section(period_rows: list[metrics.TurnRow], caption: str, *, full: bool = True) -> None:
    """Resumo, qualidade, uso e por tool. ``full`` acrescenta "Resposta segura", "Aprovação do
    cliente" e "Por versão do prompt", que só a aba Métricas (modo avaliador) mostra."""
    agg = metrics.aggregates(period_rows)
    st.subheader("Period summary")
    st.caption(caption)
    q = metrics.quality(period_rows)
    # (rótulo, valor, ajuda, só no modo completo)
    items: list[tuple[str, int | str, str | None, bool]] = [
        ("Turns", agg.turns, None, False),
        (
            "Median time",
            metrics.fmt_seconds(agg.p50_s),
            "Half of the turns answered in less time than this (p50).",
            False,
        ),
        (
            "Time of the slowest",
            metrics.fmt_seconds(agg.p95_s),
            "95% of the turns answered in less time than this (p95).",
            False,
        ),
        ("Total cost", metrics.fmt_usd(agg.total_cost), None, False),
        ("Cost per turn", metrics.fmt_usd(metrics.cost_per_turn(agg)), None, False),
        (
            "Safe answer",
            metrics.fmt_pct(agg.fallback_rate),
            "Turns in which the guard blocked twice and the customer received the safe answer.",
            True,
        ),
        (
            "Fixed by the guard",
            metrics.fmt_pct(q.corrected_rate),
            "Turns in which the guard blocked the first answer and the model corrected itself.",
            False,
        ),
        (
            "Guard blocked",
            metrics.fmt_pct(q.blocked_rate),
            "Turns in which the guard blocked at least one answer (corrected or not).",
            False,
        ),
        (
            "Customer approval",
            metrics.fmt_pct(q.approval),
            f"Share of 👍 among the 👍 and 👎 ratings given by customers ({q.feedback_n} in the period).",
            True,
        ),
        (
            "Tool errors",
            metrics.fmt_pct(q.tool_error_rate),
            "Queries that came back with an error, among all queries made.",
            False,
        ),
        (
            "Queries per turn",
            metrics.fmt_num(q.tools_per_turn),
            "Average number of queries (tools) in each turn.",
            False,
        ),
        (
            "Input from the cache",
            metrics.fmt_pct(q.cache_share),
            "Share of the input tokens that the provider reused from the cache.",
            False,
        ),
    ]
    shown = [
        (label, value, help_) for label, value, help_, only_full in items if full or not only_full
    ]
    per_row = (
        3 if full else 2
    )  # sem os dois itens o total é 10: em fileiras de 2 nada fica sobrando
    for start in range(0, len(shown), per_row):
        for column, (label, value, help_) in zip(
            st.columns(per_row), shown[start : start + per_row], strict=False
        ):
            column.metric(label, value, help=help_)
    st.subheader("By tool")
    if tools := metrics.by_tool(period_rows):
        st.dataframe(tools, hide_index=True)
    else:
        st.caption("No queries in the period.")
    if not full:
        return
    st.subheader("By prompt version")
    if versions := metrics.by_prompt(period_rows):
        st.dataframe(versions, hide_index=True)
    else:
        st.caption("No turns in the period.")
