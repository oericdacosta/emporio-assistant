from __future__ import annotations

from decimal import Decimal

import pytest

from emporio.agent.grounding import (
    FALLBACK_MESSAGE,
    Ledger,
    contract_violations,
    correction_message,
    parse_user_amounts,
)

# Resultado realista de `get_product` e `calculate_payment_options`: dinheiro em "R$ x,xx".
GD20 = {
    "ok": True,
    "data": {
        "name": "Takamine GD20",
        "list_price_brl": "R$ 2.199,00",
        "pix_price_brl": "R$ 2.089,05",
        "promo_percent": None,
        "pix_discount": "5%",
        "plans": [{"plan": "12x de R$ 183,25", "total_brl": "R$ 2.199,00"}],
        "tracking_code": "BRAB1234567BR",
    },
}


def ledger_for(user_text: str = "quanto custa o gd20?") -> Ledger:
    ledger = Ledger.for_turn(user_text)
    ledger.absorb(GD20)
    return ledger


def test_formatted_tool_strings_are_absorbed() -> None:
    # regressão: ler só números deixava "R$ 2.199,00" fora do ledger e barrava todo preço correto
    ok = "O GD20 sai por R$ 2.199,00, ou R$2.089,05 no PIX (5% off). 12x de R$ 183,25. Cód. BRAB1234567BR."
    assert ledger_for().violations(ok) == []


def test_amount_without_thousands_separator_is_not_truncated() -> None:
    ledger = Ledger()
    ledger.absorb({"price_brl": "R$ 2199,00"})
    assert ledger.amounts == {Decimal("2199.00")}


@pytest.mark.parametrize(
    "answer",
    [
        "Sai por R$ 1.935,12",
        "só R$ 2.000",
        "uns R$ 2199,01",
        "custa 2.199,50 reais",
        "18% de desconto",
        "em 10x",
        "BRXX9999999BR",
    ],
)
def test_invented_numbers_are_blocked(answer: str) -> None:
    assert ledger_for().violations(answer) != []


@pytest.mark.parametrize(
    "answer",
    ["R$ 2199", "R$2.199,00", "R$ 2.199,0", "2.199 reais", "2199,00 reais", "5%", "12x", "12 x"],
)
def test_equivalent_spellings_of_grounded_values_pass(answer: str) -> None:
    assert ledger_for().violations(answer) == []


# ---- valores citados pelo usuário -----------------------------------------------------------
@pytest.mark.parametrize(
    ("user", "answer"),
    [
        ("quais violões até R$1000?", "Violões até R$ 1.000,00 que temos:"),
        ("quais violões até R$1000?", "Violões até R$ 1.000 que temos:"),
        ("violão até 1k", "Dentro do seu orçamento de R$ 1.000:"),
        ("tem violão de uns 2 mil?", "Na faixa de 2.000 reais temos:"),
        ("entre 1 e 2 mil", "Entre R$ 1.000 e R$ 2.000 tenho estes:"),
        ("quero gastar 1,5 mil", "Com R$ 1.500,00 dá pra:"),
        ("até quinhentos? 500 reais", "até 500 reais"),
        ("até R$1000", "Dentro do teto de R$ 1.000,00 tenho estes:"),
    ],
)
def test_user_quoted_budgets_are_not_hallucinations(user: str, answer: str) -> None:
    assert Ledger.for_turn(user).violations(answer) == []


@pytest.mark.parametrize(
    ("user", "answer"),
    [
        ("até R$1000", "Tenho um por R$ 999,00"),  # o preço do produto precisa vir de tool
        ("no concorrente sai 1800", "A gente faz por R$ 1.700,00"),  # só o 1800 foi dito
        ("uns 2 mil", "Por R$ 2.100,00"),
    ],
)
def test_user_budget_does_not_whitelist_other_values(user: str, answer: str) -> None:
    assert Ledger.for_turn(user).violations(answer) == [answer[answer.index("R$") :]]


def test_competitor_price_quoted_by_user_can_be_repeated() -> None:
    ledger = Ledger.for_turn("na loja X o GD20 está R$ 1.999,90")
    assert ledger.violations("Entendi, R$ 1.999,90 lá.") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("quais violões até R$1000?", {"1000"}),
        ("até 1k", {"1000"}),
        ("2.5k", {"2500"}),
        ("uns 2 mil", {"2000"}),
        ("entre 1 e 2 mil", {"1000", "2000"}),
        ("de 500 a 1000", {"500", "1000"}),
        ("R$ 1.000,00", {"1000"}),
        ("1,5 mil", {"1500"}),
        ("dois mil reais", {"2000"}),
        ("mil reais", {"1000"}),
        ("500 reais", {"500"}),
        ("R$ 50", {"50"}),
        ("quero em 12x, tenho 3 filhos e 50 anos", set()),  # número pequeno e solto não é preço
    ],
)
def test_parse_user_amounts(text: str, expected: set[str]) -> None:
    assert parse_user_amounts(text) == {Decimal(e) for e in expected}


# ---- percentuais, parcelas, rastreio --------------------------------------------------------
def test_percent_and_installments_need_a_source() -> None:
    ledger = Ledger()
    ledger.absorb({"promo_percent": "18%", "plans": ["3x de R$ 53,30"]})
    assert ledger.violations("18% off em 3x") == []
    assert ledger.violations("20% off em 6x") == ["20%", "6x"]
    assert ledger.violations("18,0%") == []  # mesma grandeza


def test_raw_numbers_in_tool_output_are_invisible_which_is_why_the_contract_exists() -> None:
    ledger = Ledger()
    ledger.absorb({"price_brl": 35, "promo_percent": 18})
    assert ledger.violations("R$ 35,00 e 18%") != []


def test_amounts_inside_policy_text_are_allowed() -> None:
    ledger = Ledger()
    ledger.absorb(
        {
            "sections": [
                "Frete grátis acima de R$ 500,00; abaixo, taxa fixa de R$ 35,00. Reembolso em 10 dias úteis."
            ]
        }
    )
    assert ledger.violations("Acima de R$ 500 o frete é grátis; senão R$ 35.") == []


def test_tracking_codes_must_come_from_tools_ignoring_case() -> None:
    ledger = ledger_for()
    assert ledger.violations("brab1234567br") == []
    assert ledger.violations("Rastreio: BRXX9999999BR") == ["BRXX9999999BR"]


def test_previous_approved_answers_feed_followups() -> None:
    ledger = Ledger.for_turn("e em 3x?", approved_answers=["Frete: R$ 35,00. Em 12x de R$ 183,25."])
    assert ledger.violations("Lembrando: R$ 35,00 de frete; 12x de R$ 183,25.") == []


def test_nothing_is_inherited_without_approval() -> None:
    assert Ledger.for_turn("oi").violations("R$ 2.199,00") != []


# ---- contrato das tools ---------------------------------------------------------------------
def test_contract_accepts_formatted_money_and_percent() -> None:
    assert contract_violations(GD20) == []


def test_contract_flags_raw_numbers_and_bad_format() -> None:
    bad = {
        "a": {"price_brl": 35.0, "ok_brl": "R$ 1,00", "promo_percent": 18},
        "b": [{"total_brl": "35,00"}],
    }
    assert contract_violations(bad) == ["$.a.price_brl", "$.a.promo_percent", "$.b[0].total_brl"]
    assert contract_violations({"installments_brl": ["R$ 1,00", 2]}) == ["$.installments_brl"]


def test_messages() -> None:
    assert correction_message(["R$ 1,00", "R$ 1,00"]).count("R$ 1,00") == 1
    assert "(67) 3321-4500" in FALLBACK_MESSAGE
