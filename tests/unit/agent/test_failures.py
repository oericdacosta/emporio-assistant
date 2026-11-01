from __future__ import annotations

from emporio.agent.failures import describe
from emporio.errors import LLMError, ProviderFatal, ProviderUnavailable


def test_customer_messages_per_provider_failure() -> None:
    busy = describe(ProviderUnavailable("429", rate_limited=True))
    assert "muita demanda" in busy.message and busy.retryable and not busy.misconfigured

    down = describe(ProviderUnavailable("502"))
    assert "instabilidade" in down.message and down.retryable and "(67) 3321-4500" in down.message

    config = describe(ProviderFatal("chave inválida"))
    assert config.misconfigured and not config.retryable

    assert describe(LLMError("?")).retryable


def test_customer_messages_have_no_money_and_no_technical_detail() -> None:
    for error in (
        ProviderUnavailable("x", rate_limited=True),
        ProviderUnavailable("x"),
        ProviderFatal("sk-secret"),
    ):
        message = describe(error).message
        assert "R$" not in message and "sk-secret" not in message and "Traceback" not in message
