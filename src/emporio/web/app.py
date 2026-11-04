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
from emporio.cli.text import AGENT_NAME, MAX_CHARS, sanitize, scrub
from emporio.data.repository import CsvRepository, DataLoadError
from emporio.identity import IdentityVerifier
from emporio.paths import default_data_dir
from emporio.web import logic, metrics
from emporio.web.examples import examples_tab
from emporio.web.metrics_view import summary_section, turns_section
from emporio.web.results import results_tab

MODE_DEMO = "Demo date (default: AGENT_NOW or 2026-03-23 10:00)"
MODE_REAL = "Now (real time)"
MODE_PICK = "Pick"
NEW = "(new conversation)"

# The progress labels shown while a tool runs. The CLI keeps its own Portuguese ones (cli/text.py).
TOOL_LABELS_EN = {
    "search_products": "searching the catalog",
    "get_product": "looking up the product",
    "calculate_payment_options": "calculating the payment options",
    "calculate_cart_total": "adding up the cart",
    "estimate_shipping": "checking the shipping",
    "get_store_info": "checking the store information",
    "search_policy": "consulting the store policies",
    "verify_customer": "verifying the order details",
    "get_order_status": "looking up the order",
    "check_return_eligibility": "checking the exchange and return deadline",
    "register_complaint": "registering the request",
}
NO_KEY_WARNING_EN = (
    "LangSmith tracing is off: the LANGSMITH_API_KEY key is missing "
    "(the service works normally; see .env.example)"
)
HELP_WEB = f"""**How to talk to {AGENT_NAME}**

Write in the box below and press Enter (up to {MAX_CHARS} characters), or click one of the examples. {AGENT_NAME} answers in Portuguese, so write to {AGENT_NAME} in Portuguese.

**Some ideas** (in Portuguese)
- Quais violões até R\\$ 1000?
- Quanto custa o Takamine GD20?
- Que horas a loja abre?
- Quero devolver um pedido

**To talk about an order**, give its number and the e-mail on file (or the last 4 digits of the phone). That is how {AGENT_NAME} confirms the order is yours.

**In this sidebar**
- *Evaluator mode*: opens the Metrics tab (the queries of each answer, tokens, latency and cost).
- *New conversation*: starts from scratch; the previous one stays saved.
- *Clock*: the demo date is the one of the store's orders."""


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
        status = "ok" if tool["ok"] else f"error ({tool['error']})"
        st.markdown(f"**{tool['name']}** - {status} - {tool['ms']} ms")
        st.code(json.dumps(tool["arguments"], ensure_ascii=False), language="json")
        st.text(tool["summary"])
    if not details["tools"]:
        st.caption("No queries in this turn.")
    st.caption(
        f"tokens in/out/cache: {details['input_tokens']}/{details['output_tokens']}/"
        f"{details['cached_tokens']} - {details['llm_calls']} model call(s) - "
        f"{details['ms']} ms - trace_id {details['trace_id']}"
    )
    for block in details["guard_blocks"]:
        st.warning(
            md(
                "The guard blocked an answer.\n\n"
                f"Draft: {block['draft']}\n\n"
                f"Without a source in the queries: {'; '.join(block['violations'])}"
            )
        )
    if details["fallback"]:
        st.warning("The safe answer was sent in place of the model's answer.")


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
    with st.expander(f"Details of the queries of each turn ({len(detailed)})"):
        for message in detailed:
            render_details(message["details"])
        if not detailed:
            st.caption("No turn yet.")


def refresh_metrics() -> None:
    """Primeiro clique: libera a leitura do LangSmith. Os seguintes: descartam o cache."""
    state()["metrics_on"] = True
    load_metrics.clear()


def metrics_tab(rt: logic.Runtime | None, messages: list[dict[str, Any]], tracing: str) -> None:
    """Aba do modo avaliador. Nada aqui pode derrubar a conversa."""
    st.info(
        "This shows the metrics of your own conversations, read from your LangSmith (it needs "
        "LANGSMITH_API_KEY in your .env). The results measured by the author are in the Results tab."
    )
    if tracing == "ativo":  # fica no topo, antes e depois do clique
        st.info(
            "Click **Refresh** to see the LangSmith data. It takes a few seconds "
            "to arrive after each answer."
        )
    st.button("Refresh", key="refresh-metrics", on_click=refresh_metrics)
    if tracing != "ativo":
        st.info("LangSmith is not configured: set LANGSMITH_API_KEY in .env (see .env.example).")
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
            f"Could not read LangSmith right now ({type(error).__name__}: {scrub(str(error))[:200]}). "
            "Try Refresh."
        )
        details_section(messages)
        return
    turns_section(data["turns"])
    details_section(messages)
    summary_section(
        data["period_rows"],
        f"Last {metrics.PERIOD_DAYS} days, up to {metrics.PERIOD_LIMIT} turns, across all conversations.",
    )
    if data["experiment"]:
        name, url = data["experiment"]
        st.caption(f"Latest evaluation experiment: [{name}]({url})" if url else name)


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
        st.caption(f"Could not record your rating right now ({reason}).")


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
    sb.header("Settings")
    model = sb.text_input("Model", value=os.environ.get("LLM_MODEL", ""), key="model")
    if env_key := os.environ.get("OPENAI_API_KEY"):
        sb.success("Key found in the environment")
        api_key = env_key
    else:
        api_key = sb.text_input("OpenAI key", type="password", key="api_key")

    mode = sb.radio("Clock", [MODE_DEMO, MODE_REAL, MODE_PICK], key="clock_mode")
    demo_now = os.environ.get("AGENT_NOW") or logic.DEMO_NOW
    clock_text: str | None
    if mode == MODE_DEMO:
        clock_text = demo_now
    elif mode == MODE_REAL:
        clock_text = None
    else:
        day = sb.date_input("Date", value=date(2026, 3, 23), key="pick_date")
        hour = sb.time_input("Time", value=time(10, 0), key="pick_time")
        clock_text = datetime.combine(day, hour).isoformat(timespec="minutes")
    sb.caption(
        "With the real date, almost no order is within the return period "
        "(the sample orders are from 2026)."
    )
    # Não é segurança: só esconde a aba para quem não é avaliador. Qualquer um liga o botão.
    sb.toggle("Evaluator mode", key="evaluator_mode")

    sb.button("New conversation", on_click=new_chat)
    with sb.popover("Forget this conversation"):
        st.write("Erases the saved history of this conversation and the identity verification.")
        st.button("Yes, erase", on_click=forget_chat)

    with sb.expander("Advanced"):
        data_dir = logic.resolve_path(
            st.text_input("Data folder", value=logic.show_path(default_data_dir()), key="data_dir")
        )
        db = logic.resolve_path(
            st.text_input(
                "History file (:memory: = nothing is saved)",
                value=logic.show_path(default_history_path(os.environ)),
                key="db",
            )
        )
        saved = logic.list_sessions(db)
        labels = {s.id: f"{s.id} (last message: {s.last_at})" for s in saved}
        choice = st.selectbox(
            "Resume a saved conversation",
            [NEW, *labels],
            format_func=lambda k: labels.get(k, k),
            key="resume",
        )
        st.checkbox("Show technical error details", key="debug")

    with sb.expander("How to use"):
        st.markdown(HELP_WEB)
    ok, message = data_status(data_dir)
    if sb.button("Check data"):
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
        st.sidebar.warning(NO_KEY_WARNING_EN)
    data_ok, _ = data_status(data_dir)
    ensure_runtime((model, api_key, clock_text, data_dir, db, resume))
    rt = s.get("rt")

    st.title(AGENT_NAME)
    if rt:
        st.caption(f"Empório da Música - {logic.clock_label(rt.clock)} - model {rt.model}")

    if s.get("build_error"):
        st.warning(s["build_error"])
    if not data_ok:
        st.error("The chat is blocked until the data is valid (see the sidebar).")

    prompt = st.chat_input("Write your message", max_chars=MAX_CHARS, disabled=not (rt and data_ok))
    text = s.pop("pending", None) if rt and data_ok else None
    if prompt:
        text = sanitize(prompt)

    messages: list[dict[str, Any]] = s.setdefault("messages", [])
    chat, examples, results, *extra = st.tabs(
        ["Chat", "Examples", "Results", "Metrics"]
        if s.get("evaluator_mode")
        else ["Chat", "Examples", "Results"]
    )
    with chat:
        if not messages and not text and not s.get("last_error") and rt and data_ok:
            st.caption("To start, choose an example or write below:")
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
    with results:
        results_tab()
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
        with holder.status(f"{AGENT_NAME} is typing...") as status:

            def on_event(event: Event) -> None:
                if isinstance(event, ToolStarted):
                    label = TOOL_LABELS_EN.get(event.name, "consulting the system")
                    status.update(label=f"{AGENT_NAME} is {label}...")

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
