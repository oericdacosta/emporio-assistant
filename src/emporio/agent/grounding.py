"""Guarda de fundamentação: bloqueia números e códigos que nenhuma fonte confiável do turno produziu.

O ``Ledger`` de um turno contém o que o sistema já "disse":
  1. todos os resultados de tool do turno, serializados em JSON e varridos por regex;
  2. valores citados pelo usuário na conversa ("até R$1000", "1k", "uns 2 mil");
  3. respostas anteriores do assistente já aprovadas pela guarda.
A resposta candidata é varrida pelos mesmos padrões; o que não está no ledger é violação:
  * valores em R$ (``R$ 1.234,56``, ``1234 reais``, ``2 mil reais``);
  * percentuais (``18%``);  * parcelamentos (``12x``);  * códigos de rastreio.
No grafo do agente (``graph.py``): violações -> 1 retry com ``correction_message`` -> ``FALLBACK_MESSAGE``.

Contrato das tools (conferido por ``contract_violations`` em teste): todo campo ``*_brl`` é
string ``"R$ x,xx"`` (``domain.pricing.brl``), todo ``*_percent`` é string ``"18%"`` e o plano de
parcelas traz ``"12x"`` em texto. Um número cru em campo monetário seria invisível à guarda.

Limites: não cobre números por extenso ("dois mil" na resposta), prazos em dias, estoque,
datas, nem associação trocada (preço do produto A atribuído ao B). Valores que o modelo derive
sozinho (somas) são barrados de propósito: cálculo é papel de ``domain/``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, Final, Self

from emporio.domain.store import WHATSAPP
from emporio.identity import DENY_CODE

_MONEY_NUM: Final = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?"
_BRL: Final = re.compile(rf"R\$\s?{_MONEY_NUM}")
_REAIS: Final = re.compile(rf"(?<![\w.,]){_MONEY_NUM}\s*reais\b", re.IGNORECASE)
_MIL_REAIS: Final = re.compile(r"(?<![\w.,])(\d{1,3})(?:,(\d))?\s*mil\s+reais\b", re.IGNORECASE)
_PERCENT: Final = re.compile(r"(?<![\w.,])(\d{1,3})(?:,(\d{1,2}))?\s?%")
_INSTALLMENTS: Final = re.compile(r"(?<![\w.,])(\d{1,2})\s?x(?!\w)", re.IGNORECASE)
_TRACK: Final = re.compile(r"\bBR[A-Z0-9]{9}BR\b", re.IGNORECASE)
_TICKET: Final = re.compile(r"\bEMP-[A-F0-9]{8}\b", re.IGNORECASE)
# estado do sistema que só uma tool do turno pode afirmar: "não consegui confirmar seus dados"
_CHECK_VERB: Final = r"(?:confirmar|verificar|validar|localizar|encontrar)"
_VERIFY_FAIL: Final = re.compile(
    rf"n[ãa]o\s+consegu(?:i|imos)\s+(?:\w+\s+){{0,2}}?{_CHECK_VERB}"
    rf"|n[ãa]o\s+foi\s+poss[íi]vel\s+(?:\w+\s+){{0,2}}?{_CHECK_VERB}"
    r"|s[óo]\s+com\s+o\s+n[úu]mero\s+(?:\w+\s+){0,2}?n[ãa]o\s+consigo",
    re.IGNORECASE,
)
# pedir o número do pedido que o cliente JÁ deu (só é violação com ``Ledger.order_known``)
_MISSING_ORDER: Final = re.compile(
    r"falta\s+(?:o\s+)?n[úu]mero\s+do\s+pedido\s+para\s+(?:eu\s+)?(?:confirmar|verificar)",
    re.IGNORECASE,
)
# a frase só vale como falha de identidade se falar de dados do cliente: "não consegui confirmar o
# estoque" é outra coisa e não é barrada
_IDENTITY_WORDS: Final = re.compile(
    r"dados|pedido|e-?mail|telefone|identidade|cadastro|n[úu]mero", re.IGNORECASE
)
_SENTENCE_EDGE: Final = re.compile(r"[.!?\n]")
# nomes e e-mails fictícios dos <exemplo> de prompts/system.md (um teste confere que cobrem o arquivo)
LEAKED_EXAMPLES: Final = (
    "Violão Modelo Exemplo",
    "Violão Outro Exemplo",
    "Modelo Exemplo",
    "Outro Exemplo",
    "fulano@exemplo.com",
    "@exemplo.com",
)
# moedas que a loja não pratica: "US$ 399,82", "399 dólares", "€ 10", "10 euros"
_FOREIGN: Final = re.compile(
    rf"(?:US\$|USD|€|EUR)\s?{_MONEY_NUM}|(?<![\w.,]){_MONEY_NUM}\s*(?:US\$|USD|€|EUR|d[óo]lar(?:es)?|euros?)(?!\w)",
    re.IGNORECASE,
)
_BRL_FORMAT: Final = re.compile(r"R\$ \d{1,3}(?:\.\d{3})*,\d{2}")

# Valores digitados pelo usuário: "R$1000", "1k", "2.5k", "1,5 mil", "500 reais", "mil", "dois mil".
_USER_NUM: Final = re.compile(
    r"(?<![\w,.])(?P<rs>R\$\s?)?(?P<int>\d{1,3}(?:\.\d{3})+|\d+)"
    r"(?:,(?P<c1>\d{1,2})|\.(?P<c2>\d{1,2})(?!\d))?"
    r"(?:\s*(?P<suf>k|mil|reais?)(?!\w))?",
    re.IGNORECASE,
)
_WORD_MIL: Final = re.compile(
    r"(?<!\w)(?:(?P<w>um|dois|duas|tres|três|quatro|cinco|seis|sete|oito|nove|dez)\s+)?mil(?!\w)",
    re.IGNORECASE,
)
_WORDS: Final = {
    "um": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "três": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
}
_RANGE_GLUE: Final = {" e ", " a ", " à ", " ou ", " até ", "-", " - ", " ate "}
_CENT: Final = Decimal("0.01")

FALLBACK_MESSAGE: Final = (
    "Opa, não consegui confirmar esses valores agora e prefiro não te passar nada errado. "
    f"Pode me chamar no WhatsApp {WHATSAPP} que a equipe confirma pra você."
)


def fallback_from_tools(results: list[tuple[str, dict[str, Any]]]) -> str:
    """Resposta segura a partir do que as tools do turno devolveram: nome e preços como vieram.

    Nunca calcula nem formata valor; sem nada aproveitável, devolve ``FALLBACK_MESSAGE``.
    """
    seen: dict[str, str] = {}
    for name, result in results:
        data = result.get("data") if result.get("ok") else None
        if not isinstance(data, dict):
            continue
        if name == "get_product" and data.get("name") and data.get("effective_price_brl"):
            seen[data["name"]] = _priced(data["effective_price_brl"], data.get("pix_price_brl"))
        elif name == "search_products":
            for item in data.get("items") or []:
                seen[item["name"]] = _priced(item["effective_price_brl"], None)
        elif name == "calculate_payment_options" and data.get("base_price_brl"):
            label = data.get("product") or "O valor consultado"
            seen[label] = _priced(data["base_price_brl"], data.get("pix_total_brl"))
        elif name == "calculate_cart_total" and data.get("total_brl"):
            seen["Total do carrinho"] = _priced(data["total_brl"], data.get("pix_total_brl"))
    if not seen:
        return FALLBACK_MESSAGE
    listed = "; ".join(f"{label}: {price}" for label, price in list(seen.items())[:4])
    return (
        f"Não consegui montar a resposta completa agora, mas o que a consulta trouxe foi: {listed}. "
        f"Pra detalhes e pra fechar, chama a equipe no WhatsApp {WHATSAPP}."
    )


def _priced(price: str, pix: str | None) -> str:
    return f"{price}, ou {pix} no PIX" if pix else price


def _money(int_part: str, cents: str | None) -> Decimal:
    return Decimal(f"{int_part.replace('.', '')}.{(cents or '00').ljust(2, '0')}")


def _pct(whole: str, frac: str | None) -> Decimal:
    return Decimal(f"{whole}.{frac or '0'}").normalize()


def parse_user_amounts(text: str) -> set[Decimal]:
    """Valores em reais que o usuário citou, em qualquer grafia comum (ver ``_USER_NUM``)."""
    found: set[Decimal] = set()
    taken: list[tuple[int, int]] = []
    matches = list(_USER_NUM.finditer(text))
    for i, m in enumerate(matches):
        suffix = (m["suf"] or "").lower()
        cents = m["c1"] or m["c2"]
        base = _money(m["int"], cents)
        if suffix in {"k", "mil"}:
            base *= 1000
        elif not (m["rs"] or suffix.startswith("reai")) and base < 100:
            # número solto e pequeno ("1" em "entre 1 e 2 mil"): só vale herdando o sufixo do vizinho
            nxt = matches[i + 1] if i + 1 < len(matches) else None
            glue = text[m.end() : nxt.start()].lower() if nxt else ""
            if nxt and (nxt["suf"] or "").lower() in {"k", "mil"} and glue in _RANGE_GLUE:
                base *= 1000
            else:
                continue
        found.add(base.quantize(_CENT))
        taken.append(m.span())
    for m in _WORD_MIL.finditer(text):
        if not any(a <= m.start() < b for a, b in taken):  # "mil" que não é sufixo de dígitos
            found.add(Decimal(_WORDS.get((m["w"] or "").lower(), 1) * 1000).quantize(_CENT))
    return found


class Ledger:
    """Tudo que o sistema já "disse" neste turno: valores, percentuais, parcelamentos, rastreios."""

    def __init__(self) -> None:
        self.amounts: set[Decimal] = set()
        self.percents: set[Decimal] = set()
        self.installments: set[int] = set()
        self.tracking: set[str] = set()
        self.tickets: set[str] = set()
        self.foreign: set[Decimal] = set()
        self.verify_failed = False  # verify_customer devolveu not_verified neste turno
        self.order_known = False  # o cliente já citou um pedido nesta conversa
        self._seen = ""  # tudo que as fontes confiáveis disseram, em minúsculas

    @classmethod
    def for_turn(
        cls,
        user_text: str,
        approved_answers: list[str] | None = None,
        working_lines: Sequence[str] = (),
    ) -> Self:
        """Ledger inicial do turno: fala do usuário + respostas anteriores aprovadas + as linhas
        do estado de trabalho que o orquestrador mostra ao modelo no ``<contexto_atual>``."""
        ledger = cls()
        ledger.absorb_user(user_text)
        for answer in [*(approved_answers or []), *working_lines]:
            ledger.absorb_text(answer)
        return ledger

    def absorb(self, tool_result: Any, name: str | None = None) -> None:
        """Absorve o resultado de uma tool: JSON serializado + regex (strings formatadas incluídas).

        Com o nome da tool, também registra o estado do sistema (falha de verificação).
        """
        if name == "verify_customer" and isinstance(tool_result, dict):
            error = tool_result.get("error")
            if isinstance(error, dict) and error.get("code") == DENY_CODE:
                self.verify_failed = True
        self.absorb_text(json.dumps(tool_result, ensure_ascii=False, default=str))

    def absorb_user(self, text: str) -> None:
        self.absorb_text(text)
        self.amounts.update(parse_user_amounts(text))

    def absorb_text(self, text: str) -> None:
        """Texto de origem confiável: resultado de tool, fala do usuário, resposta aprovada."""
        self.amounts.update(self._amounts_in(text))
        self.percents.update(_pct(m[1], m[2]) for m in _PERCENT.finditer(text))
        self.installments.update(int(m[1]) for m in _INSTALLMENTS.finditer(text))
        self.tracking.update(t.upper() for t in _TRACK.findall(text))
        self.tickets.update(t.upper() for t in _TICKET.findall(text))
        self.foreign.update(self._foreign_in(text))
        self._seen += "\n" + text.lower()

    @staticmethod
    def _foreign_in(text: str) -> set[Decimal]:
        return {
            _money(m[1] or m[3], m[2] if m[1] else m[4]).quantize(_CENT)
            for m in _FOREIGN.finditer(text)
        }

    @staticmethod
    def _amounts_in(text: str) -> set[Decimal]:
        found = {_money(m[1], m[2]) for m in _BRL.finditer(text)}
        found |= {_money(m[1], m[2]) for m in _REAIS.finditer(text)}
        found |= {Decimal(f"{m[1]}.{m[2] or '0'}") * 1000 for m in _MIL_REAIS.finditer(text)}
        return {a.quantize(_CENT) for a in found}

    def violations(self, answer: str) -> list[str]:
        """Trechos da resposta sem origem no ledger (lista vazia = aprovada)."""
        bad = [
            m.group(0)
            for pattern in (_BRL, _REAIS, _MIL_REAIS)
            for m in pattern.finditer(answer)
            if next(iter(self._amounts_in(m.group(0))), None) not in self.amounts
        ]
        bad += [
            m.group(0) for m in _PERCENT.finditer(answer) if _pct(m[1], m[2]) not in self.percents
        ]
        bad += [
            m.group(0) for m in _INSTALLMENTS.finditer(answer) if int(m[1]) not in self.installments
        ]
        bad += [t for t in _TRACK.findall(answer) if t.upper() not in self.tracking]
        bad += [t for t in _TICKET.findall(answer) if t.upper() not in self.tickets]
        if not self.verify_failed:
            bad += [m.group(0) for m in _VERIFY_FAIL.finditer(answer) if _about_identity(answer, m)]
        if self.order_known:
            bad += [m.group(0) for m in _MISSING_ORDER.finditer(answer)]
        low = answer.lower()
        bad += [t for t in LEAKED_EXAMPLES if t.lower() in low and t.lower() not in self._seen]
        bad += [
            m.group(0)
            for m in _FOREIGN.finditer(answer)
            if next(iter(self._foreign_in(m.group(0))), None) not in self.foreign
        ]
        return bad


def _about_identity(answer: str, match: re.Match[str]) -> bool:
    """A frase onde a falha de verificação aparece fala de dados do cliente?"""
    before = list(_SENTENCE_EDGE.finditer(answer, 0, match.start()))
    after = _SENTENCE_EDGE.search(answer, match.end())
    sentence = answer[before[-1].end() if before else 0 : after.start() if after else len(answer)]
    return _IDENTITY_WORDS.search(sentence) is not None


def correction_message(violations: list[str]) -> str:
    """Mensagem para o retry: aponta o que não tem origem e manda reconsultar."""
    unique = list(dict.fromkeys(violations))
    notes: list[str] = []
    if any(_VERIFY_FAIL.fullmatch(v) for v in unique):
        notes.append(
            "Você não tentou verificar neste turno: chame verify_customer com o pedido e a "
            "prova que o cliente informou; não diga que não conseguiu sem tentar."
        )
    if any(_MISSING_ORDER.fullmatch(v) for v in unique):
        notes.append(
            "O cliente já informou o pedido: não peça o número de novo. Chame verify_customer "
            "com o pedido e a prova, ou peça só a prova que falta (e-mail ou 4 últimos dígitos)."
        )
    if any(_TICKET.fullmatch(v) for v in unique):
        notes.append("Não use protocolo que a ferramenta não devolveu.")
    if any(v.lower() in (e.lower() for e in LEAKED_EXAMPLES) for v in unique):
        notes.append("Nomes e e-mails de exemplo do prompt não existem: use só dados reais.")
    if any(_FOREIGN.fullmatch(v) for v in unique):
        notes.append("A loja só trabalha em reais (R$): não cite outras moedas.")
    listed = ", ".join(unique)
    return " ".join(
        [
            f"Sua resposta continha trechos sem origem nas ferramentas: {listed}.",
            *notes,
            "Reescreva usando apenas valores retornados pelas ferramentas nesta conversa; "
            "se faltar o dado, chame a ferramenta adequada ou diga que vai confirmar.",
        ]
    )


def contract_violations(data: Any, path: str = "$") -> list[str]:
    """Contrato das tools: campos ``*_brl`` devem ser ``"R$ x,xx"`` e ``*_percent`` ``"18%"``.

    Devolve os caminhos que violam (``None`` é aceito: campo ausente ou opcional).
    """
    bad: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            where = f"{path}.{key}"
            if value is not None and isinstance(key, str) and key.endswith(("_brl", "_percent")):
                values = value if isinstance(value, list) else [value]
                pattern = _BRL_FORMAT if key.endswith("_brl") else _PERCENT
                bad += [where for v in values if not (isinstance(v, str) and pattern.fullmatch(v))]
            if isinstance(value, dict | list):
                bad += contract_violations(value, where)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            bad += contract_violations(item, f"{path}[{i}]")
    return bad
