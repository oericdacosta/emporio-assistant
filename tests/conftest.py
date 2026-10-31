from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from emporio.domain.models import Product, ProductStatus


@pytest.fixture
def make_product() -> Callable[..., Product]:
    def _make(
        pid: int = 1,
        price: str = "1000",
        stock: int = 5,
        status: ProductStatus = ProductStatus.ACTIVE,
        category_id: int = 5,
    ) -> Product:
        return Product(pid, f"Produto {pid}", category_id, Decimal(price), stock, status)

    return _make
