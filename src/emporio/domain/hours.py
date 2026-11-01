"""Horário de funcionamento da loja e a próxima abertura.

É o único lugar que calcula se a loja está aberta: quem precisar dessa informação usa o
resultado daqui, sem refazer a conta. Os feriados vêm da biblioteca ``holidays`` (feriados
nacionais e do estado de Mato Grosso do Sul), então funcionam para qualquer ano. Feriados
municipais, como o aniversário de Campo Grande, não entram.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Final

import holidays

from emporio.clock import TZ
from emporio.domain.rules import RULES

_WEEKDAYS_PT: Final = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
_SHORT_NAMES: Final = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
# weekday() -> (abre, fecha); dia sem faixa = fechado. Intervalo [abre, fecha).
_HOURS: Final[dict[int, tuple[time, time]]] = {
    day: (window.open, window.close) for window in RULES.hours for day in window.weekdays
}


def _schedule_text() -> str:
    parts = []
    for window in RULES.hours:
        days = sorted(window.weekdays)
        contiguous = days == list(range(days[0], days[-1] + 1)) and len(days) > 1
        names = (
            f"{_SHORT_NAMES[days[0]]} a {_SHORT_NAMES[days[-1]]}"
            if contiguous
            else " e ".join(_SHORT_NAMES[d] for d in days)
        )
        parts.append(f"{names} {window.open:%H:%M}-{window.close:%H:%M}")
    closed = [_SHORT_NAMES[d] for d in range(7) if d not in _HOURS]
    parts.append(" e ".join([*closed, "feriados"]) + " fechado")
    return "; ".join(parts)


SCHEDULE_TEXT: Final = _schedule_text()


def weekday_pt(day: date) -> str:
    return _WEEKDAYS_PT[day.weekday()]


@lru_cache(maxsize=8)
def _calendar(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("BR", subdiv="MS", years=year, language="pt_BR")


def holiday_name(day: date) -> str | None:
    """Nome do feriado nacional ou estadual (MS) do dia, ou ``None`` se não houver."""
    return _calendar(day.year).get(day)


def hours_on(day: date) -> tuple[time, time] | None:
    """(abre, fecha) do dia, ou ``None`` se fechado (domingo ou feriado)."""
    return None if holiday_name(day) else _HOURS.get(day.weekday())


@dataclass(frozen=True, slots=True)
class StoreStatus:
    is_open: bool
    now: datetime
    today: str  # "09:00-18:00" ou "fechado (<motivo>)"
    closes_at: datetime | None  # preenchido quando aberta
    next_open: datetime | None  # preenchido quando fechada


def _span(opens: time, closes: time) -> str:
    return f"{opens:%H:%M}-{closes:%H:%M}"


def store_status(now: datetime) -> StoreStatus:
    """Estado da loja em ``now`` (datetime com fuso; convertido para America/Campo_Grande)."""
    if now.tzinfo is None:
        raise ValueError("now deve ter fuso horário (use o Clock injetado)")
    local = now.astimezone(TZ)
    today = local.date()
    window = hours_on(today)
    if window and window[0] <= local.time() < window[1]:
        closes = datetime.combine(today, window[1], tzinfo=TZ)
        return StoreStatus(True, local, _span(*window), closes, None)

    day = today if window and local.time() < window[0] else today + timedelta(days=1)
    while (opening := hours_on(day)) is None:
        day += timedelta(days=1)
    next_open = datetime.combine(day, opening[0], tzinfo=TZ)
    label = _span(*window) if window else f"fechado ({holiday_name(today) or weekday_pt(today)})"
    return StoreStatus(False, local, label, None, next_open)
