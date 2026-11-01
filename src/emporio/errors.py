"""Erros que a aplicação sabe tratar. Cada um chega ao usuário como uma mensagem curta."""

from __future__ import annotations


class EmporioError(Exception):
    pass


class ConfigError(EmporioError):
    """Falta ou está errada alguma configuração (provedor, modelo, chave)."""

    exit_code = 3


class LLMError(EmporioError):
    """O provedor de LLM não respondeu como esperado."""


class ProviderUnavailable(LLMError):
    """Falha passageira (rede, limite de uso, servidor fora do ar): vale tentar de novo depois."""

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


class ProviderFatal(LLMError):
    """Falha que repetir não resolve (chave inválida, modelo inexistente, pedido recusado)."""


class CassetteError(EmporioError):
    """A conversa gravada não corresponde mais ao que o sistema produz."""
