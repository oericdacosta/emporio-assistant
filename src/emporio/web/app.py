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

from emporio import __version__, envfile
from emporio.agent.session import Session, default_history_path, new_session_id
from emporio.agent.types import Event, ToolStarted
from emporio.app import check_data, load_data
from emporio.cli.text import AGENT_NAME, MAX_CHARS, PRIVACY, TOOL_LABELS, sanitize
from emporio.data.repository import CsvRepository, DataLoadError
from emporio.identity import IdentityVerifier
from emporio.paths import default_data_dir
from emporio.web import logic

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
- *Mostrar detalhes*: vê o que foi consultado em cada resposta.
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
    with st.expander("Detalhes do turno", expanded=bool(state().get("show_details"))):
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


def render(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(md(message["content"]))
        if message.get("details"):
            render_details(message["details"])


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
    sb.toggle("Mostrar detalhes", key="show_details")

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
    data_ok, _ = data_status(data_dir)
    ensure_runtime((model, api_key, clock_text, data_dir, db, resume))
    rt = s.get("rt")

    st.title(AGENT_NAME)
    if rt:
        st.caption(f"Empório da Música - {logic.clock_label(rt.clock)} - modelo {rt.model}")
    where = (
        "nada é gravado"
        if db == logic.IN_MEMORY
        else "o histórico fica num SQLite local, com dados pessoais redigidos"
    )
    st.info(f"{PRIVACY} Aqui, {where}.")

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

            reply = logic.run_one_turn(rt, text, on_event, log_turns=True)
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
        if reply.details:
            render_details(reply.details)


envfile.load(Path(".env"))
logic.ensure_turn_log()
main()
