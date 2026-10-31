from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from emporio.domain.models import Product, ProductStatus, Promotion
from emporio.domain.pricing import (
    brl,
    installment_allowed,
    installment_options,
    max_installments,
    min_installment,
    money,
    price_for,
    quote,
    subtotal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),
        ("0.004", "0.00"),
        ("2.675", "2.68"),
        (10, "10.00"),
        (Decimal("1967.175"), "1967.18"),
    ],
)
def test_money_rounds_half_up(raw: Decimal | str | int, expected: str) -> None:
    assert money(raw) == Decimal(expected)


def test_money_rejects_float() -> None:
    with pytest.raises(TypeError):
        money(0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("2089.05", "R$ 2.089,05"),
        (35, "R$ 35,00"),
        ("11499", "R$ 11.499,00"),
        ("0.5", "R$ 0,50"),
        ("1234567.8", "R$ 1.234.567,80"),
    ],
)
def test_brl_format(value: str | int, text: str) -> None:
    assert brl(value) == text


# Casos reais dos CSVs: (produto, tabela, promo %, preço vigente, PIX)
@pytest.mark.parametrize(
    ("table", "promo", "current", "pix"),
    [
        (
            "2199",
            None,
            "2199.00",
            "2089.05",
        ),  # 95 Takamine GD20: a promoção existe, mas está inativa
        ("2399", 18, "1967.18", "1967.18"),  # 90 Crafter HT-100: PIX NÃO soma 5% à promo
        ("799.9", None, "799.90", "759.91"),  # 96 Giannini GF-3D: 759,905 -> HALF_UP
        ("359", 10, "323.10", "323.10"),  # 127 Kalani
        ("549", 20, "439.20", "439.20"),  # 121 Ohana
        ("5999", 8, "5519.08", "5519.08"),  # 94 Taylor 110e
    ],
)
def test_price_for_real_products(table: str, promo: int | None, current: str, pix: str) -> None:
    price = price_for(table, promo)
    assert (price.price, price.pix_price) == (Decimal(current), Decimal(pix))
    assert price.pix_discount_applied is (promo is None)


def test_pix_never_stacks_with_promo() -> None:
    price = price_for("2399", 18)
    assert price.pix_price == price.price != money(price.price * Decimal("0.95"))


@pytest.mark.parametrize("pct", [0, 100, -5])
def test_invalid_promo_percent(pct: int) -> None:
    with pytest.raises(ValueError, match="percentual"):
        price_for("100", pct)


def test_quote_applies_only_active_promo_on_active_product(
    make_product: Callable[..., Product],
) -> None:
    prod = make_product(95, "2199")
    inactive = Promotion(4, 95, 12, is_active=False)
    assert quote(prod, [inactive]).price == Decimal("2199.00")  # GD20 real
    big, small = Promotion(1, 95, 10, True), Promotion(2, 95, 15, True)
    assert quote(prod, [big, small]).promo_percent == 15  # maior, sem acumular
    assert quote(prod, [Promotion(3, 999, 50, True)]).promo_percent is None  # outro produto
    discontinued = make_product(113, "500", status=ProductStatus.DISCONTINUED)
    assert quote(discontinued, [Promotion(16, 113, 30, True)]).promo_percent is None


# Parcelamento com piso por parcela (R$ 50, R$ 80 ou R$ 100 conforme a faixa). Valores reais dos CSVs.
@pytest.mark.parametrize(
    ("total", "expected_max"),
    [
        ("159.90", 3),  # item mais barato do catálogo: 3x de 53,30
        ("149.99", 2),  # 3x = 49,99 < 50
        ("150", 3),
        ("100", 2),
        ("99.99", 1),
        ("40", 1),
        ("349.90", 4),  # 5x daria 69,98, abaixo do piso de R$ 80
        ("429.90", 5),  # 6x daria 71,65, abaixo do piso de R$ 80
        ("459.90", 5),  # 12x daria 38,33: só até 5x é permitido
        ("799.90", 7),  # 7x dá 114,27; 8x daria 99,98, abaixo de R$ 100
        ("3450", 12),  # 12x de 287,50
        ("7499", 12),
    ],
)
def test_max_installments_real_totals(total: str, expected_max: int) -> None:
    assert max_installments(total) == expected_max


@pytest.mark.parametrize(
    ("total", "n", "ok"),
    [
        ("349.90", 6, False),
        ("429.90", 6, False),
        ("459.90", 12, False),
        ("159.90", 3, True),
        ("3450", 12, True),
    ],
)
def test_installment_allowed_matches_historic_orders(total: str, n: int, ok: bool) -> None:
    assert installment_allowed(total, n) is ok


def test_installment_boundaries_are_inclusive() -> None:
    assert min_installment(1) == 0
    assert [min_installment(n) for n in (2, 3, 4, 6, 7, 12)] == [50, 50, 80, 80, 100, 100]
    assert (
        installment_allowed("80", 1)
        and installment_allowed("480", 6)
        and not installment_allowed("479.99", 6)
    )
    assert installment_allowed("700", 7) and not installment_allowed("699.99", 7)


def test_display_is_half_up_and_last_installment_absorbs_difference() -> None:
    option = installment_options("100")[1]  # 2x
    assert (option.n, option.installment, option.last_installment) == (
        2,
        Decimal("50.00"),
        Decimal("50.00"),
    )
    option = installment_options("1000.01")[2]  # 3x: 333,3367 -> 333,34; sobra 333,33
    assert (option.installment, option.last_installment) == (Decimal("333.34"), Decimal("333.33"))
    option = installment_options("3450")[-1]  # 12x de 287,50
    assert (option.n, option.installment, option.last_installment) == (
        12,
        Decimal("287.50"),
        Decimal("287.50"),
    )
    option = installment_options("459.90")[4]  # 5x de 91,98
    assert (option.n, option.installment, option.last_installment) == (
        5,
        Decimal("91.98"),
        Decimal("91.98"),
    )


def test_minimum_is_checked_on_unrounded_value_not_on_displayed_installment() -> None:
    # 12 x 100 = 1200: com R$ 1.199,99 a parcela exibida (HALF_UP) seria 100,00, mas o plano é inválido
    assert money(Decimal("1199.99") / 12) == Decimal("100.00")
    assert not installment_allowed("1199.99", 12) and max_installments("1199.99") == 11
    assert max_installments("1200") == 12
    # 3 x 50 = 150: R$ 149,99 -> 49,9967 exibiria 50,00, mas continua só 2x
    assert money(Decimal("149.99") / 3) == Decimal("50.00") and max_installments("149.99") == 2


def test_subtotal_is_current_price_times_quantity_before_pix() -> None:
    promo = price_for("600", 20)  # tabela 600 -> vigente 480
    plain = price_for("250")
    assert subtotal([(promo, 1)]) == Decimal("480.00")
    assert subtotal([(plain, 2)]) == Decimal("500.00")
    assert subtotal([(plain, 1), (promo, 2)]) == Decimal("1210.00")
    assert subtotal([]) == Decimal("0.00")


def test_min_installment_rejects_out_of_range() -> None:
    for n in (0, 13):
        with pytest.raises(ValueError, match="parcelas"):
            min_installment(n)


def test_installment_options_rejects_non_positive_total() -> None:
    with pytest.raises(ValueError, match="positivo"):
        installment_options(0)


@given(st.decimals(min_value="0.01", max_value="200000", places=2))
def test_installment_plans_are_consistent(total: Decimal) -> None:
    options = installment_options(total)
    assert options[0].n == 1 and [o.n for o in options] == list(range(1, len(options) + 1))
    for o in options:
        assert o.installment * (o.n - 1) + o.last_installment == total  # sem juros, soma exata
        assert o.installment >= min_installment(o.n)  # parcela exibida nunca abaixo do mínimo
        assert abs(o.last_installment - o.installment) <= o.n * Decimal("0.005")
    if len(options) < 12:  # o próximo plano é de fato inválido
        assert not installment_allowed(total, len(options) + 1)


@given(
    st.decimals(min_value="1", max_value="100000", places=2), st.integers(min_value=1, max_value=99)
)
def test_pix_price_never_exceeds_list_price(table: Decimal, promo: int) -> None:
    assert price_for(table).pix_price <= money(table)
    assert price_for(table, promo).price <= money(table)
