from __future__ import annotations

from collections.abc import Callable

import pytest

from emporio.domain.availability import (
    Availability,
    availability,
    disclosable_stock,
    is_sellable,
    product_availability,
)
from emporio.domain.models import Product, ProductStatus

A, D, C = ProductStatus.ACTIVE, ProductStatus.DISCONTINUED, ProductStatus.COMING_SOON


@pytest.mark.parametrize(
    ("status", "stock", "expected"),
    [
        (A, 5, Availability.AVAILABLE),  # 95 Takamine GD20
        (A, 0, Availability.OUT_OF_STOCK),  # 96 Giannini: active com estoque 0
        (D, 13, Availability.DISCONTINUED),  # 136 Ibanez Bass 2X: status manda sobre estoque
        (D, 0, Availability.DISCONTINUED),  # 113 Shelby
        (C, 2, Availability.COMING_SOON),  # 130 Kala: nunca confirmar as 2 unidades
    ],
)
def test_availability_status_beats_stock(
    status: ProductStatus, stock: int, expected: Availability
) -> None:
    assert availability(status, stock) is expected


def test_sellable_and_disclosable_stock(make_product: Callable[..., Product]) -> None:
    assert is_sellable(make_product(stock=1)) and disclosable_stock(make_product(stock=3)) == 3
    assert disclosable_stock(make_product(stock=4)) is None  # não revela quantidade alta
    assert disclosable_stock(make_product(stock=2, status=C)) is None  # 130: não expõe estoque
    assert disclosable_stock(make_product(stock=0)) is None
    assert product_availability(make_product(stock=0)) is Availability.OUT_OF_STOCK
