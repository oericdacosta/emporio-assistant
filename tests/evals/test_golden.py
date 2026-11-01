"""O golden é coerente com os dados e com as tools. Roda no CI, sem modelo."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from evals.cases import Case, CaseError, parse_case
from evals.facts import FactError, Facts

from emporio.tools.registry import ToolRegistry

GOLDEN = Path(__file__).resolve().parents[2] / "evals" / "golden.yaml"
MONEY_LITERAL = re.compile(r"[1-9]\d*(?:\\?\.\d{3})*,\d{2}")


def test_the_golden_has_the_expected_shape(cases: list[Case]) -> None:
    assert len(cases) == 30
    assert sum(len(c.turns) > 1 for c in cases) == 3  # os multi-turno
    assert all(c.turns and c.intent for c in cases)


def test_every_assumption_about_the_data_still_holds(cases: list[Case], facts: Facts) -> None:
    problems = [
        f"{c.id}: {why}" for c in cases for a in c.assume if (why := facts.check_assumption(a))
    ]
    assert problems == []


def test_expected_tools_and_arguments_exist(cases: list[Case], registry: ToolRegistry) -> None:
    schemas = {spec["name"]: spec["parameters"]["properties"] for spec in registry.specs()}
    for case in cases:
        hard = case.hard
        named = [*hard.tools_must, *hard.tools_any_of]
        for expected in named:
            assert expected.name in schemas, f"{case.id}: tool inexistente {expected.name}"
            unknown = set(expected.args) - set(schemas[expected.name])
            assert not unknown, f"{case.id}: {expected.name} não tem o argumento {unknown}"
        assert set(hard.tools_forbidden) <= set(schemas), case.id


def test_no_money_value_is_typed_by_hand() -> None:
    """Os valores em reais vêm de marcadores; um literal no golden envelheceria sozinho."""
    raw = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    offenders = []
    for item in raw:
        hard = item.get("hard", {})
        patterns: list[str] = [*hard.get("forbidden", [])]
        for key in ("contains", "contains_in_conversation"):
            for group in hard.get(key, []):
                patterns += [group] if isinstance(group, str) else list(group)
        for group in item.get("soft", {}).get("phrases", []):
            patterns += list(group)
        offenders += [f"{item['id']}: {p}" for p in patterns if MONEY_LITERAL.search(p)]
    assert offenders == []


def test_derived_values_match_the_known_figures(facts: Facts) -> None:
    def matches(pattern: str, text: str) -> bool:
        return re.fullmatch(facts.expand(pattern), text) is not None

    assert matches("{price:95}", "R$ 2.199,00") and matches("{price:95}", "R$2.199,00")
    assert matches("{pix:95}", "R$ 2.089,05")
    assert matches("{installment:95:12}", "R$ 183,25")
    assert matches("{price:121}", "R$ 439,20") and matches("{list_price:121}", "R$ 549,00")
    assert matches("{pix_over_promo:121}", "R$ 417,24") and matches(
        "{pix_of_list:121}", "R$ 521,55"
    )
    assert matches("{price_if_promo:95:12}", "R$ 1.935,12")
    assert facts.expand("{max_installments:120}") == "4"
    assert facts.expand("{matches:violoes:1000}") == "12"
    assert matches("{cheapest_pix:violoes:1000}", "R$ 408,41")
    assert facts.expand("{tracking:8}") == "BRJL5544332BR"
    assert (
        facts.expand("{date:8}") == "28/02" and facts.expand("{date_long:8}") == "28 de fevereiro"
    )


def test_unknown_facts_and_bad_arguments_are_errors(facts: Facts) -> None:
    with pytest.raises(FactError, match="desconhecido"):
        facts.expand("{nao_existe:1}")
    with pytest.raises(FactError, match="não existe"):
        facts.expand("{price:9999}")
    with pytest.raises(FactError, match="argumentos"):
        facts.expand("{price}")
    with pytest.raises(FactError, match="não parcela"):
        facts.expand("{installment:119:12}")


def test_regex_quantifiers_are_not_mistaken_for_facts(facts: Facts) -> None:
    assert facts.expand("BR[A-Z0-9]{9}BR") == "BR[A-Z0-9]{9}BR"


def test_a_stale_assumption_is_detected(facts: Facts) -> None:
    assert facts.check_assumption({"product": 96, "availability": "available"})
    assert facts.check_assumption({"product": 95, "promo_active": True})
    assert facts.check_assumption({"order": 8, "status": "pending"})
    assert facts.check_assumption({"product": 9999})
    assert facts.check_assumption({"product": 96, "availability": "out_of_stock"}) is None


@pytest.mark.parametrize(
    "raw",
    [
        {"id": "x", "intent": "i"},  # sem turnos
        {
            "id": "x",
            "intent": "i",
            "turns": ["oi"],
            "hard": {"contains": ["(aberto"]},
        },  # regex quebrada
    ],
)
def test_malformed_cases_are_rejected(raw: dict[str, Any], facts: Facts) -> None:
    with pytest.raises(CaseError):
        parse_case(raw, facts)
