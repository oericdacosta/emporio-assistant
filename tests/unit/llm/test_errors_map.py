from __future__ import annotations

import httpx
import openai

from emporio.errors import ProviderFatal, ProviderUnavailable
from emporio.llm.errors_map import translate

REQUEST = httpx.Request("POST", "https://api.exemplo.test/v1")


def status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    return cls("msg", response=httpx.Response(code, request=REQUEST), body=None)


def test_rate_limit_is_a_temporary_failure_flagged_as_such() -> None:
    error = translate(status_error(openai.RateLimitError, 429))
    assert isinstance(error, ProviderUnavailable) and error.rate_limited


def test_server_and_network_failures_are_temporary() -> None:
    server = translate(status_error(openai.InternalServerError, 500))
    assert isinstance(server, ProviderUnavailable) and not server.rate_limited
    assert isinstance(translate(openai.APITimeoutError(request=REQUEST)), ProviderUnavailable)
    assert isinstance(translate(openai.APIConnectionError(request=REQUEST)), ProviderUnavailable)


def test_configuration_failures_are_fatal() -> None:
    for cls, code in (
        (openai.AuthenticationError, 401),
        (openai.NotFoundError, 404),
        (openai.BadRequestError, 400),
    ):
        assert isinstance(translate(status_error(cls, code)), ProviderFatal)


def test_unknown_error_passes_through_untouched() -> None:
    error = RuntimeError("x")
    assert translate(error) is error


def test_already_translated_error_is_kept() -> None:
    error = ProviderFatal("x")
    assert translate(error) is error
