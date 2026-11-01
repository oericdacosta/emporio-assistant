"""Tools da loja: dados e horário, e frete."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from emporio.domain.hours import SCHEDULE_TEXT, store_status, weekday_pt
from emporio.domain.pricing import brl, quote
from emporio.domain.shipping import (
    FLAT_FEE,
    FREE_SHIPPING_ABOVE,
    Zone,
    is_large_item,
    quote_shipping,
)
from emporio.tools.catalog import require_product
from emporio.tools.deps import ToolContext
from emporio.tools.registry import Money, Params, Tool

NAME = "Empório da Música"
ADDRESS = "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333"
PHONE = "(67) 3341-4444"
WHATSAPP = "(67) 3321-4500"
PHONES = (
    {"label": "Telefone", "number": PHONE},
    {"label": "WhatsApp", "number": WHATSAPP},
)
EMAIL = "contato@emporiodamusica.com.br"


class StoreInfoParams(Params):
    pass


def get_store_info_tool(ctx: ToolContext, args: StoreInfoParams) -> dict[str, Any]:
    status = store_status(ctx.clock.now())
    return {
        "name": NAME,
        "address": ADDRESS,
        "phones": list(PHONES),
        "email": EMAIL,
        "hours": SCHEDULE_TEXT,
        "now": status.now.isoformat(timespec="minutes"),
        "weekday": weekday_pt(status.now.date()),
        "is_open_now": status.is_open,
        "today_hours": status.today,
        "closes_at": status.closes_at.isoformat(timespec="minutes") if status.closes_at else None,
        "next_open": status.next_open.isoformat(timespec="minutes") if status.next_open else None,
    }


class ShippingParams(Params):
    city: str = Field(min_length=1, description="Cidade de entrega")
    order_total_brl: Money | None = Field(default=None, description="Subtotal do pedido")
    product_id: int | None = Field(
        default=None, ge=1, description="Opcional: detecta item de grande porte e dá o subtotal"
    )


def estimate_shipping_tool(ctx: ToolContext, args: ShippingParams) -> dict[str, Any]:
    large = False
    total = args.order_total_brl
    if args.product_id is not None:
        product = require_product(ctx, args.product_id)
        category = ctx.repo.category(product.category_id)
        large = category is not None and is_large_item(category.name)
        if total is None:
            total = quote(product, ctx.repo.promotions_for(product.id)).price

    if total is None:
        # sem subtotal não dá para saber se o frete é grátis em Campo Grande
        result = quote_shipping(args.city, FREE_SHIPPING_ABOVE + 1, large_item=large)
        fee, free = None, None
    else:
        result = quote_shipping(args.city, total, large_item=large)
        fee = result.fee
        free = None if fee is None else fee == 0

    data: dict[str, Any] = {
        "zone": result.zone.value,
        "shipping_brl": None if fee is None else brl(fee),
        "free": free,
        "delivery_time": result.delivery_time,
        "carriers": list(result.carriers),
        "needs_quote": result.needs_quote,
        "note": result.note,
    }
    if result.zone is Zone.CAMPO_GRANDE:
        data["free_above_brl"] = brl(FREE_SHIPPING_ABOVE)
        data["flat_fee_brl"] = brl(FLAT_FEE)
        if total is None:
            data["note"] = "Informe o subtotal para saber se o frete é grátis."
    if result.needs_quote:
        data["note"] += " Item de grande porte: frete especial, com cotação individual."
    return data


def tools() -> list[Tool[Any]]:
    return [
        Tool(
            "estimate_shipping",
            "Frete e prazo por cidade de entrega. Campo Grande: taxa fixa ou frete grátis, "
            "conforme o subtotal. Outras cidades: modalidades e prazos, SEM valor (depende "
            "de CEP, peso e dimensões; não invente). Item de grande porte fora de Campo "
            "Grande exige cotação. NÃO use para rastrear pedido.",
            ShippingParams,
            estimate_shipping_tool,
        ),
        Tool(
            "get_store_info",
            "Endereço, telefone e WhatsApp, e-mail, horário de funcionamento e se a loja está "
            "ABERTA AGORA, com o próximo horário de abertura. USE para 'onde fica', 'abre "
            "sábado?', 'estão abertos?'. NÃO use para regras de troca, frete ou pagamento "
            "(use search_policy).",
            StoreInfoParams,
            get_store_info_tool,
        ),
    ]
