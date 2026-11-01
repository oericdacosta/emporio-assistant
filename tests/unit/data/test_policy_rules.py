"""Os números do código vêm de ``policy_rules.json``; estes testes pegam o JSON desatualizado."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from emporio.data.policies import PolicyStore, Topic
from emporio.domain.rules import POLICIES_FILE, RULES_FILE, PolicyRules, load_rules, verify_rules
from emporio.errors import ConfigError

DATA = Path(__file__).resolve().parents[3] / "data"
RULES = load_rules(DATA)
STORE = PolicyStore.load(DATA)


def _section(topic: Topic) -> str:
    return " ".join(section.text for section in STORE.get([topic]))


def _brl(value: object) -> str:
    whole, _, cents = f"{value:.2f}".partition(".")
    return f"R$ {int(whole):,}".replace(",", ".") + f",{cents}"


def test_rules_match_the_current_manual_hash() -> None:
    verify_rules(DATA)  # falha se o policies.md mudou sem revisar o policy_rules.json


def test_a_changed_manual_is_reported(tmp_path: Path) -> None:
    for name in (RULES_FILE, POLICIES_FILE):
        shutil.copy(DATA / name, tmp_path / name)
    (tmp_path / POLICIES_FILE).write_text("outro texto", encoding="utf-8")
    with pytest.raises(ConfigError, match="mudou desde a última revisão"):
        verify_rules(tmp_path)


def test_missing_or_invalid_rules_are_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="ilegíveis"):
        load_rules(tmp_path)
    (tmp_path / RULES_FILE).write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(ConfigError, match="ilegíveis"):
        load_rules(tmp_path)


@pytest.mark.parametrize(
    ("section", "pattern"),
    [
        (Topic.TROCAS, rf"{RULES.regret_days} \(\w+\) dias corridos após o recebimento"),
        (Topic.TROCAS, rf"preferência.*em até {RULES.regret_days} dias"),
        (Topic.TROCAS, rf"{RULES.defect_exchange_days} \(\w+\) dias corridos após a compra"),
        (Topic.TROCAS, rf"em até {RULES.refund_business_days} dias úteis"),
        (Topic.GARANTIA, rf"{RULES.legal_warranty_days} \(\w+\) dias"),
        (Topic.GARANTIA, r"de 6 meses a 2 anos"),
        (Topic.PAGAMENTO, rf"{RULES.pix_discount_percent}% de desconto"),
        (Topic.PAGAMENTO, rf"Até {RULES.max_installments}x sem juros"),
        (Topic.PAGAMENTO, rf"acima de {re.escape(_brl(RULES.combined_payment_above))}"),
        (Topic.FRETE, rf"acima de {re.escape(_brl(RULES.free_shipping_above))}"),
        (Topic.FRETE, rf"taxa fixa de {re.escape(_brl(RULES.flat_fee))}"),
        (
            Topic.FRETE,
            rf"{RULES.local_delivery_business_days[0]} a {RULES.local_delivery_business_days[1]} dias úteis",
        ),
        (Topic.EMPRESA, RULES.local_city),
    ],
)
def test_every_number_appears_in_its_manual_section(section: Topic, pattern: str) -> None:
    assert re.search(pattern, _section(section)), f"{pattern!r} não aparece em {section.value}"


def test_installment_tiers_match_the_detailed_rules() -> None:
    text = _section(Topic.PAGAMENTO)
    previous = 0
    for tier in RULES.installment_tiers:
        low = previous + 1
        label = rf"até {tier.up_to}x" if low == 1 else rf"de {low}x a {tier.up_to}x"
        assert re.search(
            rf"{label}: sem juros.*{re.escape(_brl(tier.min_installment))}", text, re.I
        )
        previous = tier.up_to


def test_carriers_match_the_shipping_table() -> None:
    text = _section(Topic.FRETE)
    for carrier in RULES.carriers:
        name, days = carrier.rsplit(" ", 3)[0], carrier.rsplit(" ", 3)[1]
        low, high = days.split("-")
        assert f"{low} a {high} dias úteis" in text, carrier
        assert name.split()[0] in text, carrier


def test_hours_match_the_manual_table() -> None:
    text = _section(Topic.HORARIO)
    for window in RULES.hours:
        assert f"{window.open:%H:%M} às {window.close:%H:%M}" in text


def test_incoherent_rules_are_rejected() -> None:
    data = RULES.model_dump(mode="json")
    data["installment_tiers"] = list(reversed(data["installment_tiers"]))
    with pytest.raises(ValueError, match="faixas de parcela"):
        PolicyRules.model_validate(data)
    data = RULES.model_dump(mode="json")
    data["hours"][0]["close"] = "08:00"
    with pytest.raises(ValueError, match="abrir antes de fechar"):
        PolicyRules.model_validate(data)
