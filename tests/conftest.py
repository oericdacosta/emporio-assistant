from __future__ import annotations

import os
import socket
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.clock import TZ, FixedClock
from emporio.data.policies import PolicyStore
from emporio.data.repository import CsvRepository
from emporio.domain.models import Product, ProductStatus
from emporio.tools import build_registry
from emporio.tools.deps import ToolContext
from emporio.tools.registry import ToolRegistry


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


@pytest.fixture(scope="session")
def repo(data_dir: Path) -> CsvRepository:
    return CsvRepository.load(data_dir)


@pytest.fixture(scope="session")
def registry(repo: CsvRepository) -> ToolRegistry:
    return build_registry(repo)


@pytest.fixture(scope="session")
def policies(data_dir: Path) -> PolicyStore:
    return PolicyStore.load(data_dir.parent)


@pytest.fixture
def ctx(repo: CsvRepository, data_dir: Path) -> ToolContext:
    """Contexto das tools com o relógio parado numa segunda-feira, 10h, com a loja aberta."""
    clock = FixedClock.parse("2026-03-23T10:00:00")
    return ToolContext(repo, clock, PolicyStore.load(data_dir.parent))


LIVE_VARS = ("OPENAI_API_KEY", "LLM_MODEL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    live_ok = os.getenv("EMPORIO_LIVE") == "1" and bool(os.getenv("OPENAI_API_KEY"))
    skip = pytest.mark.skip(reason="live: defina EMPORIO_LIVE=1, OPENAI_API_KEY e LLM_MODEL")
    for item in items:
        if "live" in item.keywords and not live_ok:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _hermetic(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Teste comum não toca a rede nem enxerga as chaves do desenvolvedor."""
    if request.node.get_closest_marker("live"):
        return
    for name in LIVE_VARS:
        monkeypatch.delenv(name, raising=False)

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("rede bloqueada nos testes (use @pytest.mark.live)")

    monkeypatch.setattr(socket.socket, "connect", blocked)
