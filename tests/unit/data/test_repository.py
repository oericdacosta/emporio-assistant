from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.data.repository import CsvRepository, DataLoadError, default_data_dir
from emporio.domain.models import OrderStatus, PaymentMethod, ProductStatus


@pytest.fixture(scope="module")
def repo() -> CsvRepository:
    return CsvRepository.load(Path(__file__).resolve().parents[3] / "data" / "raw")


def test_loads_every_row_of_the_real_files(repo: CsvRepository) -> None:
    assert len(repo.categories()) == 9
    assert len(repo.products()) == 65
    assert len(repo.customers()) == 50
    assert len(repo.orders()) == 20
    assert sum(len(order.items) for order in repo.orders()) == 22
    assert sum(len(repo.promotions_for(p.id)) for p in repo.products()) == 25
    assert repo.quality_report().rejected == ()


def test_product_is_parsed_with_decimal_price_and_specs(repo: CsvRepository) -> None:
    product = repo.product(95)
    assert product is not None
    assert product.name == "Takamine GD20 Dreadnought Natural"
    assert product.price == Decimal("2199")
    assert product.status is ProductStatus.ACTIVE
    assert product.specs["strings"] == "steel"


def test_order_fields_are_parsed(repo: CsvRepository) -> None:
    delivered = repo.order(1)
    assert delivered is not None
    assert delivered.status is OrderStatus.DELIVERED
    assert delivered.payment_method is PaymentMethod.PIX
    assert delivered.tracking_code == "BRAB1234567BR"

    pending = repo.order(9)
    assert pending is not None
    assert pending.tracking_code is None and pending.estimated_delivery is None

    cancelled = repo.order(17)
    assert cancelled is not None and cancelled.notes


def test_order_items_are_attached_with_quantity(repo: CsvRepository) -> None:
    order = repo.order(3)
    assert order is not None
    assert [(item.product_id, item.quantity) for item in order.items] == [(130, 2)]


def test_promotions_are_returned_for_the_product_even_when_inactive(repo: CsvRepository) -> None:
    promotions = repo.promotions_for(96)
    assert len(promotions) == 2
    assert not any(promo.is_active for promo in promotions)
    assert repo.promotions_for(81) == ()


def test_unknown_ids_return_none(repo: CsvRepository) -> None:
    assert repo.product(1) is None
    assert repo.order(999) is None
    assert repo.customer(999) is None
    assert repo.category(999) is None


def test_data_dir_comes_from_the_environment_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EMPORIO_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path


def test_data_dir_falls_back_to_the_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMPORIO_DATA_DIR", raising=False)
    assert (default_data_dir() / "products.csv").is_file()


def test_missing_directory_or_file_gives_a_clear_error(data_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(DataLoadError, match="products.csv"):
        CsvRepository.load(tmp_path)
    copy = tmp_path / "partial"
    shutil.copytree(data_dir, copy)
    (copy / "orders.csv").unlink()
    with pytest.raises(DataLoadError, match="orders.csv"):
        CsvRepository.load(copy)


def _append(folder: Path, name: str, line: str) -> None:
    path = folder / f"{name}.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\r\n" + line, encoding="utf-8", newline="")


def test_invalid_rows_are_rejected_without_stopping_the_load(
    data_dir: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "raw"
    shutil.copytree(data_dir, folder)
    _append(
        folder, "products", '146,-5,Produto invalido,5,Sem preco valido,3,active,"{}",2025-01-01'
    )
    _append(folder, "promotions", "999,9999,10,Teste,1")
    _append(folder, "orders", "98,1,2026-01-01,delivered,100,pix,BRRUIM,,")
    _append(folder, "orders", "99,9999,2026-01-01,delivered,100,pix,,,")
    _append(folder, "order_items", "77,1,81")

    repo = CsvRepository.load(folder)
    reasons = {(r.file, r.reason) for r in repo.quality_report().rejected}

    assert len(repo.products()) == 65  # o produto inválido não entrou
    assert repo.order(98) is None and repo.order(99) is None
    assert ("products", "produto 146: preço deve ser positivo") in reasons
    assert ("promotions", "promoção 999 aponta para produto inexistente") in reasons
    assert ("orders", "código de rastreio fora do formato esperado") in reasons
    assert ("orders", "pedido 99 aponta para cliente inexistente") in reasons
    assert ("order_items", "itens do pedido 77 sem pedido") in reasons


def test_specs_quantity_and_missing_column_are_rejected(data_dir: Path, tmp_path: Path) -> None:
    folder = tmp_path / "raw"
    shutil.copytree(data_dir, folder)
    _append(folder, "products", "147,100,Produto,5,Specs erradas,3,active,[],2025-01-01")
    _append(folder, "order_items", "1,0,93")
    customers = folder / "customers.csv"
    customers.write_text(
        customers.read_text(encoding="utf-8").replace("city", "cidade", 1),
        encoding="utf-8",
        newline="",
    )

    repo = CsvRepository.load(folder)
    reasons = {r.reason for r in repo.quality_report().rejected}

    assert "specs deve ser um objeto JSON" in reasons
    assert "quantidade deve ser positiva" in reasons
    assert "coluna ausente: 'city'" in reasons
    assert repo.customers() == ()
