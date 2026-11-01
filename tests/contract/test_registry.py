"""Contrato das tools: nomes, esquemas e formato das saídas."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry

CANONICAL = frozenset(
    {
        "search_products",
        "get_product",
        "calculate_payment_options",
        "estimate_shipping",
        "get_store_info",
        "search_policy",
    }
)
MONEY = re.compile(r"^R\$ \d{1,3}(\.\d{3})*,\d{2}$")
PERCENT = re.compile(r"^\d{1,2}%$")
SRC = Path(__file__).resolve().parents[2] / "src"

CALLS = [
    ("search_products", {"category": "violoes", "max_price_brl": 1000}),
    ("search_products", {"query": "takamine", "only_available": False}),
    ("get_product", {"name": "gd20"}),
    ("get_product", {"product_id": 121}),
    ("get_product", {"product_id": 96}),
    ("calculate_payment_options", {"product_id": 95}),
    ("calculate_payment_options", {"product_id": 121, "quantity": 2}),
    ("calculate_payment_options", {"amount_brl": 2500}),
    ("estimate_shipping", {"city": "Campo Grande", "order_total_brl": 300}),
    ("estimate_shipping", {"city": "Dourados", "product_id": 95}),
    ("get_store_info", {}),
    ("search_policy", {"topics": ["pagamento", "frete_entrega"]}),
]


def test_registry_has_the_canonical_tools(registry: ToolRegistry) -> None:
    assert set(registry.names()) == CANONICAL


def test_specs_are_plain_json_schemas(registry: ToolRegistry) -> None:
    for spec in registry.specs():
        json.dumps(spec)
        assert spec["description"].startswith(
            ("Lista", "Ficha", "Calcula", "Frete", "Endere", "Texto")
        )
        schema = spec["parameters"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert "$ref" not in json.dumps(schema) and "title" not in schema
        assert set(schema.get("required", ())) <= set(schema["properties"])


def test_category_enum_comes_from_the_csv(registry: ToolRegistry, ctx: ToolContext) -> None:
    spec = next(s for s in registry.specs() if s["name"] == "search_products")
    enum = spec["parameters"]["properties"]["category"]["enum"]
    assert enum == [
        "guitarras",
        "baixos",
        "baterias_e_percussao",
        "teclados_e_pianos",
        "violoes",
        "instrumentos_de_sopro_madeiras",
        "instrumentos_de_sopro_metais",
        "cordas_orquestrais",
        "ukuleles",
    ]
    for slug in enum:
        assert registry.call("search_products", {"category": slug}, ctx)["ok"]


def test_policy_topics_in_the_schema_are_the_enum(registry: ToolRegistry) -> None:
    from emporio.data.policies import Topic

    spec = next(s for s in registry.specs() if s["name"] == "search_policy")
    items = spec["parameters"]["properties"]["topics"]["items"]
    assert items["enum"] == [t.value for t in Topic]
    assert spec["parameters"]["properties"]["topics"]["maxItems"] == 3


def _walk(node: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in _walk(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _walk(v, f"{path}[{i}]")]
    return [(path, node)]


@pytest.mark.parametrize(("tool", "args"), CALLS)
def test_outputs_are_json_with_formatted_money(
    registry: ToolRegistry, ctx: ToolContext, tool: str, args: dict[str, Any]
) -> None:
    result = registry.call(tool, args, ctx)
    assert result["ok"] is True
    json.dumps(result)  # sem Decimal, datas nem objetos
    for path, value in _walk(result):
        assert not isinstance(value, float), f"float em {path}"
        key = path.rsplit(".", 1)[-1].split("[")[0]
        if key.endswith("_brl") and value is not None:
            assert isinstance(value, str) and MONEY.match(value), f"{path} = {value!r}"
        if key.endswith("_percent") and value is not None:
            assert isinstance(value, str) and PERCENT.match(value), f"{path} = {value!r}"


def test_errors_are_data_with_code_message_and_hint(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    for name, args in [("nope", {}), ("get_product", {}), ("get_product", {"product_id": 9999})]:
        result = registry.call(name, args, ctx)
        assert result["ok"] is False
        assert set(result["error"]) == {"code", "message", "hint"}


def test_stock_is_only_disclosed_when_low(registry: ToolRegistry, ctx: ToolContext) -> None:
    items = registry.call("search_products", {"category": "violoes", "limit": 8}, ctx)["data"][
        "items"
    ]
    for item in items:
        product = ctx.repo.product(item["product_id"])
        assert product is not None
        assert ("stock_quantity" in item) == (item["availability"] == "low_stock")
        assert item.get("stock_quantity", 1) <= 3


def test_no_product_id_is_hardcoded_in_the_source() -> None:
    pattern = re.compile(r"\b(96|130|136)\b")
    offenders = [
        f"{path.relative_to(SRC)}:{number}"
        for path in SRC.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
