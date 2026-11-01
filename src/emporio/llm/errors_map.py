"""Exceções do SDK da OpenAI -> erros que a aplicação trata.

A tradução é feita pelo nome da classe (e de suas bases), sem importar o SDK aqui.
"""

from __future__ import annotations

from typing import Final

from emporio.errors import LLMError, ProviderFatal, ProviderUnavailable

_RATE_LIMIT: Final = {"RateLimitError"}
_TRANSIENT: Final = {
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "OverloadedError",
    "ServiceUnavailableError",
}
_FATAL: Final = {
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "UnprocessableEntityError",
}


def translate(error: Exception) -> Exception:
    if isinstance(error, LLMError):
        return error
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & _RATE_LIMIT:
        return ProviderUnavailable("limite de uso do provedor atingido", rate_limited=True)
    if names & _TRANSIENT:
        return ProviderUnavailable(f"provedor indisponível ({type(error).__name__})")
    if names & _FATAL:
        return ProviderFatal(str(error))
    return error  # desconhecido: segue como erro inesperado
