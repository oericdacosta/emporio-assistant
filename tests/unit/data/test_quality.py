from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

from emporio.data.quality import QualityReport, Rejection, build_report
from emporio.data.repository import CsvRepository
from emporio.domain.models import (
    Category,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    Product,
    ProductStatus,
)


def make_order(
    order_id: int, total: str, method: PaymentMethod, items: tuple[OrderItem, ...]
) -> Order:
    return Order(
        order_id, 1, date(2026, 1, 1), OrderStatus.DELIVERED, Decimal(total), method, items
    )


def report(
    products: list[Product] | None = None,
    orders: list[Order] | None = None,
    customers: list[Customer] | None = None,
    categories: list[Category] | None = None,
    rejected: list[Rejection] | None = None,
) -> QualityReport:
    return build_report(
        categories=categories or [],
        products=products or [],
        customers=customers or [],
        orders=orders or [],
        rejected=rejected or [],
    )


def test_status_and_stock_conflicts(make_product: Callable[..., Product]) -> None:
    products = [
        make_product(1, stock=0, status=ProductStatus.ACTIVE),  # ativo sem estoque
        make_product(2, stock=5, status=ProductStatus.ACTIVE),  # normal
        make_product(3, stock=3, status=ProductStatus.DISCONTINUED),  # descontinuado com estoque
        make_product(4, stock=0, status=ProductStatus.DISCONTINUED),  # normal
        make_product(5, stock=2, status=ProductStatus.COMING_SOON),  # em breve com estoque
    ]
    assert report(products=products).status_stock_conflicts == (1, 3, 5)


def test_order_total_must_match_the_sum_of_items(make_product: Callable[..., Product]) -> None:
    product = make_product(10, price="100")
    ok = make_order(1, "200", PaymentMethod.PIX, (OrderItem(10, 2),))
    different = make_order(2, "180", PaymentMethod.PIX, (OrderItem(10, 2),))
    unknown_product = make_order(3, "999", PaymentMethod.PIX, (OrderItem(77, 1),))
    assert report(
        products=[product], orders=[ok, different, unknown_product]
    ).order_total_mismatches == (2,)


def test_installments_below_the_minimum_are_reported() -> None:
    orders = [
        make_order(1, "349.90", PaymentMethod.CREDIT_6X, ()),  # 58,32 por parcela: abaixo de R$ 80
        make_order(2, "3450", PaymentMethod.CREDIT_12X, ()),  # 287,50: ok
        make_order(3, "159.90", PaymentMethod.CREDIT_3X, ()),  # 53,30: ok
        make_order(4, "349.90", PaymentMethod.PIX, ()),  # à vista nunca viola
    ]
    assert report(orders=orders).installment_violations == (1,)


def test_customers_with_the_same_email_are_grouped_ignoring_case() -> None:
    customers = [
        Customer(1, "A", "Campo Grande", email="Bruno@jmail.com"),
        Customer(2, "B", "Dourados", email="bruno@jmail.com"),
        Customer(3, "C", "Corumbá", email="outro@jmail.com"),
    ]
    assert report(customers=customers).duplicate_email_customers == ((1, 2),)


def test_categories_without_products_are_reported(make_product: Callable[..., Product]) -> None:
    categories = [Category(5, "Violões"), Category(6, "Sopros")]
    assert report(
        products=[make_product(1, category_id=5)], categories=categories
    ).empty_categories == (6,)


def test_summary_has_only_counts_and_clean_flag() -> None:
    clean = report()
    assert clean.is_clean
    dirty = report(
        customers=[Customer(1, "A", "X", email="a@x.com"), Customer(2, "B", "Y", email="a@x.com")]
    )
    assert not dirty.is_clean
    assert "a@x.com" not in dirty.summary()
    assert "1 e-mails repetidos" in dirty.summary()


def test_real_data_reports_the_known_inconsistencies(data_dir: Path) -> None:
    result = CsvRepository.load(data_dir).quality_report()
    assert result.status_stock_conflicts == (96, 130, 136)
    assert result.order_total_mismatches == (3, 20)
    assert result.installment_violations == (9, 16, 18)
    assert result.duplicate_email_customers == ((11, 43),)
    assert result.empty_categories == (6, 7, 8)
    assert result.rejected == ()
