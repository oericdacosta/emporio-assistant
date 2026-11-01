"""Interface web do Téo. Só desenha: a lógica está em ``emporio.web.logic``.

Rodar: ``uv run streamlit run src/emporio/web/app.py`` (ou ``make ui``).
"""

from __future__ import annotations

import json
import os
from collections.abc import MutableMapping
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, cast

import streamlit as st

from emporio import __version__, envfile, observability
from emporio.agent.session import Session, default_history_path, new_session_id
from emporio.agent.types import Event, ToolStarted
from emporio.app import check_data, load_data
from emporio.cli.text import AGENT_NAME, MAX_CHARS, TOOL_LABELS, sanitize, scrub
from emporio.data.repository import CsvRepository, DataLoadError
from emporio.identity import IdentityVerifier
from emporio.paths import default_data_dir
from emporio.web import logic, metrics
from emporio.web.examples import examples_tab

MODE_DEMO = "Data de demonstração (padrão: AGENT_NOW ou 2026-03-23 10:00)"
MODE_REAL = "Agora (real)"
MODE_PICK = "Escolher"
NEW = "(nova conversa)"
HELP_WEB = f"""**Como conversar com o {AGENT_NAME}**

Escreva na caixa de baixo e aperte Enter (até {MAX_CHARS} caracteres), ou clique em um dos exemplos.

**Algumas ideias**
- Quais violões até R\\$ 1000?
- Quanto custa o Takamine GD20?
- Que horas a loja abre?
- Quero devolver um pedido

**Para falar de um pedido**, informe o número dele e o e-mail do cadastro (ou os 4 últimos dígitos do telefone). É assim que o {AGENT_NAME} confirma que o pedido é seu.

**Nesta barra**
- *Modo avaliador*: abre a aba Métricas (consultas de cada resposta, tokens, latência e custo).
- *Nova conversa*: começa do zero; a anterior continua salva.
- *Relógio*: a data de demonstração é a dos pedidos da loja."""


def state() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], st.session_state)


@st.cache_resource
def shared(data_dir: str) -> tuple[CsvRepository, Any, IdentityVerifier]:
    """Um por processo: o bloqueio por tentativas de identidade não pode zerar a cada rerun."""
    repo, policies = load_data(Path(data_dir))
    return repo, policies, IdentityVerifier()


@st.cache_resource
def data_status(data_dir: str) -> tuple[bool, str]:
    try:
        return True, logic.hide_path(check_data(Path(data_dir)), data_dir)
    except Exception as error:
        text = logic.friendly_error(
            error if isinstance(error, DataLoadError) else DataLoadError(str(error))
        )
        return False, logic.hide_path(text, data_dir)


def md(text: str) -> str:
    return text.replace("$", "\\$")  # "R$ 10 ... R$ 20" viraria fórmula


def render_details(details: dict[str, Any]) -> None:
    """Uma consulta por bloco: argumentos e resultado resumido (vai para a aba Métricas)."""
    for tool in details["tools"]:
        status = "ok" if tool["ok"] else f"erro ({tool['error']})"
        st.markdown(f"**{tool['name']}** - {status} - {tool['ms']} ms")
        st.code(json.dumps(tool["arguments"], ensure_ascii=False), language="json")
        st.text(tool["summary"])
    if not details["tools"]:
        st.caption("Nenhuma consulta neste turno.")
    st.caption(
        f"tokens entrada/saída/cache: {details['input_tokens']}/{details['output_tokens']}/"
        f"{details['cached_tokens']} - {details['llm_calls']} chamada(s) ao modelo - "
        f"{details['ms']} ms - trace_id {details['trace_id']}"
    )
    for block in details["guard_blocks"]:
        st.warning(
            md(
                "A guarda barrou uma resposta.\n\n"
                f"Rascunho: {block['draft']}\n\n"
                f"Sem origem nas consultas: {'; '.join(block['violations'])}"
            )
        )
    if details["fallback"]:
        st.warning("Foi enviada a resposta segura no lugar da resposta do modelo.")


@st.cache_data(ttl=60, show_spinner=False)
def load_metrics(project: str, session_id: str) -> dict[str, Any]:
    """Cache de 60 s. Levanta o que o cliente levantar; quem chama transforma em aviso."""
    client = metrics.client_factory()
    turns, period_rows = metrics.fetch_rows(client, project, session_id)
    try:
        experiment = metrics.last_experiment(client)
    except Exception:
        experiment = None  # o experimento é um extra: sem ele a aba continua útil
    return {
        "turns": turns,
        "period": metrics.aggregates(period_rows),
        "period_rows": period_rows,
        "experiment": experiment,
    }


def details_section(messages: list[dict[str, Any]]) -> None:
    """Os detalhes vêm da memória da conversa: aparecem mesmo sem o LangSmith."""
    detailed = [m for m in messages if m.get("details")]
    with st.expander(f"Detalhes das consultas de cada turno ({len(detailed)})"):
        for message in detailed:
            render_details(message["details"])
        if not detailed:
            st.caption("Nenhum turno ainda.")


def refresh_metrics() -> None:
    """Primeiro clique: libera a leitura do LangSmith. Os seguintes: descartam o cache."""
    state()["metrics_on"] = True
    load_metrics.clear()


def metrics_tab(rt: logic.Runtime | None, messages: list[dict[str, Any]], tracing: str) -> None:
    """Aba do modo avaliador. Nada aqui pode derrubar a conversa."""
    if tracing == "ativo":  # fica no topo, antes e depois do clique
        st.info(
            "Clique em **Atualizar** para ver os dados do LangSmith. Eles levam alguns segundos "
            "para chegar depois de cada resposta."
        )
    st.button("Atualizar", key="refresh-metrics", on_click=refresh_metrics)
    if tracing != "ativo":
        st.info("LangSmith não configurado: defina LANGSMITH_API_KEY no .env (veja .env.example).")
        details_section(messages)
        return
    if rt is None:
        return
    if not state().get("metrics_on"):
        details_section(messages)
        return
    try:
        data = load_metrics(metrics.project_name(), rt.session.id)
    except Exception as error:
        st.warning(
            f"Não consegui ler o LangSmith agora ({type(error).__name__}: {scrub(str(error))[:200]}). "
            "Tente Atualizar."
        )
        details_section(messages)
        return
    st.subheader("Turnos desta conversa")
    if data["turns"]:
        st.dataframe(metrics.table(data["turns"]), hide_index=True)
    else:
        st.caption("Nenhum turno enviado ao LangSmith ainda (o envio leva alguns segundos).")
    details_section(messages)
    agg = data["period"]
    st.subheader("Resumo do período")
    st.caption(
        f"Últimos {metrics.PERIOD_DAYS} dias, até {metrics.PERIOD_LIMIT} turnos, em todas as conversas."
    )
    top = st.columns(3)
    top[0].metric("Turnos", agg.turns)
    top[1].metric(
        "Tempo mediano",
        metrics.fmt_seconds(agg.p50_s),
        help="Metade dos turnos respondeu em menos tempo que isso (p50).",
    )
    top[2].metric(
        "Tempo dos mais lentos",
        metrics.fmt_seconds(agg.p95_s),
        help="95% dos turnos respondeu em menos tempo que isso (p95).",
    )
    bottom = st.columns(3)
    bottom[0].metric("Custo total", metrics.fmt_usd(agg.total_cost))
    bottom[1].metric("Custo por turno", metrics.fmt_usd(metrics.cost_per_turn(agg)))
    bottom[2].metric(
        "Resposta segura",
        metrics.fmt_pct(agg.fallback_rate),
        help="Turnos em que a guarda barrou duas vezes e o cliente recebeu a resposta segura.",
    )
    q = metrics.quality(data["period_rows"])
    guard = st.columns(3)
    guard[0].metric(
        "Corrigidas pela guarda",
        metrics.fmt_pct(q.corrected_rate),
        help="Turnos em que a guarda barrou a primeira resposta e o modelo se corrigiu.",
    )
    guard[1].metric(
        "Guarda barrou",
        metrics.fmt_pct(q.blocked_rate),
        help="Turnos em que a guarda barrou ao menos uma resposta (corrigida ou não).",
    )
    guard[2].metric(
        "Aprovação do cliente",
        metrics.fmt_pct(q.approval),
        help=f"Avaliações 👍 entre 👍 e 👎 dadas pelos clientes ({q.feedback_n} no período).",
    )
    use = st.columns(3)
    use[0].metric(
        "Erros de tool",
        metrics.fmt_pct(q.tool_error_rate),
        help="Consultas que voltaram com erro, entre todas as consultas feitas.",
    )
    use[1].metric(
        "Consultas por turno",
        metrics.fmt_num(q.tools_per_turn),
        help="Média de consultas (tools) em cada turno.",
    )
    use[2].metric(
        "Entrada vinda do cache",
        metrics.fmt_pct(q.cache_share),
        help="Parte dos tokens de entrada que o provedor reaproveitou do cache.",
    )
    st.subheader("Por tool")
    if tools := metrics.by_tool(data["period_rows"]):
        st.dataframe(tools, hide_index=True)
    else:
        st.caption("Nenhuma consulta no período.")
    st.subheader("Por versão do prompt")
    if versions := metrics.by_prompt(data["period_rows"]):
        st.dataframe(versions, hide_index=True)
    else:
        st.caption("Nenhum turno no período.")
    if data["experiment"]:
        name, url = data["experiment"]
        st.caption(f"Último experimento de avaliação: [{name}]({url})" if url else name)


def rate_turn(trace_id: str) -> None:
    """on_change do 👍/👎: grava no run do turno, sem bloquear. O erro vira legenda, não exceção."""
    s = state()
    errors: dict[str, str] = s.setdefault("fb_errors", {})
    errors.pop(trace_id, None)
    if (value := s.get(f"fb-{trace_id}")) is not None:  # 0 = 👎, 1 = 👍
        observability.send_feedback(
            trace_id, float(value), lambda reason: errors.__setitem__(trace_id, reason)
        )


def feedback_widget(trace_id: str) -> None:
    st.feedback("thumbs", key=f"fb-{trace_id}", on_change=rate_turn, args=(trace_id,))
    if reason := state().get("fb_errors", {}).get(trace_id):
        st.caption(f"Não consegui registrar sua avaliação agora ({reason}).")


def render(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(md(message["content"]))
        trace_id = (message.get("details") or {}).get("trace_id")
        if trace_id and message["role"] == "assistant" and state().get("tracing") == "ativo":
            feedback_widget(trace_id)


# --- ações dos botões (rodam antes do rerun) -----------------------------------------------------


def send_example(text: str) -> None:
    state()["pending"] = text


def new_chat() -> None:
    s = state()
    if rt := s.get("rt"):
        rt.session = Session(new_session_id())
    s["messages"], s["last_error"], s["resume"] = [], None, NEW


def forget_chat() -> None:
    s = state()
    if rt := s.get("rt"):
        logic.forget(rt)
    s["messages"], s["last_error"], s["resume"] = [], None, NEW


# --- barra lateral -------------------------------------------------------------------------------


def sidebar() -> tuple[str, str, str | None, str, str, str | None]:
    """Devolve (modelo, chave, relógio ISO ou None, pasta dos dados, db, conversa a retomar)."""
    sb = st.sidebar
    sb.header("Configuração")
    model = sb.text_input("Modelo", value=os.environ.get("LLM_MODEL", ""), key="model")
    if env_key := os.environ.get("OPENAI_API_KEY"):
        sb.success("Chave encontrada no ambiente")
        api_key = env_key
    else:
        api_key = sb.text_input("Chave da OpenAI", type="password", key="api_key")

    mode = sb.radio("Relógio", [MODE_DEMO, MODE_REAL, MODE_PICK], key="clock_mode")
    demo_now = os.environ.get("AGENT_NOW") or logic.DEMO_NOW
    clock_text: str | None
    if mode == MODE_DEMO:
        clock_text = demo_now
    elif mode == MODE_REAL:
        clock_text = None
    else:
        day = sb.date_input("Data", value=date(2026, 3, 23), key="pick_date")
        hour = sb.time_input("Hora", value=time(10, 0), key="pick_time")
        clock_text = datetime.combine(day, hour).isoformat(timespec="minutes")
    sb.caption(
        "Com a data real, quase nenhum pedido está dentro do prazo de devolução "
        "(os pedidos de exemplo são de 2026)."
    )
    # Não é segurança: só esconde a aba para quem não é avaliador. Qualquer um liga o botão.
    sb.toggle("Modo avaliador", key="evaluator_mode")

    sb.button("Nova conversa", on_click=new_chat)
    with sb.popover("Esquecer esta conversa"):
        st.write("Apaga o histórico gravado desta conversa e a verificação de identidade.")
        st.button("Sim, apagar", on_click=forget_chat)

    with sb.expander("Avançado"):
        data_dir = logic.resolve_path(
            st.text_input(
                "Pasta dos dados", value=logic.show_path(default_data_dir()), key="data_dir"
            )
        )
        db = logic.resolve_path(
            st.text_input(
                "Arquivo do histórico (:memory: = nada é gravado)",
                value=logic.show_path(default_history_path(os.environ)),
                key="db",
            )
        )
        saved = logic.list_sessions(db)
        labels = {s.id: f"{s.id} (última mensagem: {s.last_at})" for s in saved}
        choice = st.selectbox(
            "Retomar conversa salva",
            [NEW, *labels],
            format_func=lambda k: labels.get(k, k),
            key="resume",
        )
        st.checkbox("Mostrar detalhes técnicos de erros", key="debug")

    with sb.expander("Como usar"):
        st.markdown(HELP_WEB)
    ok, message = data_status(data_dir)
    if sb.button("Verificar dados"):
        data_status.clear()
        ok, message = data_status(data_dir)
    (sb.success if ok else sb.error)(message)
    sb.caption(f"emporio {__version__}")
    return model, api_key, clock_text, data_dir, db, None if choice == NEW else choice


def ensure_runtime(sig: tuple[Any, ...]) -> None:
    """O agente e a conversa só são refeitos quando a configuração muda (e a conversa zera)."""
    s = state()
    if s.get("sig") == sig:
        return
    model, api_key, clock_text, data_dir, db, resume = sig
    s["sig"], s["rt"], s["build_error"], s["last_error"] = sig, None, None, None
    try:
        clock = logic.parse_clock(clock_text)
        repo, policies, identity = shared(data_dir)
        s["rt"] = logic.build_runtime(
            model=model,
            api_key=api_key,
            clock=clock,
            repo=repo,
            policies=policies,
            identity=identity,
            db=db,
            resume_id=resume,
        )
    except Exception as error:
        s["build_error"] = logic.friendly_error(error)
    rt = s["rt"]
    s["messages"] = (
        [{"role": m.role, "content": m.content} for m in rt.session.history] if rt else []
    )


# --- página --------------------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title=f"{AGENT_NAME} - Empório da Música")
    s = state()
    model, api_key, clock_text, data_dir, db, resume = sidebar()
    tracing = s["tracing"] = observability.setup()
    if tracing == "sem_chave":
        st.sidebar.warning(observability.NO_KEY_WARNING)
    data_ok, _ = data_status(data_dir)
    ensure_runtime((model, api_key, clock_text, data_dir, db, resume))
    rt = s.get("rt")

    st.title(AGENT_NAME)
    if rt:
        st.caption(f"Empório da Música - {logic.clock_label(rt.clock)} - modelo {rt.model}")

    if s.get("build_error"):
        st.warning(s["build_error"])
    if not data_ok:
        st.error("O chat está bloqueado até os dados ficarem válidos (veja a barra lateral).")

    prompt = st.chat_input(
        "Escreva sua mensagem", max_chars=MAX_CHARS, disabled=not (rt and data_ok)
    )
    text = s.pop("pending", None) if rt and data_ok else None
    if prompt:
        text = sanitize(prompt)

    messages: list[dict[str, Any]] = s.setdefault("messages", [])
    chat, examples, *extra = st.tabs(
        ["Conversa", "Exemplos", "Métricas"]
        if s.get("evaluator_mode")
        else ["Conversa", "Exemplos"]
    )
    with chat:
        if not messages and not text and not s.get("last_error") and rt and data_ok:
            st.caption("Para começar, escolha um exemplo ou escreva abaixo:")
            for example in logic.EXAMPLES:
                st.button(example, on_click=send_example, args=(example,), key=f"ex-{example}")
        for message in messages:
            render(message)
        if (err := s.get("last_error")) and not text:
            render({"role": "user", "content": err["user"]})
            with st.chat_message("assistant"):
                show_error(err)
        if text and rt:
            s["last_error"] = None
            turn(rt, text, messages)
    with examples:
        examples_tab()
    for tab in extra:
        with tab:
            metrics_tab(rt, messages, tracing)


def show_error(err: dict[str, Any]) -> None:
    st.warning(err["text"])
    if state().get("debug") and err.get("error"):
        st.exception(err["error"])


def turn(rt: logic.Runtime, text: str, messages: list[dict[str, Any]]) -> None:
    render({"role": "user", "content": text})
    with st.chat_message("assistant"):
        holder = st.empty()
        with holder.status(f"{AGENT_NAME} está digitando...") as status:

            def on_event(event: Event) -> None:
                if isinstance(event, ToolStarted):
                    label = TOOL_LABELS.get(event.name, "consultando o sistema")
                    status.update(label=f"{AGENT_NAME} está {label}...")

            reply = logic.run_one_turn(rt, text, on_event)
        holder.empty()
        if reply.kind == "error":  # não entra no histórico nem é gravado
            state()["last_error"] = err = {"user": text, "text": reply.text, "error": reply.error}
            show_error(err)
            return
        messages += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply.text, "details": reply.details},
        ]
        st.markdown(md(reply.text))
        if reply.details and state().get("tracing") == "ativo":
            feedback_widget(reply.details["trace_id"])


envfile.load(Path(".env"))
main()
