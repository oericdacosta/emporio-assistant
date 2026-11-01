"""Registro de reclamações e encaminhamentos para a equipe.

A gravação é local (um arquivo JSONL); o envio real para a gerência é simulado.
"""

from __future__ import annotations

import json
from typing import Any, Final
from uuid import uuid4

from pydantic import Field

from emporio.privacy import redact
from emporio.tools.deps import ToolContext
from emporio.tools.registry import Params, Tool, ToolError

SLA: Final = "retorno em até 24 horas úteis"


class ComplaintParams(Params):
    summary: str = Field(min_length=1, max_length=500, description="O que o cliente relatou")
    order_id: int | None = Field(default=None, ge=1, description="Pedido relacionado, se houver")
    contact: str | None = Field(
        default=None, max_length=120, description="E-mail ou telefone para retorno"
    )


def register_complaint_tool(ctx: ToolContext, args: ComplaintParams) -> dict[str, Any]:
    verified = ctx.session.customer_id is not None
    if not verified and not args.contact:
        raise ToolError(
            "contact_needed",
            "sem um contato a equipe não consegue retornar",
            "Peça ao cliente um e-mail ou telefone e chame de novo.",
        )
    order = ctx.repo.order(args.order_id) if args.order_id and verified else None
    own_order = order.id if order and order.customer_id == ctx.session.customer_id else None

    ticket = f"EMP-{uuid4().hex[:8].upper()}"
    record = {
        "ticket_id": ticket,
        "created_at": ctx.clock.now().isoformat(timespec="seconds"),
        "summary": redact(args.summary),
        "order_id": own_order,
        "verified": verified,
        # cliente verificado é achado pelo pedido; o contato só vale para quem não passou
        "contact": None if verified else args.contact,
    }
    ctx.complaints_log.parent.mkdir(parents=True, exist_ok=True)
    with ctx.complaints_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ticket_id": ticket, "sla": SLA}


def tools() -> list[Tool[Any]]:
    return [
        Tool(
            "register_complaint",
            "Registra uma reclamação, um pedido fora da regra (exceção, negociação) ou uma "
            "dúvida de política para a equipe, e devolve o protocolo. USE quando o cliente "
            "reclamar, quando pedir algo que você não pode conceder ou quando o manual não "
            "cobrir a pergunta. Sem conversa verificada, peça um contato para retorno "
            "(e-mail ou telefone). Informe ao cliente o protocolo e o prazo de retorno.",
            ComplaintParams,
            register_complaint_tool,
        ),
    ]
