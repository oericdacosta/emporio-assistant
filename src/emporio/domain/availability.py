"""Disponibilidade de produto. O status manda sobre o estoque."""

from __future__ import annotations

from enum import StrEnum

from emporio.domain.models import Product, ProductStatus

LOW_STOCK_MAX = 3  # só se diz a quantidade quando restam poucas unidades


class Availability(StrEnum):
    AVAILABLE = "available"
    OUT_OF_STOCK = "out_of_stock"  # active, estoque 0
    DISCONTINUED = "discontinued"  # nunca vender, mesmo com estoque
    COMING_SOON = "coming_soon"  # nunca confirmar unidades


def availability(status: ProductStatus, stock: int) -> Availability:
    match status:
        case ProductStatus.DISCONTINUED:
            return Availability.DISCONTINUED
        case ProductStatus.COMING_SOON:
            return Availability.COMING_SOON
        case ProductStatus.ACTIVE:
            return Availability.AVAILABLE if stock > 0 else Availability.OUT_OF_STOCK


def product_availability(product: Product) -> Availability:
    return availability(product.status, product.stock)


def is_sellable(product: Product) -> bool:
    return product_availability(product) is Availability.AVAILABLE


def disclosable_stock(product: Product) -> int | None:
    """Quantidade que pode ser dita ao cliente: só 1..LOW_STOCK_MAX de item vendável."""
    if is_sellable(product) and product.stock <= LOW_STOCK_MAX:
        return product.stock
    return None
