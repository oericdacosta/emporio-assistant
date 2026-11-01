"""Guarda de fundamentação: bloqueia números e códigos que nenhuma fonte confiável do turno produziu.

O ``Ledger`` de um turno contém o que o sistema já "disse":
  1. todos os resultados de tool do turno, serializados em JSON e varridos por regex;
  2. valores citados pelo usuário na conversa ("até R$1000", "1k", "uns 2 mil");
  3. respostas anteriores do assistente já aprovadas pela guarda.
A resposta candidata é varrida pelos mesmos padrões; o que não está no ledger é violação:
  * valores em R$ (``R$ 1.234,56``, ``1234 reais``, ``2 mil reais``);
  * percentuais (``18%``);  * parcelamentos (``12x``);  * códigos de rastreio.
No loop do agente: violações -> 1 retry com ``correction_message`` -> ``FALLBACK_MESSAGE``.

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
from decimal import Decimal
from typing import Any, Final, Self

from emporio.tools.store import WHATSAPP

_MONEY_NUM: Final = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?"
_BRL: Final = re.compile(rf"R\$\s?{_MONEY_NUM}")
_REAIS: Final = re.compile(rf"(?<![\w.,]){_MONEY_NUM}\s*reais\b", re.IGNORECASE)
_MIL_REAIS: Final = re.compile(r"(?<![\w.,])(\d{1,3})(?:,(\d))?\s*mil\s+reais\b", re.IGNORECASE)
_PERCENT: Final = re.compile(r"(?<![\w.,])(\d{1,3})(?:,(\d{1,2}))?\s?%")
_INSTALLMENTS: Final = re.compile(r"(?<![\w.,])(\d{1,2})\s?x(?!\w)", re.IGNORECASE)
_TRACK: Final = re.compile(r"\bBR[A-Z0-9]{9}BR\b", re.IGNORECASE)
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

    @classmethod
    def for_turn(cls, user_text: str, approved_answers: list[str] | None = None) -> Self:
        """Ledger inicial do turno: fala do usuário + respostas anteriores aprovadas."""
        ledger = cls()
        ledger.absorb_user(user_text)
        for answer in approved_answers or []:
            ledger.absorb_text(answer)
        return ledger

    def absorb(self, tool_result: Any) -> None:
        """Absorve o resultado de uma tool: JSON serializado + regex (strings formatadas incluídas)."""
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
        return bad


def correction_message(violations: list[str]) -> str:
    """Mensagem para o retry: aponta o que não tem origem e manda reconsultar."""
    listed = ", ".join(dict.fromkeys(violations))
    return (
        f"Sua resposta continha valores sem origem nas ferramentas: {listed}. "
        "Reescreva usando apenas valores retornados pelas ferramentas nesta conversa; "
        "se faltar o dado, chame a ferramenta adequada ou diga que vai confirmar."
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
