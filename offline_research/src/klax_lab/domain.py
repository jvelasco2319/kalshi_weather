"""Offline KLAX target semantics; no source acquisition or settlement assumptions.

The fixed-standard-time window and nearest-integer-Fahrenheit mapping are explicit
research conventions. A historical market must have its own rules verified before
using them. Gaussian parameters describe latent Fahrenheit, not rounded outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import ceil, erfc, floor, isfinite, sqrt


PST = timezone(timedelta(hours=-8), name="PST")
UTC = timezone.utc


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def climate_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the half-open [start, end) UTC interval for a fixed-PST day.

    This is always 24 hours, including civil daylight-saving transition days.
    The first instant is 08:00 UTC; on summer civil clocks it is 01:00 PDT.
    """
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValueError("day must be a date, not a datetime")
    start = datetime.combine(day, time.min, PST).astimezone(UTC)
    return start, start + timedelta(days=1)


def interval_within_climate_day(start: datetime, end: datetime, day: date) -> bool:
    """Whether a forecast interval is fully contained; never clip interval maxima."""
    start, end = _aware(start, "start"), _aware(end, "end")
    if end <= start:
        raise ValueError("interval end must follow start")
    window_start, window_end = climate_day_bounds(day)
    return window_start <= start and end <= window_end


def require_as_of(
    observed_at: datetime,
    available_at: datetime,
    decision_at: datetime,
    *,
    issued_at: datetime | None = None,
) -> None:
    """Reject unavailable observations, including delayed releases and revisions.

    Each revision needs its own availability timestamp. These checks establish
    timestamp consistency only; callers must supply independently audited times.
    Do not pass forecast *valid time* as observed_at: future forecasts require
    their issue/release time to be checked, not their future target time.
    """
    observed = _aware(observed_at, "observed_at")
    available = _aware(available_at, "available_at")
    decision = _aware(decision_at, "decision_at")
    if available < observed:
        raise ValueError("available_at precedes observed_at")
    if issued_at is not None and _aware(issued_at, "issued_at") > available:
        raise ValueError("issued_at follows available_at")
    if observed > decision or available > decision:
        raise ValueError("observation was not available at the decision cutoff")


def as_of_eligible(
    observed_at: datetime,
    available_at: datetime,
    decision_at: datetime,
    *,
    issued_at: datetime | None = None,
) -> bool:
    """Boolean counterpart to require_as_of; malformed timestamps also fail."""
    try:
        require_as_of(observed_at, available_at, decision_at, issued_at=issued_at)
    except ValueError:
        return False
    return True


def round_fahrenheit(value: float | Decimal) -> int:
    """Explicit nearest-integer, ties-away-from-zero convention for labels.

    Gaussian probability is continuous, so the half-degree tie convention does
    not affect its mass. Do not use this to overwrite a provider's settled label.
    """
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("temperature must be finite")
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ContractBounds:
    """Bounds on the *reported integer* Fahrenheit result; None is an open tail.

    Examples: above 87 -> ContractBounds(87, None, lower_inclusive=False).
    From 75 through 76 -> ContractBounds(75, 76), inclusive by default.
    Numeric bounds can be noninteger; inclusion is resolved in integer space.
    """

    lower_f: float | None = None
    upper_f: float | None = None
    lower_inclusive: bool = True
    upper_inclusive: bool = True

    def __post_init__(self) -> None:
        for value in (self.lower_f, self.upper_f):
            if value is not None and not isfinite(value):
                raise ValueError("contract bounds must be finite or None")
        if self.lower_f is not None and self.upper_f is not None and self.lower_f > self.upper_f:
            raise ValueError("lower contract bound exceeds upper bound")

    def integer_bounds(self) -> tuple[int | None, int | None]:
        lower = None if self.lower_f is None else (
            ceil(self.lower_f) if self.lower_inclusive else floor(self.lower_f) + 1
        )
        upper = None if self.upper_f is None else (
            floor(self.upper_f) if self.upper_inclusive else ceil(self.upper_f) - 1
        )
        return lower, upper

    def contains(self, reported_f: int) -> bool:
        if isinstance(reported_f, bool) or not isinstance(reported_f, int):
            raise ValueError("reported_f must be an integer settlement label")
        lower, upper = self.integer_bounds()
        return (lower is None or reported_f >= lower) and (upper is None or reported_f <= upper)

    def probability(self, mean_f: float, sd_f: float, side: str = "YES") -> float:
        """Integrate Gaussian mass after nearest-degree rounding; complement for NO."""
        if not isfinite(mean_f) or not isfinite(sd_f) or sd_f <= 0:
            raise ValueError("Gaussian mean must be finite and sd must be positive")
        if side not in ("YES", "NO"):
            raise ValueError("side must be YES or NO")
        lower, upper = self.integer_bounds()
        if lower is not None and upper is not None and lower > upper:
            probability = 0.0
        else:
            lo = float("-inf") if lower is None else (lower - 0.5 - mean_f) / sd_f
            hi = float("inf") if upper is None else (upper + 0.5 - mean_f) / sd_f
            # Complementary error functions preserve small upper-tail intervals.
            if lo >= 0:
                probability = 0.5 * (erfc(lo / sqrt(2)) - erfc(hi / sqrt(2)))
            else:
                probability = 0.5 * (erfc(-hi / sqrt(2)) - erfc(-lo / sqrt(2)))
            probability = min(1.0, max(0.0, probability))
        return probability if side == "YES" else 1.0 - probability
