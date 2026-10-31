from __future__ import annotations

from decimal import Decimal

import pytest

from emporio.domain.models import Customer, PaymentMethod, Product, ProductStatus, Promotion


@pytest.mark.parametrize(
    ("method", "n"),
    [
        ("pix", 1),
        ("boleto", 1),
        ("debit", 1),
        ("credit_3x", 3),
        ("credit_6x", 6),
        ("credit_12x", 12),
    ],
)
def test_payment_installments(method: str, n: int) -> None:
    assert PaymentMethod(method).installments == n


def test_unknown_enum_values_fail_fast() -> None:
    with pytest.raises(ValueError, match="credit_24x"):
        PaymentMethod("credit_24x")
    with pytest.raises(ValueError, match="archived"):
        ProductStatus("archived")


def test_invariants() -> None:
    with pytest.raises(ValueError, match="preço"):
        Product(1, "x", 1, Decimal(0), 1, ProductStatus.ACTIVE)
    with pytest.raises(ValueError, match="estoque"):
        Product(1, "x", 1, Decimal(1), -1, ProductStatus.ACTIVE)
    with pytest.raises(ValueError, match="percentual"):
        Promotion(1, 1, 100, True)


def test_customer_pii_is_not_in_repr() -> None:
    text = repr(
        Customer(1, "Lucas", "Campo Grande", phone="(67) 99812-3456", email="lucas@jmail.com")
    )
    assert "99812" not in text and "jmail" not in text
