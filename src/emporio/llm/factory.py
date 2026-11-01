"""Variáveis de ambiente -> cliente de LLM.

Não há modelo padrão de propósito: nomes de modelo envelhecem, e um erro de "modelo inexistente"
na primeira mensagem é pior do que um pedido claro de configuração.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from emporio.errors import ConfigError
from emporio.llm.base import LLMClient
from emporio.llm.openai import OpenAIClient


def make_llm(model: str | None = None, env: Mapping[str, str] = os.environ) -> LLMClient:
    model = model or env.get("LLM_MODEL")
    if not model:
        raise ConfigError("falta o modelo da OpenAI: informe --model ou a variável LLM_MODEL.")
    key = env.get("OPENAI_API_KEY")
    if not key:
        raise ConfigError(
            "falta a chave da OpenAI: defina OPENAI_API_KEY (veja .env.example). "
            "Para só ver uma conversa gravada, sem chave: emporio --demo <cassete.json>"
        )
    return OpenAIClient(model, api_key=key)
