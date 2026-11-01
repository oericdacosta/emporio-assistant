"""Ponto de entrada do ``emporio``: lê as opções, monta tudo e escolhe o modo de uso."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from emporio import __version__, envfile, observability
from emporio.agent.session import Session, SQLiteSessionStore, default_history_path, new_session_id
from emporio.app import build_agent, check_data, load_data
from emporio.cli import codes
from emporio.cli.chat import Chat
from emporio.cli.demo import run_demo
from emporio.cli.text import AGENT_NAME, EPILOG, format_moment
from emporio.cli.ui import Ui
from emporio.clock import Clock, FixedClock, SystemClock
from emporio.data.repository import DataLoadError
from emporio.errors import CassetteError, ConfigError
from emporio.llm.base import LLMClient
from emporio.llm.factory import make_llm
from emporio.paths import default_data_dir

with contextlib.suppress(ImportError):  # setas e edição de linha; o histórico NÃO vai a disco
    import readline  # noqa: F401

LlmFactory = Callable[[str | None, Mapping[str, str]], LLMClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emporio",
        description=f"Converse com o {AGENT_NAME}, assistente virtual da Empório da Música.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", help="modelo da OpenAI (obrigatório: aqui ou em LLM_MODEL)")
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        help="congela o relógio, ex.: 2026-02-20T10:00 (padrão: AGENT_NOW ou o relógio real)",
    )
    parser.add_argument("--session", metavar="ID", help="retoma uma conversa salva (padrão: nova)")
    parser.add_argument(
        "--trace", action="store_true", help="mostra consultas, tokens e latência por turno"
    )
    parser.add_argument(
        "-m", "--message", action="append", metavar="TEXTO", help="envia, imprime a resposta e sai"
    )
    parser.add_argument(
        "--demo", type=Path, metavar="CASSETE", help="reproduz uma conversa gravada (sem chave)"
    )
    parser.add_argument(
        "--check-data", action="store_true", help="valida os CSVs e as políticas e sai"
    )
    parser.add_argument(
        "--data",
        type=Path,
        help="pasta dos CSVs (padrão: EMPORIO_DATA_DIR, o repositório ou o pacote)",
    )
    parser.add_argument(
        "--db",
        help="arquivo SQLite do histórico (padrão: EMPORIO_HISTORY_DB); ':memory:' = nada em disco",
    )
    parser.add_argument("--debug", action="store_true", help="mostra logs e stack traces")
    parser.add_argument("--version", action="version", version=f"emporio {__version__}")
    return parser


def _clock(text: str | None) -> Clock:
    if not text:
        return SystemClock()
    try:
        return FixedClock.parse(text)
    except ValueError as error:
        raise ConfigError(
            f"--now inválido: {text!r} (use ISO 8601, ex.: 2026-02-20T10:00)"
        ) from error


def _banner(
    ui: Ui, model: str, clock: Clock, fixed: bool, session: Session, db: str, *, interactive: bool
) -> None:
    ui.note(f"Empório da Música - {AGENT_NAME}, assistente virtual", "1")
    if fixed and not ui.trace:
        ui.note(f"  Relógio de demonstração: {format_moment(clock.now())}")
    if ui.trace:  # os detalhes da execução só interessam a quem pediu o trace
        state = f"retomada, {len(session.history)} mensagens" if session.history else "nova"
        ui.note(f"  modelo {model} (OpenAI) | sessão {session.id} ({state}; histórico em {db})")
        ui.note(
            f"  relógio: {format_moment(clock.now())} (America/Campo_Grande), "
            f"{'congelado por --now' if fixed else 'relógio real'}"
        )
    if interactive:
        ui.note("  /ajuda  /novo  /esquecer  /trace  /sair")
    ui.note("")


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    llm_factory: LlmFactory = make_llm,
) -> int:
    args = build_parser().parse_args(argv)
    if env is None:
        envfile.load(Path(".env"))
        env = os.environ
    debug = args.debug or env.get("EMPORIO_DEBUG", "") not in ("", "0")
    logging.getLogger("emporio").addHandler(
        logging.NullHandler()
    )  # sem logs soltos no meio do chat
    if debug:
        logging.basicConfig(
            level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
        )
    ui = Ui(trace=args.trace, debug=debug)

    store: SQLiteSessionStore | None = None
    try:
        data_dir = args.data or default_data_dir()
        if args.check_data:
            ui.say(check_data(data_dir))
            return codes.OK
        if args.demo:
            with observability.disabled():  # a demo não vai ao LangSmith
                return run_demo(args.demo, ui, data_dir)

        now_text = args.now or env.get("AGENT_NOW")
        clock = _clock(now_text)
        llm = llm_factory(args.model, env)
        repo, policies = load_data(data_dir)
        db = args.db or str(default_history_path(env))
        store = SQLiteSessionStore(Path(db), clock=clock)
        session = store.load(args.session) if args.session else Session(new_session_id())
        observability.setup(env)
        agent = build_agent(llm, repo=repo, clock=clock, policies=policies)
        chat = Chat(agent, session, ui, clock=clock, store=store)
    except CassetteError as error:
        ui.error(str(error))
        return codes.CASSETTE
    except (ConfigError, DataLoadError) as error:
        ui.error(str(error))
        return codes.CONFIG

    _banner(ui, llm.model, clock, bool(now_text), session, db, interactive=not args.message)
    try:
        return chat.play(args.message) if args.message else chat.repl()
    except KeyboardInterrupt:  # Ctrl-C fora do prompt e do turno
        ui.say()
        return codes.INTERRUPTED
    finally:
        store.close()
        observability.flush()
