"""Fatos dos dados que os casos de referência usam, calculados dos CSVs na hora de carregar.

Nenhum valor é digitado à mão no golden: o caso escreve ``{price:95}`` e este módulo troca pelo
padrão que casa com o preço vigente do produto 95. Se os dados mudarem, o golden acompanha; e
quando um caso depende de uma situação dos dados (produto esgotado, promoção ativa), ele a declara
em ``assume`` e um teste confere.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Final

from emporio.data.repository import CsvRepository
from emporio.data.search import rank_products
from emporio.domain import store
from emporio.domain.availability import product_availability
from emporio.domain.pricing import (
    PIX_DISCOUNT,
    brl,
    installment_options,
    max_installments,
    money,
    price_for,
    quote,
)
from emporio.domain.shipping import FLAT_FEE

_PLACEHOLDER: Final = re.compile(r"\{([a-z_]+)((?::[\w.]+)*)\}")
_MONTHS: Final = [
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
]


class FactError(ValueError):
    """O golden pede um fato que os dados não têm."""


def _money_pattern(value: Decimal) -> str:
    """Casa com ``R$ 2.199,00`` com ou sem espaço depois do cifrão."""
    return re.escape(brl(value)).replace(r"\ ", r"\s?", 1)


class Facts:
    def __init__(self, repo: CsvRepository) -> None:
        self._repo = repo
        self._resolvers: dict[str, Callable[..., str]] = {
            "price": lambda pid: _money_pattern(self._price(pid).price),
            "list_price": lambda pid: _money_pattern(self._price(pid).list_price),
            "pix": lambda pid: _money_pattern(self._price(pid).pix_price),
            "pix_of_list": lambda pid: _money_pattern(
                price_for(self._price(pid).list_price).pix_price
            ),
            "pix_over_promo": lambda pid: _money_pattern(
                money(self._price(pid).price * (1 - PIX_DISCOUNT))
            ),
            "price_if_promo": lambda pid, pct: _money_pattern(
                price_for(self._price(pid).list_price, int(pct)).price
            ),
            "installment": lambda pid, n: _money_pattern(self._installment(pid, int(n))),
            "max_installments": lambda pid: str(max_installments(self._price(pid).price)),
            "matches": lambda category, ceiling: str(len(self._listing(category, ceiling))),
            "cheapest_pix": lambda category, ceiling: _money_pattern(
                self._cheapest(category, ceiling).pix_price
            ),
            "tracking": lambda oid: re.escape(self._order(oid).tracking_code or ""),
            "date": lambda oid: self._order_date(oid)[0],
            "date_long": lambda oid: self._order_date(oid)[1],
            "flat_fee": lambda: _money_pattern(FLAT_FEE),
            "whatsapp": lambda: re.escape(store.WHATSAPP),
            "address": lambda: re.escape("Rua 14 de Maio, 3200").replace(",", ",?"),
        }

    # --- consultas -----------------------------------------------------------------------------

    def _price(self, product_id: str) -> Any:
        product = self._repo.product(int(product_id))
        if product is None:
            raise FactError(f"produto {product_id} não existe")
        return quote(product, self._repo.promotions_for(product.id))

    def _installment(self, product_id: str, n: int) -> Decimal:
        options = {o.n: o for o in installment_options(self._price(product_id).price)}
        if n not in options:
            raise FactError(f"o produto {product_id} não parcela em {n}x")
        return options[n].installment

    def _listing(self, category: str, ceiling: str) -> list[Any]:
        found = rank_products(
            self._repo, category=category, max_price=Decimal(ceiling), sort="price_asc"
        )
        if not found:
            raise FactError(f"nenhum produto em {category} até {ceiling}")
        return [hit.product for hit in found]

    def _cheapest(self, category: str, ceiling: str) -> Any:
        cheapest = self._listing(category, ceiling)[0]
        return quote(cheapest, self._repo.promotions_for(cheapest.id))

    def _order(self, order_id: str) -> Any:
        order = self._repo.order(int(order_id))
        if order is None:
            raise FactError(f"pedido {order_id} não existe")
        return order

    def _order_date(self, order_id: str) -> tuple[str, str]:
        delivery = self._order(order_id).estimated_delivery
        if delivery is None:
            raise FactError(f"o pedido {order_id} não tem previsão de entrega")
        return f"{delivery:%d/%m}", f"{delivery.day} de {_MONTHS[delivery.month - 1]}"

    # --- uso -----------------------------------------------------------------------------------

    def expand(self, pattern: str) -> str:
        """Troca cada ``{fato:arg}`` pelo trecho de regex que o representa."""

        def replace(match: re.Match[str]) -> str:
            name, raw = match[1], match[2]
            resolver = self._resolvers.get(name)
            if resolver is None:
                raise FactError(f"fato desconhecido: {{{name}}}")
            args = [a for a in raw.split(":") if a]
            try:
                return str(resolver(*args))
            except TypeError as error:
                raise FactError(f"argumentos errados em {{{name}{raw}}}") from error

        return _PLACEHOLDER.sub(replace, pattern)

    def check_assumption(self, assumption: Mapping[str, Any]) -> str | None:
        """Devolve o motivo se os dados não são mais como o caso supõe; ``None`` se estão."""
        if "product" in assumption:
            product = self._repo.product(int(assumption["product"]))
            if product is None:
                return f"produto {assumption['product']} não existe"
            if "availability" in assumption:
                actual = product_availability(product).value
                if actual != assumption["availability"]:
                    return (
                        f"produto {product.id}: esperado {assumption['availability']}, é {actual}"
                    )
            if "promo_active" in assumption:
                active = self._price(str(product.id)).promo_percent is not None
                if active != assumption["promo_active"]:
                    return f"produto {product.id}: promoção ativa é {active}"
            if "has_inactive_promo" in assumption:
                inactive = any(not p.is_active for p in self._repo.promotions_for(product.id))
                if inactive != assumption["has_inactive_promo"]:
                    return f"produto {product.id}: promoção inativa é {inactive}"
        if "order" in assumption:
            order = self._repo.order(int(assumption["order"]))
            if order is None:
                return f"pedido {assumption['order']} não existe"
            if "status" in assumption and order.status.value != assumption["status"]:
                return f"pedido {order.id}: esperado {assumption['status']}, é {order.status.value}"
        return None
