from __future__ import annotations

from datetime import date, datetime, timezone

from kalshi_weather.model.lax_high_temp import (
    current_lax_market_date,
    lax_climate_day_utc,
    remaining_lax_day_local,
)


def test_dst_midnight_boundary_uses_fixed_pacific_standard_time() -> None:
    assert current_lax_market_date(datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc)) == date(2026, 6, 19)
    assert current_lax_market_date(datetime(2026, 6, 20, 8, 30, tzinfo=timezone.utc)) == date(2026, 6, 20)


def test_winter_standard_time_market_date() -> None:
    assert current_lax_market_date(datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)) == date(2026, 1, 15)


def test_climate_day_utc_boundaries_for_june_19() -> None:
    start, end = lax_climate_day_utc(date(2026, 6, 19))

    assert start == datetime(2026, 6, 19, 8, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc)


def test_remaining_lax_day_uses_climate_day_end_wall_time() -> None:
    start, end = remaining_lax_day_local(datetime(2026, 6, 20, 7, 30, tzinfo=timezone.utc))

    assert start.isoformat() == "2026-06-20T00:30:00"
    assert end.isoformat() == "2026-06-20T01:00:00"
