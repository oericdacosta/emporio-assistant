"""Um registro JSON por turno, para auditar e medir o agente sem expor dados pessoais.

Guarda tokens, latência, tools chamadas e se a guarda de fundamentação agiu. Não guarda o texto
do cliente nem o da resposta (só o tamanho), nem os resultados das tools (só o tamanho). Nos
argumentos das tools, o que identifica o cliente vira ``***`` e o resto passa pela máscara de
dados pessoais.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from emporio.agent.loop import ToolRecord, TurnResult
from emporio.errors import LLMError
from emporio.privacy import redact

LOGGER_NAME: Final = "emporio.turns"
_SECRET_ARGUMENTS: Final = frozenset({"proof", "contact"})


def mask(value: Any) -> Any:
    """Máscara para qualquer coisa que vá a log ou a trace: e-mail, telefone etc. e o que identifica o cliente."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: "***" if k in _SECRET_ARGUMENTS else mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(item) for item in value]
    return value


def _tool(record: ToolRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "arguments": mask(record.arguments),
        "ok": record.ok,
        "error": record.error_code,
        "ms": round(record.seconds * 1000),
        "result_chars": record.result_chars,
    }


def turn_record(session_id: str, result: TurnResult, now: datetime) -> dict[str, Any]:
    return {
        "event": "turn",
        "at": now.isoformat(timespec="seconds"),
        "session_id": session_id,
        "trace_id": result.trace_id,
        "outcome": result.outcome.value,
        "guard_blocked": result.guard_blocked,
        "llm_calls": result.llm_calls,
        "tokens": {
            "input": result.usage.input_tokens,
            "cached": result.usage.cached_tokens,
            "output": result.usage.output_tokens,
        },
        "ms": round(result.seconds * 1000),
        "tools": [_tool(t) for t in result.tools],
        "reply_chars": len(result.text),
    }


def failure_record(session_id: str, error: LLMError, now: datetime) -> dict[str, Any]:
    return {
        "event": "llm_failure",
        "at": now.isoformat(timespec="seconds"),
        "session_id": session_id,
        "error": type(error).__name__,
    }


def configure(path: Path) -> logging.Logger:
    """Liga o registro em um arquivo JSON Lines (uma linha por turno)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    return logger


def write(record: dict[str, Any]) -> None:
    logging.getLogger(LOGGER_NAME).info(json.dumps(record, ensure_ascii=False))
