from __future__ import annotations

import pytest

from emporio.privacy import redact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("meu email é ana@jmail.com ok", "meu email é [email] ok"),
        ("cpf 123.456.789-09 e 12345678909", "cpf [cpf] e [cpf]"),
        ("ligue (67) 99812-3456 ou 67 99812-3456", "ligue [telefone] ou [telefone]"),
        ("rastreio BRAB1234567BR chegou?", "rastreio [rastreio] chegou?"),
        ("cartão 4111 1111 1111 1111 e 4111-1111-1111-1111", "cartão [cartao] e [cartao]"),
        ("o violão custa R$ 2.199,00", "o violão custa R$ 2.199,00"),
        ("pedido 12 em 12x de R$ 183,25", "pedido 12 em 12x de R$ 183,25"),
    ],
)
def test_redact(raw: str, expected: str) -> None:
    assert redact(raw) == expected


def test_mask_hides_secrets_and_personal_data_for_display() -> None:
    from emporio.privacy import mask

    masked = mask(
        {"proof": "a@b.com", "summary": "ligue (67) 99812-3456", "n": 4, "l": ["a@b.com"]}
    )
    assert masked == {"proof": "***", "summary": "ligue [telefone]", "n": 4, "l": ["[email]"]}
