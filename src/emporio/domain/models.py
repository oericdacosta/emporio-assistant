"""Entidades imutáveis do domínio, espelhando os CSVs da loja.

Dataclasses puras: a validação de formato acontece na carga dos dados
e nas entradas das ferramentas; aqui ficam só invariantes simples.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ProductStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class OrderStatus(StrEnum):
    PENDING = "pending"  # aguardando pagamento
    CONFIRMED = "confirmed"  # pago / em preparação
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    PIX = "pix"
    BOLETO = "boleto"
    DEBIT = "debit"
    CREDIT_3X = "credit_3x"
    CREDIT_6X = "credit_6x"
    CREDIT_12X = "credit_12x"

    @property
    def installments(self) -> int:
        """Nº de parcelas contratadas (1 para PIX, boleto e débito)."""
        head, _, tail = self.value.partition("_")
        return int(tail.removesuffix("x")) if head == "credit" else 1


@dataclass(frozen=True, slots=True)
class Category:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    name: str
    category_id: int
    price: Decimal  # preço de tabela
    stock: int
    status: ProductStatus
    description: str = ""
    specs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError(f"produto {self.id}: preço deve ser positivo")
        if self.stock < 0:
            raise ValueError(f"produto {self.id}: estoque negativo")


@dataclass(frozen=True, slots=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: int
    is_active: bool  # única fonte de vigência: o CSV não tem datas

    def __post_init__(self) -> None:
        if not 0 < self.discount_percent < 100:
            raise ValueError(f"promoção {self.id}: percentual fora de (0, 100)")


@dataclass(frozen=True, slots=True)
class Customer:
    id: int
    name: str
    city: str
    # Telefone e e-mail ficam fora do repr para não vazar em logs nem em tracebacks.
    phone: str = field(repr=False, default="")
    email: str = field(repr=False, default="")


@dataclass(frozen=True, slots=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True, slots=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: OrderStatus
    total: Decimal  # "total registrado": nunca recalcular
    payment_method: PaymentMethod
    items: tuple[OrderItem, ...] = ()
    tracking_code: str | None = None
    estimated_delivery: date | None = None
    notes: str | None = None
