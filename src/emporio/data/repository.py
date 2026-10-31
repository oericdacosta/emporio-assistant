"""Leitura dos CSVs da loja e consultas simples sobre os dados.

O repositório entrega os dados crus (preço de tabela, percentual de promoção). As contas de
preço, parcela e prazo ficam em ``emporio.domain``. Linhas inválidas não derrubam a carga:
vão para a lista de rejeitadas, que aparece no relatório de qualidade.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Final, Self, TypeVar

from emporio.data.quality import QualityReport, Rejection, build_report
from emporio.domain.models import (
    Category,
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    PaymentMethod,
    Product,
    ProductStatus,
    Promotion,
)

T = TypeVar("T")

_TRACKING: Final = re.compile(r"^BR[A-Z0-9]{9}BR$")
_FILES: Final = ("categories", "products", "promotions", "customers", "orders", "order_items")


class DataLoadError(Exception):
    """Os dados não puderam ser carregados (pasta ou arquivo ausente)."""


def default_data_dir() -> Path:
    """Pasta com os CSVs: variável de ambiente, depois o repositório, depois o pacote instalado."""
    if configured := os.environ.get("EMPORIO_DATA_DIR"):
        return Path(configured)
    here = Path(__file__).resolve()
    checkout = here.parents[3] / "data" / "raw"
    return checkout if checkout.is_dir() else here.parents[1] / "_data" / "raw"


def _rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from enumerate(csv.DictReader(handle), start=2)


def _text_or_none(value: str) -> str | None:
    return value.strip() or None


def _parse_category(row: dict[str, str]) -> Category:
    return Category(id=int(row["category_id"]), name=row["name"].strip())


def _parse_product(row: dict[str, str]) -> Product:
    specs = json.loads(row["specs"]) if row["specs"].strip() else {}
    if not isinstance(specs, dict):
        raise ValueError("specs deve ser um objeto JSON")
    return Product(
        id=int(row["product_id"]),
        name=row["name"].strip(),
        category_id=int(row["category_id"]),
        price=Decimal(row["price_brl"]),
        stock=int(row["stock_quantity"]),
        status=ProductStatus(row["status"]),
        description=row["description"].strip(),
        specs={str(key): str(value) for key, value in specs.items()},
    )


def _parse_promotion(row: dict[str, str]) -> Promotion:
    return Promotion(
        id=int(row["promotion_id"]),
        product_id=int(row["product_id"]),
        discount_percent=int(row["discount_percent"]),
        is_active=row["is_active"].strip() == "1",
    )


def _parse_customer(row: dict[str, str]) -> Customer:
    return Customer(
        id=int(row["customer_id"]),
        name=row["name"].strip(),
        city=row["city"].strip(),
        phone=row["phone"].strip(),
        email=row["email"].strip(),
    )


def _parse_item(row: dict[str, str]) -> tuple[int, OrderItem]:
    quantity = int(row["quantity"])
    if quantity <= 0:
        raise ValueError("quantidade deve ser positiva")
    return int(row["order_id"]), OrderItem(product_id=int(row["product_id"]), quantity=quantity)


def _parse_order(row: dict[str, str], items: tuple[OrderItem, ...]) -> Order:
    tracking = _text_or_none(row["tracking_code"])
    if tracking is not None and not _TRACKING.match(tracking):
        raise ValueError("código de rastreio fora do formato esperado")
    delivery = _text_or_none(row["estimated_delivery"])
    return Order(
        id=int(row["order_id"]),
        customer_id=int(row["customer_id"]),
        order_date=date.fromisoformat(row["order_date"]),
        status=OrderStatus(row["status"]),
        total=Decimal(row["total_brl"]),
        payment_method=PaymentMethod(row["payment_method"]),
        items=items,
        tracking_code=tracking,
        estimated_delivery=date.fromisoformat(delivery) if delivery else None,
        notes=_text_or_none(row["notes"]),
    )


class CsvRepository:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._rejected: list[Rejection] = []

        missing = [name for name in _FILES if not (data_dir / f"{name}.csv").is_file()]
        if missing:
            raise DataLoadError(
                f"arquivos ausentes em {data_dir}: {', '.join(f'{m}.csv' for m in missing)}"
            )

        self._categories = {c.id: c for c in self._load("categories", _parse_category)}
        self._products = {
            p.id: p
            for p in self._load("products", _parse_product)
            if self._known(
                p.category_id in self._categories, "products", f"produto {p.id}", "categoria"
            )
        }
        self._promotions: dict[int, list[Promotion]] = defaultdict(list)
        for promo in self._load("promotions", _parse_promotion):
            if self._known(
                promo.product_id in self._products, "promotions", f"promoção {promo.id}", "produto"
            ):
                self._promotions[promo.product_id].append(promo)
        self._customers = {c.id: c for c in self._load("customers", _parse_customer)}
        self._orders = self._load_orders()

    @classmethod
    def load(cls, data_dir: Path | None = None) -> Self:
        return cls(data_dir or default_data_dir())

    # --- carga ---------------------------------------------------------------------------------

    def _load(self, file: str, parse: Callable[[dict[str, str]], T]) -> list[T]:
        parsed: list[T] = []
        for line, row in _rows(self.data_dir / f"{file}.csv"):
            try:
                parsed.append(parse(row))
            except (KeyError, ValueError, ArithmeticError) as exc:
                self._rejected.append(Rejection(file, line, _reason(exc)))
        return parsed

    def _known(self, found: bool, file: str, who: str, what: str) -> bool:
        """Registra a rejeição quando a referência aponta para algo que não existe."""
        if not found:
            self._rejected.append(Rejection(file, 0, f"{who} aponta para {what} inexistente"))
        return found

    def _load_orders(self) -> dict[int, Order]:
        items: dict[int, list[OrderItem]] = defaultdict(list)
        for order_id, item in self._load("order_items", _parse_item):
            if self._known(
                item.product_id in self._products,
                "order_items",
                f"item do pedido {order_id}",
                "produto",
            ):
                items[order_id].append(item)

        orders: dict[int, Order] = {}
        for line, row in _rows(self.data_dir / "orders.csv"):
            try:
                order = _parse_order(row, tuple(items.get(int(row["order_id"]), ())))
            except (KeyError, ValueError, ArithmeticError) as exc:
                self._rejected.append(Rejection("orders", line, _reason(exc)))
                continue
            if self._known(
                order.customer_id in self._customers, "orders", f"pedido {order.id}", "cliente"
            ):
                orders[order.id] = order
        for order_id in items.keys() - orders.keys():
            self._rejected.append(
                Rejection("order_items", 0, f"itens do pedido {order_id} sem pedido")
            )
        return orders

    # --- consultas -----------------------------------------------------------------------------

    def categories(self) -> tuple[Category, ...]:
        return tuple(self._categories.values())

    def category(self, category_id: int) -> Category | None:
        return self._categories.get(category_id)

    def products(self) -> tuple[Product, ...]:
        return tuple(self._products.values())

    def product(self, product_id: int) -> Product | None:
        return self._products.get(product_id)

    def promotions_for(self, product_id: int) -> tuple[Promotion, ...]:
        """Todas as promoções do produto, ativas ou não; quem calcula o preço filtra as ativas."""
        return tuple(self._promotions.get(product_id, ()))

    def customer(self, customer_id: int) -> Customer | None:
        return self._customers.get(customer_id)

    def order(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    def orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def customers(self) -> tuple[Customer, ...]:
        return tuple(self._customers.values())

    def quality_report(self) -> QualityReport:
        return build_report(
            categories=self._categories.values(),
            products=self._products.values(),
            customers=self._customers.values(),
            orders=self._orders.values(),
            rejected=self._rejected,
        )


def _reason(exc: Exception) -> str:
    if isinstance(exc, KeyError):
        return f"coluna ausente: {exc}"
    return str(exc) or type(exc).__name__
