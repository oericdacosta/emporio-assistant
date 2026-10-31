"""Relógio injetável e fuso da loja. Só ``SystemClock`` lê o relógio real.

Campo Grande fica em UTC-4 o ano todo (não há horário de verão desde 2019). As regras de
prazo e de horário recebem a data já calculada a partir de um ``Clock``, o que permite
fixar o "agora" em testes e em exemplos reprodutíveis.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo

TZ: Final = ZoneInfo("America/Campo_Grande")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(TZ)


class FixedClock:
    """Relógio congelado em um instante, para testes e exemplos reprodutíveis."""

    def __init__(self, moment: datetime) -> None:
        self._now = moment if moment.tzinfo else moment.replace(tzinfo=TZ)

    @classmethod
    def parse(cls, iso: str) -> FixedClock:
        return cls(datetime.fromisoformat(iso))

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)
