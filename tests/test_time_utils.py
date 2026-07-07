from datetime import date, timezone

from kalshi_weather.time_utils import climate_day_utc


def test_pacific_standard_climate_day_utc() -> None:
    start, end = climate_day_utc(date(2026, 6, 19), -8)
    assert start.tzinfo == timezone.utc
    assert start.hour == 8
    assert end.hour == 8
    assert (end - start).total_seconds() == 24 * 60 * 60
