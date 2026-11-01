"""Regras numéricas do manual de políticas, lidas de ``data/policy_rules.json``.

O manual é a fonte; o JSON é a cópia estruturada que o ``domain`` usa, para que prazo, desconto,
faixa de parcela, frete e horário não fiquem digitados no código. O JSON guarda o ``sha256`` do
``policies.md`` de onde saiu: se o manual mudar, ``verify_rules`` (e o teste de drift) falham até
alguém revisar os valores e atualizar o hash. A lógica (arredondamento, âncoras de prazo,
precedência) continua em código.
"""

from __future__ import annotations

import hashlib
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from emporio.errors import ConfigError
from emporio.paths import default_data_dir

RULES_FILE: Final = "policy_rules.json"
POLICIES_FILE: Final = "policies.md"

PositiveInt = Annotated[int, Field(gt=0)]
PositiveMoney = Annotated[Decimal, Field(gt=0)]
IntRange = Annotated[tuple[PositiveInt, PositiveInt], Field()]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InstallmentTier(_Frozen):
    up_to: PositiveInt  # vale até N parcelas
    min_installment: PositiveMoney  # valor mínimo de cada parcela


class OpeningWindow(_Frozen):
    weekdays: tuple[Annotated[int, Field(ge=0, le=6)], ...] = Field(min_length=1)  # 0 = segunda
    open: time
    close: time

    @model_validator(mode="after")
    def _opens_before_closing(self) -> Self:
        if self.open >= self.close:
            raise ValueError("a loja deve abrir antes de fechar")
        return self


class PolicyRules(_Frozen):
    schema_version: int
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # do policies.md de onde saiu
    regret_days: PositiveInt  # arrependimento e preferência, a partir do recebimento
    defect_exchange_days: PositiveInt  # defeito: troca, a partir da compra
    legal_warranty_days: PositiveInt  # garantia legal, a partir do recebimento
    refund_business_days: PositiveInt
    manufacturer_warranty_months: IntRange  # (mínimo, máximo)
    pix_discount_percent: Annotated[int, Field(gt=0, lt=100)]
    max_installments: PositiveInt
    installment_tiers: tuple[InstallmentTier, ...] = Field(min_length=1)
    combined_payment_above: PositiveMoney
    free_shipping_above: PositiveMoney
    flat_fee: PositiveMoney
    local_city: str = Field(min_length=1)
    local_delivery_business_days: IntRange  # (mínimo, máximo)
    carriers: tuple[str, ...]
    hours: tuple[OpeningWindow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        ups = [t.up_to for t in self.installment_tiers]
        mins = [t.min_installment for t in self.installment_tiers]
        if ups != sorted(set(ups)) or mins != sorted(mins) or ups[-1] != self.max_installments:
            raise ValueError("faixas de parcela devem crescer e a última deve ser max_installments")
        days = [d for window in self.hours for d in window.weekdays]
        if len(days) != len(set(days)):
            raise ValueError("um dia da semana aparece em mais de uma faixa de horário")
        for low, high in (self.manufacturer_warranty_months, self.local_delivery_business_days):
            if low > high:
                raise ValueError("intervalo com mínimo maior que o máximo")
        return self


def policies_sha256(directory: Path) -> str:
    return hashlib.sha256((directory / POLICIES_FILE).read_bytes()).hexdigest()


def load_rules(directory: Path | None = None) -> PolicyRules:
    """Lê ``policy_rules.json``, que fica ao lado da pasta ``raw`` (como o ``policies.md``)."""
    directory = directory or default_data_dir().parent
    try:
        return PolicyRules.model_validate_json((directory / RULES_FILE).read_bytes())
    except (OSError, ValidationError) as exc:
        raise ConfigError(f"regras das políticas ilegíveis em {directory}: {exc}") from exc


def verify_rules(directory: Path | None = None) -> PolicyRules:
    """Carrega as regras e confere que ainda correspondem ao manual (hash do ``policies.md``)."""
    directory = directory or default_data_dir().parent
    rules = load_rules(directory)
    try:
        actual = policies_sha256(directory)
    except OSError as exc:
        raise ConfigError(f"manual ilegível em {directory}: {exc}") from exc
    if actual != rules.source_sha256:
        raise ConfigError(
            f"o manual ({POLICIES_FILE}) mudou desde a última revisão de {RULES_FILE}: "
            f"confira os valores contra o manual e atualize source_sha256 para {actual}"
        )
    return rules


RULES: Final = load_rules()
