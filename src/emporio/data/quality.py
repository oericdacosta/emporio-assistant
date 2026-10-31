"""Relatório de qualidade dos dados, montado na carga.

O relatório só aponta as inconsistências; ele não corrige nada. O agente trata o total de um
pedido, por exemplo, como "total registrado", mesmo quando ele não bate com a soma dos itens.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from emporio.domain.models import Category, Customer, Order, Product, ProductStatus
from emporio.domain.pricing import installment_allowed, money


class Rejection(NamedTuple):
    """Linha de CSV que não pôde ser carregada, e o motivo."""

    file: str
    line: int
    reason: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    status_stock_conflicts: tuple[int, ...]  # ids de produto: status e estoque não combinam
    order_total_mismatches: tuple[int, ...]  # ids de pedido: total diferente da soma dos itens
    installment_violations: tuple[int, ...]  # ids de pedido: parcela abaixo do piso permitido
    duplicate_email_customers: tuple[tuple[int, ...], ...]  # grupos de clientes com o mesmo e-mail
    empty_categories: tuple[int, ...]  # ids de categoria sem nenhum produto
    rejected: tuple[Rejection, ...]  # linhas que não foram carregadas

    @property
    def is_clean(self) -> bool:
        return not any(
            (
                self.status_stock_conflicts,
                self.order_total_mismatches,
                self.installment_violations,
                self.duplicate_email_customers,
                self.empty_categories,
                self.rejected,
            )
        )

    def summary(self) -> str:
        """Uma linha para o log de inicialização (só contagens, sem dados pessoais)."""
        return (
            f"{len(self.status_stock_conflicts)} produtos com status e estoque incoerentes, "
            f"{len(self.order_total_mismatches)} pedidos com total diferente dos itens, "
            f"{len(self.installment_violations)} pedidos com parcela abaixo do piso, "
            f"{len(self.duplicate_email_customers)} e-mails repetidos, "
            f"{len(self.empty_categories)} categorias vazias, "
            f"{len(self.rejected)} linhas rejeitadas"
        )


def _status_conflict(product: Product) -> bool:
    if product.status is ProductStatus.ACTIVE:
        return product.stock == 0
    return product.stock > 0


def _total_mismatch(order: Order, products: dict[int, Product]) -> bool:
    if not order.items or any(item.product_id not in products for item in order.items):
        return False
    expected = sum(
        (products[item.product_id].price * item.quantity for item in order.items),
        start=money(0),
    )
    return money(order.total) != money(expected)


def build_report(
    *,
    categories: Iterable[Category],
    products: Iterable[Product],
    customers: Iterable[Customer],
    orders: Iterable[Order],
    rejected: Iterable[Rejection],
) -> QualityReport:
    product_list = list(products)
    by_id = {p.id: p for p in product_list}
    order_list = list(orders)

    emails: dict[str, list[int]] = defaultdict(list)
    for customer in customers:
        if customer.email:
            emails[customer.email.strip().casefold()].append(customer.id)

    used_categories = {p.category_id for p in product_list}
    return QualityReport(
        status_stock_conflicts=tuple(sorted(p.id for p in product_list if _status_conflict(p))),
        order_total_mismatches=tuple(sorted(o.id for o in order_list if _total_mismatch(o, by_id))),
        installment_violations=tuple(
            sorted(
                o.id
                for o in order_list
                if o.payment_method.installments > 1
                and not installment_allowed(o.total, o.payment_method.installments)
            )
        ),
        duplicate_email_customers=tuple(
            tuple(ids) for _, ids in sorted(emails.items()) if len(ids) > 1
        ),
        empty_categories=tuple(sorted(c.id for c in categories if c.id not in used_categories)),
        rejected=tuple(rejected),
    )
