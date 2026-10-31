from __future__ import annotations

from decimal import Decimal

import pytest

from emporio.domain.pricing import price_for, subtotal
from emporio.domain.shipping import Zone, is_large_item, normalize_text, quote_shipping


@pytest.mark.parametrize(
    ("subtotal", "fee"),
    [
        ("499.99", "35"),
        ("500", "35"),
        ("500.00", "35"),
        ("500.01", "0"),
        ("2199", "0"),
        ("159.90", "35"),
    ],
)
def test_campo_grande_fee(subtotal: str, fee: str) -> None:
    result = quote_shipping("Campo Grande", subtotal)
    assert (result.zone, result.fee) == (
        Zone.CAMPO_GRANDE,
        Decimal(fee),
    )  # grátis só ESTRITAMENTE acima de 500


@pytest.mark.parametrize("city", ["campo grande", "  CAMPO   GRANDE ", "Campo Grande"])
def test_city_is_normalized(city: str) -> None:
    assert quote_shipping(city, "100").zone is Zone.CAMPO_GRANDE


@pytest.mark.parametrize("city", ["Dourados", "Três Lagoas", "Corumbá", "Ponta Porã"])
def test_other_cities_never_get_an_invented_fee(city: str) -> None:
    result = quote_shipping(city, "900")
    assert result.zone is Zone.OTHER and result.fee is None and len(result.carriers) == 3
    assert not result.needs_quote
    assert quote_shipping(city, "900", large_item=True).needs_quote


def test_validation_and_helpers() -> None:
    with pytest.raises(ValueError, match="cidade"):
        quote_shipping("  ", "100")
    assert normalize_text("Três Lagoas") == "tres lagoas"
    assert is_large_item("Baterias e Percussão") and not is_large_item("Violões")


def test_exact_500_pays_and_subtotal_is_post_promo_times_quantity() -> None:
    # C6: "acima de R$ 500" é estrito; subtotal = vigente x qtd, antes do PIX
    assert quote_shipping("Campo Grande", subtotal([(price_for("250"), 2)])).fee == Decimal(
        35
    )  # 2 x 250 = 500
    assert quote_shipping("Campo Grande", subtotal([(price_for("250.01"), 2)])).fee == Decimal(0)
    assert quote_shipping("Campo Grande", subtotal([(price_for("600", 20), 1)])).fee == Decimal(
        35
    )  # 480 pós-promo
    pix_only_below = subtotal(
        [(price_for("510"), 1)]
    )  # PIX levaria a 484,50, mas o subtotal ignora o PIX
    assert quote_shipping("Campo Grande", pix_only_below).fee == Decimal(0)
