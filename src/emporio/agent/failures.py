"""O que dizer ao cliente quando o provedor de LLM falha.

Nenhuma mensagem tem valor em reais nem promessa, e nenhuma deixa passar detalhe técnico.
"""

from __future__ import annotations

from dataclasses import dataclass

from emporio.domain.store import WHATSAPP
from emporio.errors import LLMError, ProviderFatal, ProviderUnavailable


@dataclass(frozen=True, slots=True)
class Failure:
    message: str  # para o cliente
    retryable: bool  # vale reenviar a mesma mensagem
    misconfigured: bool  # quem opera o sistema precisa agir (chave, modelo)


def describe(error: LLMError) -> Failure:
    if isinstance(error, ProviderUnavailable) and error.rate_limited:
        return Failure(
            "Estou com muita demanda agora. Tenta de novo em alguns instantes, por favor.",
            retryable=True,
            misconfigured=False,
        )
    if isinstance(error, ProviderFatal):
        return Failure(
            f"Tive um problema técnico e não consigo atender agora. Chama a equipe no WhatsApp {WHATSAPP}.",
            retryable=False,
            misconfigured=True,
        )
    return Failure(
        f"Estou com instabilidade no momento. Tenta de novo em instantes ou chama a equipe no WhatsApp {WHATSAPP}.",
        retryable=True,
        misconfigured=False,
    )
