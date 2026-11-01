from __future__ import annotations

from typing import Any

import pytest

from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry


def call(registry: ToolRegistry, ctx: ToolContext, tool: str, /, **args: Any) -> dict[str, Any]:
    return registry.call(tool, args, ctx)


def ids(result: dict[str, Any]) -> list[int]:
    return [item["product_id"] for item in result["data"]["items"]]


def test_guitars_up_to_1000_come_back_cheapest_first(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    result = call(registry, ctx, "search_products", category="violoes", max_price_brl=1000, limit=8)
    assert result["data"]["total_matches"] == 12
    assert result["data"]["filters_applied"] == {
        "category": "violoes",
        "max_price_brl": "R$ 1.000,00",
    }
    assert ids(result) == [83, 85, 89, 88, 81, 84, 87, 86]
    first = result["data"]["items"][0]
    assert first["effective_price_brl"] == "R$ 429,90" and first["availability"] == "in_stock"


def test_search_says_how_many_matches_were_not_shown(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    cut = call(registry, ctx, "search_products", category="violoes", max_price_brl=1000, limit=5)[
        "data"
    ]
    assert (cut["total_matches"], cut["shown"], cut["not_shown"]) == (12, 5, 7)
    assert "12 produtos ao todo" in cut["note"] and "só 5" in cut["note"]

    whole = call(registry, ctx, "search_products", category="violoes", max_price_brl=1000, limit=8)[
        "data"
    ]
    assert whole["not_shown"] == 4
    empty = call(registry, ctx, "search_products", category="cordas_orquestrais")["data"]
    assert (empty["shown"], empty["not_shown"]) == (0, 0) and "note" not in empty
    one = call(registry, ctx, "search_products", query="gd20", limit=1)["data"]
    assert one["not_shown"] == 0 and "note" not in one


def test_search_reports_a_sold_out_model_instead_of_hiding_it(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "search_products", query="Giannini GF-3D")["data"]
    assert data["unavailable_matches"] == [
        {
            "product_id": 96,
            "name": "Giannini GF-3D Dreadnought Sunburst",
            "availability": "out_of_stock",
        }
    ]
    assert "não está à venda" in data["unavailable_note"]
    assert 96 not in ids({"data": data})

    listing = call(registry, ctx, "search_products", category="violoes")["data"]
    assert "unavailable_matches" not in listing and "unavailable_note" not in listing


def test_search_accepts_json_numbers_for_prices(registry: ToolRegistry, ctx: ToolContext) -> None:
    result = call(registry, ctx, "search_products", category="violoes", max_price_brl=999.0)
    assert result["data"]["total_matches"] == 12  # 999 inclui o Tagima TW-7


def test_unknown_category_lists_the_valid_ones(registry: ToolRegistry, ctx: ToolContext) -> None:
    error = call(registry, ctx, "search_products", category="pedais")["error"]
    assert error["code"] == "unknown_category" and "violoes" in error["hint"]


def test_empty_category_is_not_an_error(registry: ToolRegistry, ctx: ToolContext) -> None:
    result = call(registry, ctx, "search_products", category="cordas_orquestrais")
    assert result["ok"] and result["data"]["total_matches"] == 0 and result["data"]["items"] == []


def test_brand_search_returns_candidates(registry: ToolRegistry, ctx: ToolContext) -> None:
    result = call(registry, ctx, "search_products", query="takamine")
    assert {c["product_id"] for c in result["data"]["candidates"]} == {95, 100, 105, 115}


def test_get_product_by_model_name(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "get_product", name="gd20")["data"]
    assert data["product_id"] == 95
    assert data["list_price_brl"] == data["effective_price_brl"] == "R$ 2.199,00"
    assert data["promo_percent"] is None and data["pix_price_brl"] == "R$ 2.089,05"
    assert data["specs"]["strings"] == "steel" and "alternatives" not in data


def test_get_product_with_an_ambiguous_name_asks_which(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "get_product", name="takamine")["data"]
    assert {c["product_id"] for c in data["candidates"]} == {95, 100, 105, 115}
    assert "product_id" not in data


def test_promotion_shows_both_prices_and_no_pix(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "get_product", product_id=121)["data"]
    assert data["list_price_brl"] == "R$ 549,00" and data["promo_percent"] == "20%"
    assert data["effective_price_brl"] == "R$ 439,20" and data["pix_price_brl"] is None


@pytest.mark.parametrize(
    ("product_id", "availability"),
    [(96, "out_of_stock"), (136, "discontinued"), (130, "coming_soon")],
)
def test_unavailable_products_come_with_alternatives_and_no_stock(
    registry: ToolRegistry, ctx: ToolContext, product_id: int, availability: str
) -> None:
    data = call(registry, ctx, "get_product", product_id=product_id)["data"]
    assert data["availability"] == availability and "stock_quantity" not in data
    assert 1 <= len(data["alternatives"]) <= 3
    assert all(a["product_id"] != product_id for a in data["alternatives"])


def test_unknown_product_is_a_hinted_error(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert (
        call(registry, ctx, "get_product", product_id=9999)["error"]["code"] == "product_not_found"
    )
    assert call(registry, ctx, "get_product", name="flauta")["error"]["code"] == "product_not_found"


def test_get_product_needs_exactly_one_identifier(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert call(registry, ctx, "get_product")["error"]["code"] == "invalid_arguments"
    both = call(registry, ctx, "get_product", product_id=95, name="gd20")
    assert both["error"]["code"] == "invalid_arguments"
    wrong_field = call(registry, ctx, "get_product", id=95)
    assert "product_id" in wrong_field["error"]["hint"]


def test_payment_options_for_a_product_without_promotion(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "calculate_payment_options", product_id=95)["data"]
    assert data["base_price_brl"] == "R$ 2.199,00" and data["pix_total_brl"] == "R$ 2.089,05"
    assert data["max_installments"] == "12x" and data["combined_payment_allowed"] is True
    twelve = data["installments"][-1]
    assert twelve["installments"] == "12x" and twelve["total_brl"] == "R$ 2.199,00"
    assert twelve["plan"] == "12x de R$ 183,25"


def test_payment_options_in_promotion_have_no_pix(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "calculate_payment_options", product_id=121)["data"]
    assert data["base_price_brl"] == "R$ 439,20" and data["pix_total_brl"] is None
    assert "promoção" in data["pix_note"] and data["combined_payment_allowed"] is False


def test_payment_options_respect_the_installment_floor(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    data = call(registry, ctx, "calculate_payment_options", amount_brl=240)["data"]
    assert data["max_installments"] == "3x"  # 4x daria R$ 60,00, abaixo do mínimo de R$ 80,00


def test_payment_quantity_multiplies_the_price(registry: ToolRegistry, ctx: ToolContext) -> None:
    data = call(registry, ctx, "calculate_payment_options", product_id=81, quantity=2)["data"]
    assert data["base_price_brl"] == "R$ 1.199,80"


def test_payment_needs_exactly_one_source(registry: ToolRegistry, ctx: ToolContext) -> None:
    assert call(registry, ctx, "calculate_payment_options")["error"]["code"] == "invalid_arguments"
    both = call(registry, ctx, "calculate_payment_options", product_id=95, amount_brl=100)
    assert both["error"]["code"] == "invalid_arguments"
    assert call(registry, ctx, "calculate_payment_options", amount_brl=0)["ok"] is False
