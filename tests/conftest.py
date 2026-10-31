from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.clock import TZ
from emporio.domain.models import Product, ProductStatus


@pytest.fixture
def at() -> Callable[..., datetime]:
    """Cria datetimes em America/Campo_Grande: ``at(2026, 3, 23, 9, 0)``."""

    def _at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
        return datetime(year, month, day, hour, minute, tzinfo=TZ)

    return _at


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


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Pasta com os CSVs reais do repositório."""
    return Path(__file__).resolve().parents[1] / "data" / "raw"
