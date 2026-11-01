from __future__ import annotations

import pytest

from emporio.domain.identity_slots import extract_order_id, extract_proof


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cadê meu pedido 4?", 4),
        ("pedido n° 4", 4),
        ("Pedido nº 12 por favor", 12),
        ("pedido #4", 4),
        ("e o #7?", 7),
        ("Status do 17? matheus.correia@hayoo.com.br", 17),  # G31
        ("cadê o 8", 8),
        ("o pedido é o 10", 10),
        ("e do 17?", 17),
        ("Pedido 3, isabela.moreira@jmail.com", 3),  # G32 t1
        ("Agora o pedido 4", 4),  # G32 t2
        ("pedido 3 e depois o pedido 4", 4),  # o último citado
        ("O e-mail é lucas.mendes@jmail.com", None),  # G32 t3
        ("Quero um violão de R$ 1.200,00", None),
        ("pedi dia 12/03 e paguei 800 reais", None),
        ("me liga (67) 99999-7821", None),
        ("os 4 últimos dígitos são 7821", None),
        ("pedido 3.500,00", None),
    ],
)
def test_extract_order_id(text: str, expected: int | None) -> None:
    assert extract_order_id(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Status do 17? matheus.correia@hayoo.com.br", "matheus.correia@hayoo.com.br"),
        ("O e-mail é lucas.mendes@jmail.com", "lucas.mendes@jmail.com"),
        ("os 4 últimos dígitos são 7821", "7821"),
        ("final 7821", "7821"),
        ("Pedido 8, telefone final 7821", "7821"),
        ("meu telefone é (67) 99999-7821", "7821"),
        ("pedido 1234 telefone", None),  # número do pedido não é prova
        ("pedido 8, 7821", None),  # sem mencionar telefone/final
        ("Cadê meu pedido 4?", None),
        ("Status do 17?", None),
    ],
)
def test_extract_proof(text: str, expected: str | None) -> None:
    assert extract_proof(text) == expected
