"""Frete. Só Campo Grande tem valor; para as outras cidades é cotação, e o valor nunca é inventado.

O subtotal é a soma de (preço vigente depois da promoção x quantidade), antes do desconto do
PIX (veja ``pricing.subtotal``). O frete só é grátis acima de R$ 500,00: exatos R$ 500,00
ainda pagam R$ 35,00.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from emporio.domain.pricing import money
from emporio.domain.rules import RULES

FREE_SHIPPING_ABOVE: Final = RULES.free_shipping_above  # só acima disso é grátis
FLAT_FEE: Final = RULES.flat_fee
LARGE_ITEM_CATEGORY_NAMES: Final = frozenset(
    {"baterias e percussao"}
)  # nome da categoria, sem acento
CARRIERS: Final = RULES.carriers


class Zone(StrEnum):
    CAMPO_GRANDE = "campo_grande"
    OTHER = "other"  # qualquer cidade que não seja Campo Grande


@dataclass(frozen=True, slots=True)
class ShippingQuote:
    zone: Zone
    fee: Decimal | None  # None = valor desconhecido (cotação por CEP)
    delivery_time: str
    carriers: tuple[str, ...] = ()
    needs_quote: bool = False  # item de grande porte fora de Campo Grande
    note: str = ""


def normalize_text(city: str) -> str:
    """Sem acento, caixa e espaços extras: ``'  CAMPO   Grande '`` -> ``'campo grande'``."""
    folded = "".join(
        c for c in unicodedata.normalize("NFD", city.casefold()) if unicodedata.category(c) != "Mn"
    )
    return " ".join(folded.split())


def is_large_item(category_name: str) -> bool:
    """Grande porte (cotação fora de Campo Grande), decidido pelo NOME da categoria do CSV."""
    return normalize_text(category_name) in LARGE_ITEM_CATEGORY_NAMES


def quote_shipping(
    city: str, subtotal: Decimal | str | int, *, large_item: bool = False
) -> ShippingQuote:
    """Frete para ``subtotal`` (soma dos preços vigentes) entregue em ``city``."""
    key = normalize_text(city)
    if not key:
        raise ValueError("cidade é obrigatória")
    if key == normalize_text(RULES.local_city):
        fee = Decimal(0) if money(subtotal) > FREE_SHIPPING_ABOVE else FLAT_FEE
        low, high = RULES.local_delivery_business_days
        return ShippingQuote(Zone.CAMPO_GRANDE, fee, f"{low} a {high} dias úteis, motoboy próprio")
    return ShippingQuote(
        Zone.OTHER,
        None,
        "depende da modalidade",
        CARRIERS,
        needs_quote=large_item,
        note="Valor calculado por CEP, peso e dimensões; a loja não tem tabela de valores.",
    )
