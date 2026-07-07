from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


STATION_DEFAULTS: dict[str, tuple[str, int]] = {
    "KLAX": ("America/Los_Angeles", -8),
    "KNYC": ("America/New_York", -5),
    "KMDW": ("America/Chicago", -6),
}


@dataclass(frozen=True)
class StationClock:
    station: str
    timezone_name: str
    standard_utc_offset_hours: int

    @classmethod
    def for_station(cls, station: str) -> "StationClock":
        normalized = station.upper()
        try:
            timezone_name, offset = STATION_DEFAULTS[normalized]
        except KeyError as exc:
            raise ValueError(f"Unknown station timezone mapping: {station}") from exc
        return cls(
            station=normalized,
            timezone_name=timezone_name,
            standard_utc_offset_hours=offset,
        )

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def standard_offset(self) -> timezone:
        return timezone(timedelta(hours=self.standard_utc_offset_hours))

    def to_station_local(self, dt_utc: datetime) -> datetime:
        if dt_utc.tzinfo is None:
            raise ValueError("dt_utc must be timezone-aware")
        return dt_utc.astimezone(self.zoneinfo)

    def parse_local_hhmm_today(self, now_utc: datetime, hhmm: str) -> datetime:
        local_now = self.to_station_local(now_utc)
        hour, minute = map(int, hhmm.split(":", 1))
        return datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            hour,
            minute,
            tzinfo=self.zoneinfo,
        )


@dataclass(frozen=True)
class NwsClimateDay:
    station_clock: StationClock
    target_date: date

    def bounds_utc(self) -> tuple[datetime, datetime]:
        """Return UTC bounds for the NWS local-standard-time climate day.

        NWS daily climate products use local standard time. For KLAX, the
        summer climate day therefore begins at 1:00 AM PDT, because midnight
        PST maps to 08:00 UTC year-round.
        """
        start = datetime.combine(
            self.target_date,
            time(0, 0),
            tzinfo=self.station_clock.standard_offset,
        ).astimezone(timezone.utc)
        return start, start + timedelta(days=1) - timedelta(microseconds=1)

    def contains(self, observation_time_utc: datetime) -> bool:
        if observation_time_utc.tzinfo is None:
            raise ValueError("observation_time_utc must be timezone-aware")
        start, end = self.bounds_utc()
        observed = observation_time_utc.astimezone(timezone.utc)
        return start <= observed <= end

