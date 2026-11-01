from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from emporio.agent.failures import describe
from emporio.agent.loop import Outcome, ToolRecord, TurnResult
from emporio.agent.turn_log import configure, failure_record, turn_record, write
from emporio.clock import TZ
from emporio.errors import LLMError, ProviderFatal, ProviderUnavailable
from emporio.llm.base import Message, Usage

NOW = datetime(2026, 3, 23, 10, 0, tzinfo=TZ)


def test_customer_messages_per_provider_failure() -> None:
    busy = describe(ProviderUnavailable("429", rate_limited=True))
    assert "muita demanda" in busy.message and busy.retryable and not busy.misconfigured

    down = describe(ProviderUnavailable("502"))
    assert "instabilidade" in down.message and down.retryable and "(67) 3321-4500" in down.message

    config = describe(ProviderFatal("chave inválida"))
    assert config.misconfigured and not config.retryable

    assert describe(LLMError("?")).retryable


def test_customer_messages_have_no_money_and_no_technical_detail() -> None:
    for error in (
        ProviderUnavailable("x", rate_limited=True),
        ProviderUnavailable("x"),
        ProviderFatal("sk-secret"),
    ):
        message = describe(error).message
        assert "R$" not in message and "sk-secret" not in message and "Traceback" not in message


def result(**overrides: object) -> TurnResult:
    base: dict[str, object] = {
        "text": "O GD20 sai por R$ 2.199,00.",
        "outcome": Outcome.ANSWERED,
        "new_messages": (Message("user", "oi"), Message("assistant", "ok")),
        "tools": (
            ToolRecord(
                "verify_customer",
                {"order_id": 4, "proof": "lucas.mendes@jmail.com"},
                True,
                None,
                0.012,
                48,
            ),
            ToolRecord(
                "register_complaint",
                {"summary": "ligue (67) 99812-3456", "contact": "7821"},
                True,
                None,
                0.001,
                60,
            ),
        ),
        "usage": Usage(900, 40, 700),
        "llm_calls": 3,
        "seconds": 2.5,
        "trace_id": "abc123",
        "guard_blocked": False,
    }
    return TurnResult(**{**base, **overrides})  # type: ignore[arg-type]


def test_turn_record_has_the_numbers_and_no_personal_data() -> None:
    record = turn_record("s1", result(), NOW)
    assert record["tokens"] == {"input": 900, "cached": 700, "output": 40}
    assert (record["outcome"], record["llm_calls"], record["ms"]) == ("answered", 3, 2500)
    assert record["reply_chars"] == len("O GD20 sai por R$ 2.199,00.")

    text = json.dumps(record, ensure_ascii=False)
    assert "lucas.mendes" not in text and "99812" not in text and "7821" not in text
    assert "O GD20 sai" not in text  # nem a resposta nem a fala do cliente vão ao log
    assert record["tools"][0]["arguments"] == {"order_id": 4, "proof": "***"}
    assert record["tools"][1]["arguments"] == {"summary": "ligue [telefone]", "contact": "***"}


def test_log_is_one_json_line_per_turn(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "turns.jsonl"
    logger = configure(path)
    write(turn_record("s1", result(), NOW))
    write(turn_record("s1", result(outcome=Outcome.FALLBACK, guard_blocked=True), NOW))
    write(failure_record("s1", ProviderUnavailable("x"), NOW))
    for handler in logger.handlers:
        handler.flush()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["turn", "turn", "llm_failure"]
    assert lines[1]["guard_blocked"] is True and lines[2]["error"] == "ProviderUnavailable"
    logging.getLogger("emporio.turns").handlers.clear()
