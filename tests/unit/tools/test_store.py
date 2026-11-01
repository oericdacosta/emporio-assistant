from __future__ import annotations

from typing import Any

from emporio.clock import FixedClock
from emporio.data.policies import Topic
from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry


def call(registry: ToolRegistry, ctx: ToolContext, tool: str, /, **args: Any) -> dict[str, Any]:
    return registry.call(tool, args, ctx)


def test_store_info_when_open(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "get_store_info")["data"]
    assert data["is_open_now"] is True and data["weekday"] == "segunda-feira"
    assert data["closes_at"] == "2026-03-23T18:00-04:00" and data["next_open"] is None
    assert {"label": "WhatsApp", "number": "(67) 3321-4500"} in data["phones"]
    assert {"label": "Telefone", "number": "(67) 3341-4444"} in data["phones"]
    assert data["email"] == "contato@emporiodamusica.com.br"


def test_store_info_on_sunday_gives_the_next_opening(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    sunday = ToolContext(ctx.repo, FixedClock.parse("2026-03-22T11:00:00"), ctx.policies)
    data = call(registry, sunday, "get_store_info")["data"]
    assert data["is_open_now"] is False and data["next_open"] == "2026-03-23T09:00-04:00"
    assert data["closes_at"] is None


def test_shipping_in_campo_grande_follows_the_subtotal(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    small = call(registry, ctx, "estimate_shipping", city="Campo Grande", order_total_brl=499.9)
    assert small["data"]["shipping_brl"] == "R$ 35,00" and small["data"]["free"] is False
    exact = call(registry, ctx, "estimate_shipping", city="campo grande", order_total_brl=500)
    assert exact["data"]["shipping_brl"] == "R$ 35,00"
    free = call(
        registry, ctx, "estimate_shipping", city="  Campo   Grande ", order_total_brl=500.01
    )
    assert free["data"]["shipping_brl"] == "R$ 0,00" and free["data"]["free"] is True


def test_shipping_uses_the_product_price_when_there_is_no_total(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "estimate_shipping", city="Campo Grande", product_id=95)["data"]
    assert data["free"] is True and data["free_above_brl"] == "R$ 500,00"


def test_shipping_without_any_total_does_not_guess(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "estimate_shipping", city="Campo Grande")["data"]
    assert data["shipping_brl"] is None and data["free"] is None
    assert data["flat_fee_brl"] == "R$ 35,00"


def test_other_cities_have_carriers_and_no_value(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "estimate_shipping", city="Dourados", order_total_brl=900)["data"]
    assert data["zone"] == "other" and data["shipping_brl"] is None and data["free"] is None
    assert len(data["carriers"]) == 3 and data["needs_quote"] is False


def test_large_item_to_another_city_needs_a_quote(registry: ToolRegistry, ctx: ToolContext) -> None:
    drum_kit = 139  # categoria de baterias e percussão
    data = call(registry, ctx, "estimate_shipping", city="Dourados", product_id=drum_kit)["data"]
    assert data["needs_quote"] is True and "cotação individual" in data["note"]


def test_shipping_requires_a_city(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert call(registry, ctx, "estimate_shipping", city="")["error"]["code"] == "invalid_arguments"
    assert call(registry, ctx, "estimate_shipping")["error"]["code"] == "invalid_arguments"


def test_policy_returns_the_literal_section_with_its_notes(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    sections = call(registry, ctx, "search_policy", topics=["trocas_devolucoes"])["data"][
        "sections"
    ]
    assert [s["number"] for s in sections] == [4]
    assert "7 (sete) dias corridos" in sections[0]["text"]
    assert any("30 dias" in note for note in sections[0]["notes"])


def test_policy_topic_limits(registry: ToolRegistry, ctx: ToolContext) -> None:
    four = [Topic.EMPRESA, Topic.HORARIO, Topic.FRETE, Topic.GARANTIA]
    assert call(registry, ctx, "search_policy", topics=four)["error"]["code"] == "invalid_arguments"
    assert call(registry, ctx, "search_policy", topics=[])["error"]["code"] == "invalid_arguments"
    assert call(registry, ctx, "search_policy", topics=["devolver"])["ok"] is False
    everything = call(registry, ctx, "search_policy", topics=["todos"])["data"]["sections"]
    assert len(everything) == 10
