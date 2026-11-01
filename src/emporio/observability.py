"""Tracing no LangSmith: ligado por padrão, e nunca pode derrubar nem atrasar um turno.

Tudo que toca o SDK passa por aqui e engole os erros. Sem chave, o app funciona com um aviso.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import uuid
import warnings
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import Any, Final, Literal

State = Literal["ativo", "sem_chave", "desligado"]

NO_KEY_WARNING: Final = (
    "Tracing LangSmith desligado: falta a chave LANGSMITH_API_KEY "
    "(o atendimento funciona normalmente; veja .env.example)"
)

_state: State = "desligado"
_warned = _warmed = False


def setup(env: Mapping[str, str] | None = None) -> State:
    """Liga o tracing por padrão e diz em que pé ele está. Chame antes do primeiro turno."""
    global _state, _warned, _warmed
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    env = os.environ if env is None else env
    tracing = (env.get("LANGSMITH_TRACING") or os.environ["LANGSMITH_TRACING"]).strip().lower()
    key = env.get("LANGSMITH_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if tracing in ("false", "0", "no", "off"):
        _state = "desligado"
    elif not key:
        _state = "sem_chave"
        with suppress(Exception):  # sem chave, tentar enviar só gera erro
            from langsmith.run_trees import configure

            configure(enabled=False)
        if not _warned:
            _warned = True
            print(f"Aviso: {NO_KEY_WARNING}.", file=sys.stderr)
    else:
        _state = "ativo"
    # o SDK avisa e loga por conta própria; no meio do chat isso é ruído
    with suppress(Exception):
        from langsmith.utils import LangSmithWarning

        warnings.simplefilter("ignore", LangSmithWarning)
    logging.getLogger("langsmith").setLevel(logging.CRITICAL)
    if _state == "ativo" and not _warmed:
        _warmed = True
        threading.Thread(target=_warm, daemon=True).start()
    return _state


def _client() -> Any:
    from langsmith.run_trees import get_cached_client

    return get_cached_client()


def _warm() -> None:
    """O SDK consulta ``/info`` no primeiro run; fazê-lo aqui tira isso do caminho do turno."""
    with suppress(Exception):
        getattr(_client(), "info")  # noqa: B009


def flush(timeout: float = 5) -> None:
    """Espera os envios pendentes por no máximo ``timeout`` segundos. Nunca levanta."""
    if _state != "ativo":
        return

    def run() -> None:
        with suppress(Exception):
            _client().flush(timeout=timeout)

    with suppress(Exception):
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout)


def annotate(metadata: Mapping[str, Any]) -> None:
    """Soma metadados ao run do turno em andamento (se houver)."""
    with suppress(Exception):
        from langsmith.run_helpers import get_current_run_tree

        if run := get_current_run_tree():
            run.add_metadata(dict(metadata))


FEEDBACK_KEY: Final = "user_score"


def _write_feedback(trace_id: str, score: float) -> None:
    # id fixo por turno: um clique repetido atualiza em vez de duplicar
    feedback_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{trace_id}:{FEEDBACK_KEY}")
    client = _client()
    try:
        client.create_feedback(trace_id, FEEDBACK_KEY, score=score, feedback_id=feedback_id)
    except Exception:
        client.update_feedback(feedback_id, score=score)


def send_feedback(
    trace_id: str, score: float, on_error: Callable[[str], None] | None = None
) -> threading.Thread | None:
    """Grava 👍 (1.0) ou 👎 (0.0) no run do turno, numa thread daemon. Nunca levanta nem bloqueia."""
    if _state != "ativo":
        return None

    def run() -> None:
        try:
            _write_feedback(trace_id, score)
        except Exception as error:
            if on_error:
                with suppress(Exception):
                    on_error(type(error).__name__)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


@contextmanager
def disabled() -> Iterator[None]:
    """Nada dentro do bloco é enviado (modo ``--demo``)."""
    try:
        from langsmith.run_helpers import tracing_context

        ctx = tracing_context(enabled=False)
    except Exception:
        yield
        return
    with ctx:
        yield
