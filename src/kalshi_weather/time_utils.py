from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def climate_day_utc(market_date: date, standard_utc_offset_hours: int) -> tuple[datetime, datetime]:
    """Return UTC interval for an NWS local-standard-time climate day."""
    standard_tz = timezone(timedelta(hours=standard_utc_offset_hours))
    start_standard = datetime.combine(market_date, time(0, 0), tzinfo=standard_tz)
    end_standard = start_standard + timedelta(days=1)
    return start_standard.astimezone(timezone.utc), end_standard.astimezone(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fixed_standard_time(now_utc: datetime, standard_utc_offset_hours: int) -> datetime:
    """Convert UTC time to fixed local-standard time, ignoring DST."""
    standard_tz = timezone(timedelta(hours=standard_utc_offset_hours))
    return ensure_utc(now_utc).astimezone(standard_tz)


def standard_market_date(now_utc: datetime, standard_utc_offset_hours: int) -> date:
    """Return the NWS local-standard-time market date for a UTC timestamp."""
    return fixed_standard_time(now_utc, standard_utc_offset_hours).date()


def local_wall_time(now_utc: datetime, timezone_name: str) -> datetime:
    """Return naive local wall-clock time matching weather-provider local timestamps."""
    return ensure_utc(now_utc).astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
