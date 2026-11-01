"""Prompt de sistema do Téo.

O texto fixo fica em ``prompts/system.md`` e vem primeiro, para o início do prompt ser igual em
toda chamada (cache de prompt). O ``<contexto_atual>`` vem por último e é montado aqui: a data e a
hora vêm do relógio injetado, o horário da loja vem de ``domain.hours`` e o nome só aparece
quando o cliente foi verificado. O prompt nunca calcula horário.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Final

from emporio.domain.hours import StoreStatus, weekday_pt

PLACEHOLDER: Final = "{contexto_atual}"
_PROMPT_FILE: Final = Path(__file__).resolve().parents[1] / "prompts" / "system.md"


@cache
def _static_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8").rstrip("\n")


def _first_name(name: str) -> str:
    """Só letras, para o nome nunca carregar instrução para dentro do prompt."""
    return re.sub(r"[^\w'-]", "", name.split()[0] if name.split() else "")[:40]


def _when(moment: datetime) -> str:
    return f"{weekday_pt(moment.date())}, {moment:%d/%m} às {moment:%H:%M}"


def build_system_prompt(
    now: datetime, status: StoreStatus, customer_first_name: str | None = None
) -> str:
    lines = [
        "<contexto_atual>",
        f"Agora: {weekday_pt(now.date())}, {now:%d/%m/%Y %H:%M} (horário de Campo Grande).",
    ]
    if status.is_open and status.closes_at:
        lines.append(f"Loja aberta agora; fecha hoje às {status.closes_at:%H:%M}.")
    elif status.next_open:
        lines.append(f"Loja fechada agora; reabre {_when(status.next_open)}.")
    if customer_first_name and (name := _first_name(customer_first_name)):
        lines.append(f"Cliente verificado: {name}.")
    lines.append("</contexto_atual>")
    return _static_prompt().replace(PLACEHOLDER, "\n".join(lines)) + "\n"
