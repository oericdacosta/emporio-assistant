from __future__ import annotations

from datetime import timedelta

from emporio.clock import FixedClock, SystemClock


def test_fixed_clock_parse_advance_and_default_timezone() -> None:
    clock = FixedClock.parse("2026-03-23T10:00")
    assert clock.now().utcoffset() == timedelta(hours=-4)  # naive -> America/Campo_Grande
    clock.advance(days=1, hours=2)
    assert clock.now().isoformat() == "2026-03-24T12:00:00-04:00"


def test_system_clock_is_timezone_aware() -> None:
    assert SystemClock().now().utcoffset() == timedelta(hours=-4)
