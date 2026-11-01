"""As seções da aba Métricas, desenhadas a partir de turnos. Servem à aba Métricas (turnos lidos do
LangSmith agora) e à aba Resultados (os mesmos turnos, lidos da foto exportada)."""

from __future__ import annotations

import streamlit as st

from emporio.web import metrics


def turns_section(turns: list[metrics.TurnRow]) -> None:
    st.subheader("Turnos desta conversa")
    if turns:
        st.dataframe(metrics.table(turns), hide_index=True)
    else:
        st.caption("Nenhum turno enviado ao LangSmith ainda (o envio leva alguns segundos).")


def summary_section(period_rows: list[metrics.TurnRow], caption: str, *, full: bool = True) -> None:
    """Resumo, qualidade, uso e por tool. ``full`` acrescenta "Resposta segura", "Aprovação do
    cliente" e "Por versão do prompt", que só a aba Métricas (modo avaliador) mostra."""
    agg = metrics.aggregates(period_rows)
    st.subheader("Resumo do período")
    st.caption(caption)
    q = metrics.quality(period_rows)
    # (rótulo, valor, ajuda, só no modo completo)
    items: list[tuple[str, int | str, str | None, bool]] = [
        ("Turnos", agg.turns, None, False),
        (
            "Tempo mediano",
            metrics.fmt_seconds(agg.p50_s),
            "Metade dos turnos respondeu em menos tempo que isso (p50).",
            False,
        ),
        (
            "Tempo dos mais lentos",
            metrics.fmt_seconds(agg.p95_s),
            "95% dos turnos respondeu em menos tempo que isso (p95).",
            False,
        ),
        ("Custo total", metrics.fmt_usd(agg.total_cost), None, False),
        ("Custo por turno", metrics.fmt_usd(metrics.cost_per_turn(agg)), None, False),
        (
            "Resposta segura",
            metrics.fmt_pct(agg.fallback_rate),
            "Turnos em que a guarda barrou duas vezes e o cliente recebeu a resposta segura.",
            True,
        ),
        (
            "Corrigidas pela guarda",
            metrics.fmt_pct(q.corrected_rate),
            "Turnos em que a guarda barrou a primeira resposta e o modelo se corrigiu.",
            False,
        ),
        (
            "Guarda barrou",
            metrics.fmt_pct(q.blocked_rate),
            "Turnos em que a guarda barrou ao menos uma resposta (corrigida ou não).",
            False,
        ),
        (
            "Aprovação do cliente",
            metrics.fmt_pct(q.approval),
            f"Avaliações 👍 entre 👍 e 👎 dadas pelos clientes ({q.feedback_n} no período).",
            True,
        ),
        (
            "Erros de tool",
            metrics.fmt_pct(q.tool_error_rate),
            "Consultas que voltaram com erro, entre todas as consultas feitas.",
            False,
        ),
        (
            "Consultas por turno",
            metrics.fmt_num(q.tools_per_turn),
            "Média de consultas (tools) em cada turno.",
            False,
        ),
        (
            "Entrada vinda do cache",
            metrics.fmt_pct(q.cache_share),
            "Parte dos tokens de entrada que o provedor reaproveitou do cache.",
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
    st.subheader("Por tool")
    if tools := metrics.by_tool(period_rows):
        st.dataframe(tools, hide_index=True)
    else:
        st.caption("Nenhuma consulta no período.")
    if not full:
        return
    st.subheader("Por versão do prompt")
    if versions := metrics.by_prompt(period_rows):
        st.dataframe(versions, hide_index=True)
    else:
        st.caption("Nenhum turno no período.")
