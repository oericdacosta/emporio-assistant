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
        ("o violão custa R$ 2.199,00", "o violão custa R$ 2.199,00"),
    ],
)
def test_redact(raw: str, expected: str) -> None:
    assert redact(raw) == expected
