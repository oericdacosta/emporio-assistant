from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry


@pytest.fixture
def logged(ctx: ToolContext, tmp_path: Path) -> ToolContext:
    return replace(ctx, complaints_log=tmp_path / "logs" / "complaints.jsonl")


def call(registry: ToolRegistry, ctx: ToolContext, **args: Any) -> dict[str, Any]:
    return registry.call("register_complaint", args, ctx)


def lines(ctx: ToolContext) -> list[dict[str, Any]]:
    text = ctx.complaints_log.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_complaint_without_contact_asks_for_one(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    result = call(registry, logged, summary="Chegou riscado")
    assert result["error"]["code"] == "contact_needed"
    assert not logged.complaints_log.exists()


def test_complaint_is_logged_with_a_redacted_summary(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    result = call(
        registry,
        logged,
        summary="Falem comigo em ana@jmail.com ou (67) 99999-1234",
        contact="ana@jmail.com",
    )
    assert result["ok"] and result["data"]["sla"] == "retorno em até 24 horas úteis"
    (record,) = lines(logged)
    assert record["ticket_id"] == result["data"]["ticket_id"]
    assert record["summary"] == "Falem comigo em [email] ou [telefone]"
    assert record["contact"] == "ana@jmail.com" and record["verified"] is False


def test_verified_customer_is_found_by_order_and_keeps_no_contact(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    registry.call("verify_customer", {"order_id": 4, "proof": "lucas.mendes@jmail.com"}, logged)
    call(registry, logged, summary="Quero falar com a gerência", order_id=4, contact="x@y.com")
    call(registry, logged, summary="Outro assunto", order_id=8)
    own, foreign = lines(logged)
    assert own["order_id"] == 4 and own["contact"] is None and own["verified"] is True
    assert foreign["order_id"] is None  # pedido de outro cliente não entra


def test_summary_is_required_and_bounded(registry: ToolRegistry, logged: ToolContext) -> None:
    assert call(registry, logged, summary="", contact="a@b.com")["ok"] is False
    assert call(registry, logged, summary="x" * 501, contact="a@b.com")["ok"] is False


def test_complaint_takes_the_contact_from_the_conversation(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    said = replace(
        logged, user_texts=("meu email é velho@x.com", "pode me ligar no (67) 99999-1234", "ok")
    )
    result = call(registry, said, summary="Ukulele com defeito")
    assert result["data"]["contact_source"] == "conversa"
    assert lines(said)[0]["contact"] == "(67) 99999-1234"  # a fala mais recente primeiro


def test_complaint_without_contact_anywhere_still_asks(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    said = replace(logged, user_texts=("meu ukulele veio com defeito",))
    assert call(registry, said, summary="x")["error"]["code"] == "contact_needed"


def test_same_contact_twice_returns_the_existing_ticket(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    first = call(registry, logged, summary="Teclado que não liga", contact="(67) 98888-7777")
    again = call(registry, logged, summary="Teclado que não liga", contact="67 98888-7777")
    assert again["data"]["ticket_id"] == first["data"]["ticket_id"]
    assert again["data"]["already_registered"] is True and "note" in again["data"]
    assert len(lines(logged)) == 1


def test_a_clearly_new_reason_or_another_contact_makes_a_new_ticket(
    registry: ToolRegistry, logged: ToolContext
) -> None:
    first = call(registry, logged, summary="Teclado que não liga", contact="a@b.com")
    new_reason = call(registry, logged, summary="Além disso veio riscado", contact="a@b.com")
    other_contact = call(registry, logged, summary="Teclado que não liga", contact="c@d.com")
    tickets = {r["data"]["ticket_id"] for r in (first, new_reason, other_contact)}
    assert len(tickets) == 3 and "already_registered" not in new_reason["data"]
