"""Textos e pequenas funções puras da CLI."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Final

from emporio.cli import codes
from emporio.domain.hours import weekday_pt

AGENT_NAME: Final = "Téo"
MAX_CHARS: Final = 1000

HELP: Final = f"""Comandos (começam com /):
  /ajuda      mostra esta ajuda
  /novo       começa outra conversa (a anterior fica salva; retome com --session ID)
  /esquecer   apaga o histórico desta conversa e a verificação de identidade
  /trace      liga ou desliga o trace (consultas, tokens e latência de cada turno)
  /sair       encerra (Ctrl-D também)
Qualquer outra linha é enviada ao {AGENT_NAME} como mensagem do cliente (máx. {MAX_CHARS} caracteres)."""

EPILOG: Final = f"""exemplos:
  emporio --model <modelo> --trace
  emporio --model <modelo> --now 2026-02-20T10:00 -m "Posso devolver o pedido 7?"
  emporio --demo examples/cassettes/01-violoes-ate-1000.json   # sem chave: reproduz uma conversa gravada
saída: {codes.OK} ok | {codes.UNEXPECTED} erro inesperado | {codes.USAGE} uso inválido | {codes.CONFIG} configuração | {codes.UNAVAILABLE} provedor indisponível | {codes.CASSETTE} cassete | {codes.INTERRUPTED} Ctrl-C"""

TOOL_LABELS: Final = {
    "search_products": "buscando no catálogo",
    "get_product": "consultando o produto",
    "calculate_payment_options": "calculando as condições de pagamento",
    "estimate_shipping": "vendo o frete",
    "get_store_info": "conferindo os dados da loja",
    "search_policy": "consultando as políticas da loja",
    "verify_customer": "verificando os dados do pedido",
    "get_order_status": "consultando o pedido",
    "check_return_eligibility": "conferindo o prazo de troca e devolução",
    "register_complaint": "registrando o atendimento",
}

UNEXPECTED_MESSAGE: Final = "Algo deu errado do meu lado. Pode repetir a mensagem?"

_LONG_TOKEN: Final = re.compile(r"[A-Za-z0-9_\-]{20,}")


def sanitize(text: str) -> str:
    """Tira caracteres de controle e de formatação (inclusive os que invertem o texto)."""
    spaced = text.replace("\t", " ").replace("\n", " ").replace("\r", " ")
    return " ".join(
        "".join(c for c in spaced if unicodedata.category(c) not in ("Cc", "Cf")).split()
    )


def scrub(text: str, limit: int = 200) -> str:
    """Erros de SDK podem repetir pedaços de chave: mascara sequências longas e corta."""
    return _LONG_TOKEN.sub("***", text.replace("\n", " "))[:limit]


def format_moment(moment: datetime) -> str:
    return f"{weekday_pt(moment.date())}, {moment:%d/%m/%Y %H:%M}"
