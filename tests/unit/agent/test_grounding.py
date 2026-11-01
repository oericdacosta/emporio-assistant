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


# --- estado do sistema, vazamento do prompt e moedas ------------------------------------------

TICKET = {"ok": True, "data": {"ticket_id": "EMP-0A1B2C3D", "sla": "24 horas úteis"}}
DENIED = {
    "ok": False,
    "error": {"code": "not_verified", "message": "Não consegui confirmar esses dados."},
}


def test_complaint_protocol_must_come_from_a_tool() -> None:
    assert Ledger().violations("Seu protocolo é EMP-0A1B2C3D.") == ["EMP-0A1B2C3D"]
    ledger = Ledger()
    ledger.absorb(TICKET, "register_complaint")
    assert ledger.violations("Seu protocolo é EMP-0A1B2C3D.") == []
    assert "não use protocolo" in correction_message(["EMP-0A1B2C3D"]).lower()


@pytest.mark.parametrize(
    "phrase",
    [
        "Não consegui confirmar esses dados.",
        "Ainda não consegui confirmar o seu pedido.",
        "Não foi possível verificar o pedido.",
        "Não foi possível confirmar seu cadastro.",
        "Não consegui confirmar o pedido 4 com o e-mail lucas.mendes@jmail.com.",  # O11
        "Ainda não consegui localizar o pedido, pode conferir os dados?",  # O11
        "Não consegui verificar seus dados.",
        "Pode me informar o número completo do pedido? Só com o número não consigo confirmar.",  # O03
        "Só com o número eu não consigo confirmar.",  # G31
        "Pode me informar o número completo do pedido? Só com o número eu não consigo confirmar.",
        "Só com o número ainda não consigo confirmar.",
    ],
)
def test_verification_failure_phrases_need_a_failed_verify_in_the_turn(phrase: str) -> None:
    assert Ledger().violations(phrase) != []
    other = Ledger()
    other.absorb(DENIED, "get_order_status")  # negou, mas o modelo não tentou verificar
    assert other.violations(phrase) != []
    ledger = Ledger()
    ledger.absorb(DENIED, "verify_customer")
    assert ledger.violations(phrase) == []
    message = correction_message(Ledger().violations(phrase))
    assert "chame verify_customer" in message and "sem tentar" in message


def test_absorb_keeps_the_old_signature() -> None:
    ledger = Ledger()
    ledger.absorb(DENIED)
    assert ledger.verify_failed is False


def test_prompt_example_names_are_a_violation_unless_the_customer_said_them() -> None:
    import re
    from pathlib import Path

    from emporio.agent.grounding import LEAKED_EXAMPLES

    prompt = (Path(__file__).resolve().parents[3] / "src/emporio/prompts/system.md").read_text()
    examples = re.findall(r"<exemplo .*?</exemplo>", prompt, re.DOTALL)
    names = set(re.findall(r"Violão [A-ZÀ-Ú]\w+(?: [A-ZÀ-Ú]\w+)* Exemplo", "".join(examples)))
    emails = set(re.findall(r"\b\w+@exemplo\.com\b", "".join(examples)))
    assert names and emails
    for found in names | emails:
        assert found in LEAKED_EXAMPLES, f"{found} saiu do system.md e não está na denylist"
        assert Ledger().violations(f"Claro! {found} sai por bom preço.") != []
    assert (
        Ledger.for_turn("quero o Violão Modelo Exemplo").violations(
            "O Violão Modelo Exemplo é esse?"
        )
        == []
    )


@pytest.mark.parametrize(
    "text", ["US$ 399,82", "custa 399 dólares", "USD 10", "€ 5,50", "10 euros", "12 EUR"]
)
def test_foreign_currencies_are_violations_unless_grounded(text: str) -> None:
    assert Ledger().violations(text) == [text.replace("custa ", "")]
    ledger = Ledger.for_turn(text)
    assert ledger.violations(text) == []
    tool = Ledger()
    tool.absorb({"note": text})
    assert tool.violations(text) == []
    assert "reais" in correction_message([text.replace("custa ", "")])


def test_dollar_word_without_a_number_is_fine() -> None:
    assert Ledger().violations("Não trabalhamos com dólar nem euro.") == []


def test_stock_uncertainty_is_not_an_identity_failure() -> None:
    for phrase in (
        "Ainda não consegui confirmar o estoque, vou checar com a equipe.",
        "Não foi possível verificar a disponibilidade agora.",
    ):
        assert Ledger().violations(phrase) == []


def test_working_state_lines_are_trusted_by_the_ledger() -> None:
    assert Ledger.for_turn("e o GD20?", []).violations("O GD20 sai por R$ 2.199,00.") != []
    ledger = Ledger.for_turn(
        "e o GD20?", [], ["Produtos já consultados: Takamine GD20 (id 95) R$ 2.199,00"]
    )
    assert ledger.violations("O Takamine GD20 sai por R$ 2.199,00.") == []
    assert ledger.violations("O Takamine GD20 sai por R$ 1.999,00.") != []


def test_asking_for_the_order_number_the_customer_already_gave_is_a_violation() -> None:
    phrase = "Ainda falta o número do pedido para eu confirmar, por favor."  # G32
    assert Ledger().violations(phrase) == []  # cliente ainda não citou pedido
    ledger = Ledger()
    ledger.order_known = True
    bad = ledger.violations(phrase)
    assert bad != [] and "não peça o número de novo" in correction_message(bad)
    assert ledger.violations("Ainda falta o número do pedido para eu verificar.") != []
