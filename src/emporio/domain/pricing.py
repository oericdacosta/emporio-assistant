"""Preço vigente, desconto PIX e parcelamento. Tudo em ``Decimal`` com arredondamento HALF_UP.

Como as regras funcionam:
* O PIX dá 5% sobre o preço de tabela e não acumula com promoção.
* Uma promoção só vale se estiver ativa e o produto também estiver ativo (o CSV não traz
  datas de vigência). Com mais de uma ativa no mesmo produto, vale a maior, sem somar.
* No parcelamento, a regra detalhada do manual vale mais que a tabela resumida. O piso de
  cada faixa (R$ 50, R$ 80 ou R$ 100) vale para o valor da parcela, não para o total, e é
  comparado sem arredondar. A parcela exibida é arredondada HALF_UP e a última absorve a
  diferença de centavos.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from emporio.domain.models import Product, ProductStatus, Promotion

CENT: Final = Decimal("0.01")
PIX_DISCOUNT: Final = Decimal("0.05")
MAX_INSTALLMENTS: Final = 12

# (até N parcelas, valor mínimo da parcela). 1x é sempre permitido.
INSTALLMENT_TIERS: Final[tuple[tuple[int, Decimal], ...]] = (
    (3, Decimal("50")),
    (6, Decimal("80")),
    (12, Decimal("100")),
)


def money(value: Decimal | str | int) -> Decimal:
    """Normaliza para centavos (HALF_UP). ``float`` é rejeitado de propósito."""
    if isinstance(value, bool) or not isinstance(value, Decimal | str | int):
        raise TypeError(f"valor monetário deve ser Decimal, str ou int, não {type(value).__name__}")
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


def brl(value: Decimal | str | int) -> str:
    """``Decimal('2089.05')`` -> ``'R$ 2.089,05'``."""
    text = f"{money(value):,.2f}"
    return "R$ " + text.replace(",", "\0").replace(".", ",").replace("\0", ".")


@dataclass(frozen=True, slots=True)
class Price:
    list_price: Decimal  # preço de tabela
    promo_percent: int | None  # None = sem promoção vigente
    price: Decimal  # o que se paga no cartão/boleto/débito
    pix_price: Decimal  # o que se paga no PIX (== price quando há promoção)

    @property
    def pix_discount_applied(self) -> bool:
        return self.promo_percent is None


def best_promo_percent(product: Product, promotions: Iterable[Promotion]) -> int | None:
    """Maior promoção vigente do produto (não cumulativas); ``None`` se não houver."""
    if product.status is not ProductStatus.ACTIVE:
        return None
    return max(
        (p.discount_percent for p in promotions if p.is_active and p.product_id == product.id),
        default=None,
    )


def price_for(list_price: Decimal | str | int, promo_percent: int | None = None) -> Price:
    table = money(list_price)
    if promo_percent is None:
        return Price(table, None, table, money(table * (1 - PIX_DISCOUNT)))
    if not 0 < promo_percent < 100:
        raise ValueError("percentual de promoção deve estar em (0, 100)")
    current = money(table * (100 - promo_percent) / 100)
    return Price(table, promo_percent, current, current)


def quote(product: Product, promotions: Iterable[Promotion] = ()) -> Price:
    return price_for(product.price, best_promo_percent(product, promotions))


def subtotal(lines: Iterable[tuple[Price, int]]) -> Decimal:
    """Subtotal do carrinho = preço vigente (pós-promoção, ANTES do PIX) x quantidade."""
    return money(sum((price.price * qty for price, qty in lines), Decimal(0)))


def min_installment(n: int) -> Decimal:
    """Parcela mínima exigida para ``n`` parcelas (1x não tem mínimo)."""
    if not 1 <= n <= MAX_INSTALLMENTS:
        raise ValueError(f"parcelas devem estar entre 1 e {MAX_INSTALLMENTS}")
    if n == 1:
        return Decimal(0)
    return next(minimum for upto, minimum in INSTALLMENT_TIERS if n <= upto)


def installment_allowed(total: Decimal | str | int, n: int) -> bool:
    # total/n >= mínimo  <=>  total >= n*mínimo: comparação exata, sem arredondar a parcela
    return money(total) >= n * min_installment(n)


@dataclass(frozen=True, slots=True)
class InstallmentOption:
    n: int
    installment: Decimal  # parcelas 1..n-1 (HALF_UP)
    last_installment: Decimal  # absorve a diferença de centavos
    total: Decimal  # sem juros: sempre igual ao total


def installment_options(total: Decimal | str | int) -> list[InstallmentOption]:
    """Planos permitidos de 1x até o máximo; sempre contém ao menos o 1x."""
    amount = money(total)
    if amount <= 0:
        raise ValueError("total deve ser positivo")
    options: list[InstallmentOption] = []
    for n in range(1, MAX_INSTALLMENTS + 1):
        if not installment_allowed(amount, n):
            break  # mínimo cresce e parcela cai com n: não há plano maior válido
        base = money(amount / n)
        options.append(InstallmentOption(n, base, amount - base * (n - 1), amount))
    return options


def max_installments(total: Decimal | str | int) -> int:
    return installment_options(total)[-1].n
