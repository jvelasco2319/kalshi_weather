"""Structural validation for the current offline LAX evaluation policy.

Validation prevents accidental split leakage and incompatible timing settings.
It does not prove historical forecast availability, label revision provenance,
fee correctness, or protected-worker isolation; those remain separate gates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, TypeVar

from .domain import climate_day_bounds


class PolicyError(ValueError):
    """The evaluation policy is inconsistent or unsupported by this pilot."""


PolicyType = TypeVar("PolicyType", bound=Mapping[str, Any])
CORE_PARTITIONS = ("weather_training", "selection", "protected_final")
PILOT_FORECAST_LEADS = (8, 9, 12, 15, 18, 21, 24, 27, 30, 31)
UTC = timezone.utc


def _day(value: Any, name: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise PolicyError(f"{name} must be a canonical YYYY-MM-DD date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyError(f"{name} is not a valid calendar date") from exc
    if parsed.isoformat() != value:
        raise PolicyError(f"{name} is not a canonical date")
    return parsed


def _range(policy: Mapping[str, Any], name: str) -> tuple[date, date]:
    value = policy.get(name)
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PolicyError(f"{name} requires inclusive [start_date, end_date]")
    start, end = (_day(value[i], f"{name}[{i}]") for i in range(2))
    if start > end:
        raise PolicyError(f"{name} ends before it starts")
    return start, end


def _utc_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)", value
    ):
        raise PolicyError(f"{name} must be an explicit UTC ISO timestamp ending Z or +00:00")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyError(f"{name} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PolicyError(f"{name} must be timezone-aware UTC")
    return parsed.astimezone(UTC)


def _integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise PolicyError(f"{name} must be an integer {bound}")
    return value


def _decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise PolicyError(f"{name} must use an exact decimal string or integer")
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise PolicyError(f"{name} is not a decimal") from exc
    if not result.is_finite():
        raise PolicyError(f"{name} must be finite")
    return result


def validate_evaluation_policy(policy: PolicyType) -> PolicyType:
    """Validate without mutation and return the same policy for caller chaining.

    The current feature extractor is a same-date 00 UTC cycle with ten
    instantaneous forecast leads, including 08 and 31 UTC-hour boundaries. The
    `decision_hour_utc` is the feature/selection cutoff; hypothetical entry is
    later by `execution_delay_seconds`. Supporting different cycles or lead
    schedules requires coordinated extractor and policy changes.
    """
    if not isinstance(policy, Mapping):
        raise PolicyError("Evaluation policy must be a mapping")
    ranges = {name: _range(policy, name) for name in (*CORE_PARTITIONS, "climatology_training")}
    for earlier, later in zip(CORE_PARTITIONS, CORE_PARTITIONS[1:]):
        if ranges[earlier][1] >= ranges[later][0]:
            raise PolicyError(f"{earlier} and {later} must be chronologically ordered and nonoverlapping")
    if policy.get("climate_day") != "fixed_PST_UTC_minus_08":
        raise PolicyError("The LAX pilot requires a fixed-PST UTC-08 climate day")
    fit_cutoff = _utc_timestamp(policy.get("model_fit_cutoff_utc"), "model_fit_cutoff_utc")
    decision_hour = _integer(policy.get("decision_hour_utc"), "decision_hour_utc", 8, 23)
    initialization_hour = _integer(policy.get("forecast_initialization_hour_utc"), "forecast_initialization_hour_utc", 0, 23)
    if initialization_hour != 0:
        raise PolicyError("The current feature extractor supports the same-date 00 UTC cycle only")
    availability_delay = _integer(policy.get("availability_delay_hours"), "availability_delay_hours", 1, 23)
    execution_delay = _integer(policy.get("execution_delay_seconds"), "execution_delay_seconds", 0)
    _integer(policy.get("max_quote_age_seconds"), "max_quote_age_seconds", 0)
    _integer(policy.get("reference_quantity"), "reference_quantity", 1)
    _integer(policy.get("max_entries_per_weather_day"), "max_entries_per_weather_day", 1)
    if policy["max_entries_per_weather_day"] != 1:
        raise PolicyError("The registered pilot supports one selected entry per weather day")
    expected_return = _decimal(policy.get("target_expected_net_return"), "target_expected_net_return")
    if expected_return < Decimal("0.10"):
        raise PolicyError("The research screen must require at least 10% expected net return on entry outlay")

    first_selection_day = ranges["selection"][0]
    decision = datetime(first_selection_day.year, first_selection_day.month, first_selection_day.day,
                        decision_hour, tzinfo=UTC)
    if fit_cutoff >= decision:
        raise PolicyError("Model fit cutoff must precede the first selection information cutoff")
    for name in ("weather_training", "climatology_training"):
        _, final_climate_day_end = climate_day_bounds(ranges[name][1])
        if final_climate_day_end > fit_cutoff:
            raise PolicyError(f"{name}'s final climate day must finish by the fit cutoff")
    issue = decision.replace(hour=initialization_hour)
    available = issue + timedelta(hours=availability_delay)
    if available > decision:
        raise PolicyError("The forecast availability assumption follows the information cutoff")
    climate_start, climate_end = climate_day_bounds(first_selection_day)
    if not climate_start <= decision < climate_end:
        raise PolicyError("The information cutoff must lie inside the target fixed-PST day")
    if decision + timedelta(seconds=execution_delay) >= climate_end:
        raise PolicyError("Execution delay would place entry after the target climate day")

    configured_leads = policy.get("forecast_leads_hours", list(PILOT_FORECAST_LEADS))
    if not isinstance(configured_leads, (list, tuple)) or any(type(value) is not int for value in configured_leads):
        raise PolicyError("forecast_leads_hours must contain whole-hour integer leads")
    if tuple(configured_leads) != PILOT_FORECAST_LEADS:
        raise PolicyError("Forecast leads must match the ten-sample pilot including leads 8 and 31")
    for lead in configured_leads:
        if not climate_start <= issue + timedelta(hours=lead) < climate_end:
            raise PolicyError("A forecast lead falls outside the half-open climate day")
    if policy.get("bootstrap_unit") != "weather_day":
        raise PolicyError("Uncertainty must resample weather days, not correlated contracts")
    for name in ("minimum_training_days", "minimum_selected_weather_days_for_inference", "minimum_final_opportunity_days_for_inference", "bootstrap_samples"):
        _integer(policy.get(name), name, 1)
    _integer(policy.get("bootstrap_seed"), "bootstrap_seed", 0)
    return policy


# Short alias for command handlers; the longer name is the public descriptive API.
validate_policy = validate_evaluation_policy
