from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import pytest

from emporio.domain.hours import holiday_name, hours_on, store_status, weekday_pt


def test_holiday_names_come_from_the_national_and_state_calendar() -> None:
    assert holiday_name(date(2026, 11, 2)) == "Finados"
    assert date(2026, 11, 2).weekday() == 0  # Finados cai numa segunda
    assert holiday_name(date(2026, 4, 3)) == "Sexta-feira Santa"
    assert holiday_name(date(2026, 3, 23)) is None


def test_holidays_work_for_other_years() -> None:
    assert holiday_name(date(2027, 12, 25)) == "Natal"
    assert holiday_name(date(2030, 11, 2)) == "Finados"


@pytest.mark.parametrize(
    ("moment", "is_open", "next_open"),
    [
        ((2026, 3, 23, 9, 0), True, None),  # segunda, abre às 09:00 (intervalo fechado à esquerda)
        ((2026, 3, 23, 8, 59), False, (2026, 3, 23, 9, 0)),
        ((2026, 3, 23, 17, 59), True, None),
        ((2026, 3, 23, 18, 0), False, (2026, 3, 24, 9, 0)),  # 18:00 já fechou
        ((2026, 10, 31, 10, 0), True, None),  # sábado
        ((2026, 10, 31, 13, 0), False, (2026, 11, 3, 9, 0)),  # seg 02/11 é Finados -> terça
        ((2026, 3, 28, 13, 0), False, (2026, 3, 30, 9, 0)),  # sábado 13:00 -> segunda
        ((2026, 3, 29, 11, 0), False, (2026, 3, 30, 9, 0)),  # domingo
        ((2026, 4, 2, 18, 0), False, (2026, 4, 4, 9, 0)),  # quinta -> sexta santa -> sábado
        ((2026, 12, 25, 10, 0), False, (2026, 12, 26, 9, 0)),  # Natal (sexta) -> sábado
    ],
)
def test_store_status(
    at: Callable[..., datetime],
    moment: tuple[int, ...],
    is_open: bool,
    next_open: tuple[int, ...] | None,
) -> None:
    status = store_status(at(*moment))
    assert status.is_open is is_open
    assert (status.next_open == at(*next_open)) if next_open else status.next_open is None
    assert (status.closes_at is not None) is is_open


def test_status_labels(at: Callable[..., datetime]) -> None:
    assert store_status(at(2026, 3, 23, 10)).today == "09:00-18:00"
    assert store_status(at(2026, 3, 28, 10)).today == "09:00-13:00"
    assert store_status(at(2026, 3, 29, 10)).today == "fechado (domingo)"
    assert store_status(at(2026, 11, 2, 10)).today == "fechado (Finados)"


def test_store_status_converts_other_timezones(at: Callable[..., datetime]) -> None:
    from datetime import UTC

    utc = datetime(2026, 3, 23, 13, 0, tzinfo=UTC)  # 09:00 em Campo Grande (UTC-4)
    assert store_status(utc).is_open
    assert not store_status(datetime(2026, 3, 23, 12, 59, tzinfo=UTC)).is_open


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="fuso"):
        store_status(datetime(2026, 3, 23, 10))  # noqa: DTZ001


def test_helpers() -> None:
    assert hours_on(date(2026, 11, 20)) is None  # Consciência Negra (sexta)
    assert weekday_pt(date(2026, 10, 31)) == "sábado"
