"""Typed, offline evidence primitives for the KLAX V3 research program.

This module contains no acquisition code.  It validates records that have
already been downloaded and turns genuinely as-of ensemble trajectories into
an empirical distribution over the integer Fahrenheit value used for contract
settlement.  A future forecast valid time is allowed; its initialization and
availability timestamps must still precede the simulated decision.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
import re
from typing import Iterable, Mapping

from .domain import ContractBounds, climate_day_bounds, round_fahrenheit


SUPPORTED_FORECAST_MODELS = frozenset({"gfs", "nbm", "hrrr", "gefs"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _finite(value: float, name: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _sha256(value: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class ForecastPoint:
    """One immutable, timestamped temperature from a historical forecast."""

    model: str
    member_id: str
    initialized_at: datetime
    available_at: datetime
    valid_at: datetime
    temperature_f: float
    source_sha256: str
    extraction_id: str

    def __post_init__(self) -> None:
        if self.model not in SUPPORTED_FORECAST_MODELS:
            raise ValueError("Unsupported forecast model")
        if not isinstance(self.member_id, str) or not self.member_id or len(self.member_id) > 64:
            raise ValueError("Invalid forecast member identifier")
        initialized = _aware_utc(self.initialized_at, "initialized_at")
        available = _aware_utc(self.available_at, "available_at")
        valid = _aware_utc(self.valid_at, "valid_at")
        if available < initialized:
            raise ValueError("Forecast availability precedes initialization")
        if valid < initialized:
            raise ValueError("Forecast valid time precedes initialization")
        object.__setattr__(self, "initialized_at", initialized)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "valid_at", valid)
        object.__setattr__(self, "temperature_f", _finite(self.temperature_f, "temperature_f"))
        object.__setattr__(self, "source_sha256", _sha256(self.source_sha256))
        if not isinstance(self.extraction_id, str) or not self.extraction_id or len(self.extraction_id) > 128:
            raise ValueError("Invalid extraction identifier")

    @property
    def ensemble_key(self) -> tuple[str, str, datetime]:
        return self.model, self.member_id, self.initialized_at

    def require_as_of(self, decision_at: datetime) -> None:
        decision = _aware_utc(decision_at, "decision_at")
        if self.initialized_at > decision or self.available_at > decision:
            raise ValueError("Forecast was not available at the decision cutoff")


@dataclass(frozen=True)
class AsOfObservation:
    """One station observation with availability and meteorological context."""

    station: str
    observed_at: datetime
    issued_at: datetime
    available_at: datetime
    temperature_f: float
    dewpoint_f: float | None
    wind_direction_degrees: float | None
    wind_speed_kt: float | None
    pressure_hpa: float | None
    cloud_ceiling_ft: float | None
    visibility_miles: float | None
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.station, str) or re.fullmatch(r"[A-Z0-9]{4}", self.station) is None:
            raise ValueError("Station must be a four-character identifier")
        observed = _aware_utc(self.observed_at, "observed_at")
        issued = _aware_utc(self.issued_at, "issued_at")
        available = _aware_utc(self.available_at, "available_at")
        if issued < observed or available < issued:
            raise ValueError("Observation timestamp order is inconsistent")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "temperature_f", _finite(self.temperature_f, "temperature_f"))
        for name in ("dewpoint_f", "wind_direction_degrees", "wind_speed_kt", "pressure_hpa",
                     "cloud_ceiling_ft", "visibility_miles"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name))
        if self.dewpoint_f is not None and self.dewpoint_f > self.temperature_f + 0.5:
            raise ValueError("Dew point materially exceeds temperature")
        if self.wind_direction_degrees is not None and not 0 <= self.wind_direction_degrees <= 360:
            raise ValueError("Wind direction is outside 0..360 degrees")
        if self.wind_speed_kt is not None and self.wind_speed_kt < 0:
            raise ValueError("Wind speed cannot be negative")
        if self.pressure_hpa is not None and not 800 <= self.pressure_hpa <= 1100:
            raise ValueError("Station pressure is outside the supported physical range")
        if self.cloud_ceiling_ft is not None and self.cloud_ceiling_ft < 0:
            raise ValueError("Cloud ceiling cannot be negative")
        if self.visibility_miles is not None and self.visibility_miles < 0:
            raise ValueError("Visibility cannot be negative")
        object.__setattr__(self, "source_sha256", _sha256(self.source_sha256))

    def require_as_of(self, decision_at: datetime) -> None:
        decision = _aware_utc(decision_at, "decision_at")
        if self.observed_at > decision or self.available_at > decision:
            raise ValueError("Observation was not available at the decision cutoff")


@dataclass(frozen=True)
class SettlementTarget:
    """Auditable official daily-high label; never a forecast input."""

    climate_date: date
    station: str
    reported_high_f: int
    report_issued_at: datetime
    report_available_at: datetime
    settlement_at: datetime
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.climate_date, date) or isinstance(self.climate_date, datetime):
            raise ValueError("climate_date must be a date")
        if self.station != "KLAX":
            raise ValueError("V3 settlement targets are restricted to KLAX")
        if type(self.reported_high_f) is not int or not -100 <= self.reported_high_f <= 150:
            raise ValueError("Invalid integer Fahrenheit settlement label")
        issued = _aware_utc(self.report_issued_at, "report_issued_at")
        available = _aware_utc(self.report_available_at, "report_available_at")
        settled = _aware_utc(self.settlement_at, "settlement_at")
        if available < issued or settled < available:
            raise ValueError("Settlement source timestamps are inconsistent")
        _, day_end = climate_day_bounds(self.climate_date)
        if issued < day_end:
            raise ValueError("A complete daily report cannot predate the climate-day end")
        object.__setattr__(self, "report_issued_at", issued)
        object.__setattr__(self, "report_available_at", available)
        object.__setattr__(self, "settlement_at", settled)
        object.__setattr__(self, "source_sha256", _sha256(self.source_sha256))

    def binary_outcome(self, bounds: ContractBounds) -> int:
        return int(bounds.contains(self.reported_high_f))


@dataclass(frozen=True)
class EmpiricalSettlementDistribution:
    """Discrete probability mass over the reported integer daily high."""

    probabilities: Mapping[int, float]
    member_count: int
    maximum_sampling_gap_hours: float

    def __post_init__(self) -> None:
        if type(self.member_count) is not int or self.member_count < 1:
            raise ValueError("member_count must be positive")
        gap = _finite(self.maximum_sampling_gap_hours, "maximum_sampling_gap_hours")
        if gap <= 0:
            raise ValueError("maximum_sampling_gap_hours must be positive")
        normalized: dict[int, float] = {}
        total = 0.0
        for label, probability in self.probabilities.items():
            if type(label) is not int:
                raise ValueError("Settlement distribution labels must be integers")
            value = _finite(probability, "probability")
            if value < 0 or value > 1:
                raise ValueError("Settlement probability is outside 0..1")
            normalized[label] = value
            total += value
        if not normalized or abs(total - 1.0) > 1e-12:
            raise ValueError("Settlement probabilities must sum to one")
        object.__setattr__(self, "probabilities", dict(sorted(normalized.items())))
        object.__setattr__(self, "maximum_sampling_gap_hours", gap)

    def probability(self, bounds: ContractBounds, side: str = "YES") -> float:
        if side not in {"YES", "NO"}:
            raise ValueError("side must be YES or NO")
        yes = sum(value for label, value in self.probabilities.items() if bounds.contains(label))
        return yes if side == "YES" else 1.0 - yes

    def to_dict(self) -> dict:
        return {
            "distribution_family": "empirical_ensemble_daily_max",
            "member_count": self.member_count,
            "maximum_sampling_gap_hours": self.maximum_sampling_gap_hours,
            "integer_fahrenheit_probabilities": [
                {"reported_high_f": label, "probability": probability}
                for label, probability in self.probabilities.items()
            ],
        }


def empirical_daily_max_distribution(
    points: Iterable[ForecastPoint],
    climate_date: date,
    decision_at: datetime,
    *,
    maximum_sampling_gap: timedelta = timedelta(hours=3),
    minimum_members: int = 2,
) -> EmpiricalSettlementDistribution:
    """Build equal-member settlement mass from complete, as-of trajectories.

    Coverage is checked from the fixed-PST climate-day start through its end.
    The result is exact for the supplied trajectory samples, while the recorded
    sampling gap keeps it from being described as a continuous-time maximum.
    """
    if not isinstance(climate_date, date) or isinstance(climate_date, datetime):
        raise ValueError("climate_date must be a date")
    decision = _aware_utc(decision_at, "decision_at")
    if not isinstance(maximum_sampling_gap, timedelta) or maximum_sampling_gap <= timedelta(0):
        raise ValueError("maximum_sampling_gap must be positive")
    if type(minimum_members) is not int or minimum_members < 1:
        raise ValueError("minimum_members must be positive")
    start, end = climate_day_bounds(climate_date)
    grouped: dict[tuple[str, str, datetime], list[ForecastPoint]] = defaultdict(list)
    for point in points:
        if not isinstance(point, ForecastPoint):
            raise ValueError("All trajectory records must be ForecastPoint values")
        point.require_as_of(decision)
        if not start <= point.valid_at < end:
            raise ValueError("Forecast point lies outside the target climate day")
        grouped[point.ensemble_key].append(point)
    if len(grouped) < minimum_members:
        raise ValueError("Insufficient independent ensemble members")

    maxima: list[float] = []
    for key, member_points in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        ordered = sorted(member_points, key=lambda point: point.valid_at)
        times = [point.valid_at for point in ordered]
        if len(set(times)) != len(times):
            raise ValueError(f"Duplicate valid time in ensemble member {key[1]}")
        coverage_gaps = [times[0] - start, end - times[-1]]
        coverage_gaps.extend(right - left for left, right in zip(times, times[1:]))
        if max(coverage_gaps) > maximum_sampling_gap:
            raise ValueError(f"Incomplete climate-day trajectory for ensemble member {key[1]}")
        maxima.append(max(point.temperature_f for point in ordered))

    counts = Counter(round_fahrenheit(value) for value in maxima)
    total = len(maxima)
    probabilities = {label: count / total for label, count in counts.items()}
    return EmpiricalSettlementDistribution(
        probabilities,
        member_count=total,
        maximum_sampling_gap_hours=maximum_sampling_gap.total_seconds() / 3600,
    )
