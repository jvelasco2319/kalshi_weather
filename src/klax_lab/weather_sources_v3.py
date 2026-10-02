"""Finite historical HRRR/GEFS planning and local-observation normalization.

This module has no HTTP client and performs no acquisition.  It produces
bounded, inspectable plans for NOAA's historical AWS archives and can validate
only provenance-bearing files already present in a local cache.  Experiment
code can therefore import its typed records without creating a hidden network
path.

Archive layouts are pinned to primary source descriptions:
* NOAA HRRR product inventory and NOAA Open Data AWS registry.
* NOAA GEFS product inventory and NOAA Open Data AWS registry.
The plans still require a compatibility pilot: URL construction is not proof
that every historical object exists or was available at its nominal cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .evidence_v3 import AsOfObservation
from .provenance import write_json


UTC = timezone.utc
TRAINING_START = date(2024, 1, 1)
TRAINING_END = date(2024, 12, 31)
DEVELOPMENT_START = date(2025, 1, 5)
DEVELOPMENT_END = date(2025, 6, 30)
PROTECTED_FINAL_START = date(2025, 7, 1)

HRRR_ARCHIVE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
GEFS_ARCHIVE = "https://noaa-gefs-pds.s3.amazonaws.com"
SOURCE_DOCUMENTATION = {
    "hrrr_registry": "https://registry.opendata.aws/noaa-hrrr-pds/",
    "hrrr_inventory": "https://www.nco.ncep.noaa.gov/pmb/products/hrrr/",
    "gefs_registry": "https://github.com/awslabs/open-data-registry/blob/main/datasets/noaa-gefs.yaml",
    "gefs_inventory": "https://www.nco.ncep.noaa.gov/pmb/products/gens/",
}

INDEX_MAX_BYTES = 2_000_000
HRRR_FIELD_MAX_BYTES = 8_000_000
GEFS_TEMPERATURE_FIELD_MAX_BYTES = 4_000_000

HRRR_FIELDS = {
    "temperature_2m": ("TMP", "2 m above ground"),
    "total_cloud_cover": ("TCDC", "entire atmosphere"),
    "cloud_ceiling": ("HGT", "cloud ceiling"),
    "wind_u_10m": ("UGRD", "10 m above ground"),
    "wind_v_10m": ("VGRD", "10 m above ground"),
    "surface_pressure": ("PRES", "surface"),
    "mean_sea_level_pressure": ("MSLMA", "mean sea level"),
}
GEFS_FIELDS = {"temperature_2m": ("TMP", "2 m above ground")}
GEFS_MEMBERS = ("c00",) + tuple(f"p{number:02d}" for number in range(1, 31))
GEFS_DERIVED_PRODUCTS = ("avg", "spr")
GEFS_SOURCE_IDS = GEFS_MEMBERS + GEFS_DERIVED_PRODUCTS
LOCAL_STATIONS = frozenset({"KLAX", "KSMO", "KHHR", "KTOA", "KLGB"})


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: float | int | None, label: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _digest(value: str, label: str = "SHA-256") -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid {label}")
    return value


def _identifier(value: str, label: str, maximum: int = 128) -> str:
    if (not isinstance(value, str) or len(value) > maximum
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is None):
        raise ValueError(f"Invalid {label}")
    return value


def _safe_historical_dates(values: Iterable[date], maximum: int, *, today: date | None = None) -> tuple[date, ...]:
    dates = tuple(sorted(set(values)))
    current = today or datetime.now(UTC).date()
    if not dates or len(dates) > maximum:
        raise ValueError("Historical date plan is empty or exceeds its day budget")
    for value in dates:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError("Historical plan dates must be date values")
        if value >= current:
            raise ValueError("Only strictly historical dates may be planned")
        if value < TRAINING_START or value > DEVELOPMENT_END:
            if value >= PROTECTED_FINAL_START:
                raise ValueError("Protected-final weather dates cannot be planned")
            raise ValueError("Weather date is outside the registered training/development scope")
    return dates


@dataclass(frozen=True)
class HistoricalWeatherLimits:
    maximum_days: int = 3
    maximum_requests: int = 2500
    maximum_bytes: int = 10_000_000_000
    maximum_hrrr_leads_per_day: int = 48
    maximum_gefs_leads_per_day: int = 16
    maximum_gefs_members: int = 31

    def __post_init__(self) -> None:
        for name in (
            "maximum_days", "maximum_requests", "maximum_bytes", "maximum_hrrr_leads_per_day",
            "maximum_gefs_leads_per_day", "maximum_gefs_members",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.maximum_hrrr_leads_per_day > 49:
            raise ValueError("HRRR lead budget cannot exceed the registered 00-48 hour archive horizon")
        if self.maximum_gefs_leads_per_day > 81:
            raise ValueError("GEFS lead budget cannot exceed the registered 0-240 hour pilot horizon")
        if self.maximum_gefs_members > len(GEFS_MEMBERS):
            raise ValueError("GEFS member budget exceeds the control plus 30 perturbed members")


@dataclass(frozen=True)
class FieldSelector:
    field_id: str
    variable: str
    level: str
    maximum_bytes: int
    allowed_extra_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.field_id, "field identifier", 64)
        if not self.variable or not self.level:
            raise ValueError("Field selector requires variable and level")
        if type(self.maximum_bytes) is not int or self.maximum_bytes <= 0:
            raise ValueError("Field byte reservation must be positive")


@dataclass(frozen=True)
class ArchiveObjectPlan:
    source_id: str
    provider: str
    model: str
    initialized_at: datetime
    lead_hours: int
    member_id: str | None
    url: str
    index_url: str
    fields: tuple[FieldSelector, ...]
    index_max_bytes: int = INDEX_MAX_BYTES

    def __post_init__(self) -> None:
        _identifier(self.source_id, "source identifier")
        initialized = _aware_utc(self.initialized_at, "initialized_at")
        object.__setattr__(self, "initialized_at", initialized)
        if self.provider != "NOAA_NODD_AWS" or self.model not in {"hrrr", "gefs"}:
            raise ValueError("Unsupported archive provider or model")
        if type(self.lead_hours) is not int or self.lead_hours < 0:
            raise ValueError("Forecast lead must be a nonnegative whole hour")
        if not self.url.startswith((HRRR_ARCHIVE + "/", GEFS_ARCHIVE + "/")):
            raise ValueError("Archive object must use an approved historical NOAA host")
        if any(token in self.url.casefold() for token in ("latest", "current", "recent")):
            raise ValueError("Current/latest archive aliases are forbidden")
        if self.index_url != self.url + ".idx":
            raise ValueError("Index URL must bind to the exact GRIB2 object")
        if not self.fields or len({field.field_id for field in self.fields}) != len(self.fields):
            raise ValueError("Archive object requires distinct field selectors")
        if type(self.index_max_bytes) is not int or self.index_max_bytes <= 0:
            raise ValueError("Index byte reservation must be positive")

    @property
    def request_count(self) -> int:
        return 1 + len(self.fields)

    @property
    def maximum_bytes(self) -> int:
        return self.index_max_bytes + sum(field.maximum_bytes for field in self.fields)

    @property
    def cache_key(self) -> str:
        return self.source_id

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "provider": self.provider, "model": self.model,
            "initialized_at": self.initialized_at.isoformat(), "lead_hours": self.lead_hours,
            "member_id": self.member_id, "url": self.url, "index_url": self.index_url,
            "index_max_bytes": self.index_max_bytes,
            "fields": [
                {"field_id": field.field_id, "variable": field.variable, "level": field.level,
                 "maximum_bytes": field.maximum_bytes,
                 "allowed_extra_prefixes": list(field.allowed_extra_prefixes)}
                for field in self.fields
            ],
        }


@dataclass(frozen=True)
class HistoricalWeatherPlan:
    purpose: str
    dates: tuple[date, ...]
    objects: tuple[ArchiveObjectPlan, ...]
    limits: HistoricalWeatherLimits
    coverage_claim: str

    def __post_init__(self) -> None:
        _identifier(self.purpose, "plan purpose")
        if not self.objects or len({item.source_id for item in self.objects}) != len(self.objects):
            raise ValueError("Weather plan requires distinct archive objects")
        if any(item.initialized_at.date() not in self.dates for item in self.objects):
            raise ValueError("Archive initialization is outside the planned dates")
        if self.request_count > self.limits.maximum_requests:
            raise ValueError("Weather plan exceeds its request budget")
        if self.maximum_bytes > self.limits.maximum_bytes:
            raise ValueError("Weather plan exceeds its transfer-byte budget")

    @property
    def request_count(self) -> int:
        return sum(item.request_count for item in self.objects)

    @property
    def maximum_bytes(self) -> int:
        return sum(item.maximum_bytes for item in self.objects)

    def to_dict(self) -> dict:
        return {
            "plan_version": "klax-historical-weather-plan-v3",
            "purpose": self.purpose,
            "status": "PLANNED_NO_DATA_ACQUIRED",
            "historical_only": True,
            "cache_only_compatibility_runner": True,
            "network_client_present": False,
            "protected_final_allowed": False,
            "dates": [value.isoformat() for value in self.dates],
            "coverage_claim": self.coverage_claim,
            "source_documentation": SOURCE_DOCUMENTATION,
            "objects": [item.to_dict() for item in self.objects],
            "budget": {
                "maximum_days": self.limits.maximum_days,
                "planned_days": len(self.dates),
                "remaining_days": self.limits.maximum_days - len(self.dates),
                "maximum_requests": self.limits.maximum_requests,
                "planned_requests": self.request_count,
                "remaining_requests": self.limits.maximum_requests - self.request_count,
                "maximum_bytes": self.limits.maximum_bytes,
                "planned_maximum_bytes": self.maximum_bytes,
                "remaining_bytes": self.limits.maximum_bytes - self.maximum_bytes,
                "maximum_gefs_members": self.limits.maximum_gefs_members,
                "maximum_hrrr_leads_per_day": self.limits.maximum_hrrr_leads_per_day,
                "maximum_gefs_leads_per_day": self.limits.maximum_gefs_leads_per_day,
            },
        }


def hrrr_url(initialized_at: datetime, lead_hours: int) -> str:
    initialized = _aware_utc(initialized_at, "initialized_at")
    if initialized.minute or initialized.second or initialized.microsecond:
        raise ValueError("HRRR initialization must be on an exact UTC hour")
    if initialized.hour not in (0, 6, 12, 18):
        raise ValueError("V3 uses only HRRR extended cycles 00/06/12/18 UTC")
    if type(lead_hours) is not int or not 0 <= lead_hours <= 48:
        raise ValueError("HRRR extended-cycle lead must be 0 through 48 hours")
    day, cycle = initialized.strftime("%Y%m%d"), initialized.strftime("%H")
    return f"{HRRR_ARCHIVE}/hrrr.{day}/conus/hrrr.t{cycle}z.wrfsfcf{lead_hours:02d}.grib2"


def gefs_url(initialized_at: datetime, member_id: str, lead_hours: int) -> str:
    initialized = _aware_utc(initialized_at, "initialized_at")
    if initialized.minute or initialized.second or initialized.microsecond or initialized.hour not in (0, 6, 12, 18):
        raise ValueError("GEFS initialization must use an exact 00/06/12/18 UTC cycle")
    if member_id not in GEFS_SOURCE_IDS:
        raise ValueError("GEFS source must be c00, p01 through p30, avg, or spr")
    if type(lead_hours) is not int or not 0 <= lead_hours <= 240 or lead_hours % 3:
        raise ValueError("GEFS pilot lead must be a three-hour step from 0 through 240")
    day, cycle = initialized.strftime("%Y%m%d"), initialized.strftime("%H")
    prefix = "gec00" if member_id == "c00" else "ge" + member_id
    return (f"{GEFS_ARCHIVE}/gefs.{day}/{cycle}/atmos/pgrb2ap5/"
            f"{prefix}.t{cycle}z.pgrb2a.0p50.f{lead_hours:03d}")


def _field_selectors(source: Mapping[str, tuple[str, str]], names: Iterable[str], maximum: int,
                     *, gefs: bool = False) -> tuple[FieldSelector, ...]:
    selected = tuple(names)
    if not selected or len(set(selected)) != len(selected) or any(name not in source for name in selected):
        raise ValueError("Field plan must be a nonempty unique subset of registered fields")
    extras = ("ENS=",) if gefs else ()
    return tuple(FieldSelector(name, *source[name], maximum, extras) for name in selected)


def plan_hrrr(
        dates: Iterable[date], *, cycle_hour: int = 6, leads: Iterable[int] = range(2, 26),
        fields: Iterable[str] = tuple(HRRR_FIELDS), limits: HistoricalWeatherLimits | None = None,
        today: date | None = None, purpose: str = "hrrr_feasibility") -> HistoricalWeatherPlan:
    limits = limits or HistoricalWeatherLimits()
    safe_dates = _safe_historical_dates(dates, limits.maximum_days, today=today)
    lead_values = tuple(sorted(set(leads)))
    if not lead_values or len(lead_values) > limits.maximum_hrrr_leads_per_day:
        raise ValueError("HRRR leads are empty or exceed their per-day budget")
    selectors = _field_selectors(HRRR_FIELDS, fields, HRRR_FIELD_MAX_BYTES)
    objects = []
    for day in safe_dates:
        initialized = datetime.combine(day, time(cycle_hour), UTC)
        for lead in lead_values:
            url = hrrr_url(initialized, lead)
            source_id = f"hrrr-{day:%Y%m%d}-t{cycle_hour:02d}z-f{lead:02d}"
            objects.append(ArchiveObjectPlan(source_id, "NOAA_NODD_AWS", "hrrr", initialized,
                                             lead, None, url, url + ".idx", selectors))
    return HistoricalWeatherPlan(purpose, safe_dates, tuple(objects), limits,
                                 "Feasibility plan; coverage is unverified until cached compatibility and gap audits pass")


def plan_gefs(
        dates: Iterable[date], *, cycle_hour: int = 0,
        members: Iterable[str] = GEFS_MEMBERS, leads: Iterable[int] = range(9, 31, 3),
        limits: HistoricalWeatherLimits | None = None, today: date | None = None,
        purpose: str = "gefs_feasibility") -> HistoricalWeatherPlan:
    limits = limits or HistoricalWeatherLimits()
    safe_dates = _safe_historical_dates(dates, limits.maximum_days, today=today)
    member_values = tuple(members)
    lead_values = tuple(sorted(set(leads)))
    if (not member_values or len(member_values) > limits.maximum_gefs_members
            or len(set(member_values)) != len(member_values)
            or any(member not in GEFS_SOURCE_IDS for member in member_values)):
        raise ValueError("GEFS sources are invalid or exceed their per-day budget")
    if not lead_values or len(lead_values) > limits.maximum_gefs_leads_per_day:
        raise ValueError("GEFS leads are empty or exceed their per-day budget")
    objects = []
    for day in safe_dates:
        initialized = datetime.combine(day, time(cycle_hour), UTC)
        for member in member_values:
            extras = (("ens mean",) if member == "avg" else ("ens std dev",)
                      if member == "spr" else ("ENS=",))
            selectors = tuple(
                FieldSelector(name, *GEFS_FIELDS[name], GEFS_TEMPERATURE_FIELD_MAX_BYTES, extras)
                for name in GEFS_FIELDS
            )
            for lead in lead_values:
                url = gefs_url(initialized, member, lead)
                source_id = f"gefs-{day:%Y%m%d}-t{cycle_hour:02d}z-{member}-f{lead:03d}"
                objects.append(ArchiveObjectPlan(source_id, "NOAA_NODD_AWS", "gefs", initialized,
                                                 lead, member, url, url + ".idx", selectors))
    return HistoricalWeatherPlan(purpose, safe_dates, tuple(objects), limits,
                                 "Feasibility plan; ensemble trajectories are unverified until cached member/lead coverage audits pass")


def build_gefs_derived_product_pilot(target_date: date, *, today: date | None = None) -> HistoricalWeatherPlan:
    """Plan archived GEFS ensemble mean/spread temperature fields at eight leads."""
    limits = HistoricalWeatherLimits(
        maximum_days=1, maximum_requests=40, maximum_bytes=128_000_000,
        maximum_hrrr_leads_per_day=4, maximum_gefs_leads_per_day=8,
        maximum_gefs_members=2,
    )
    return plan_gefs(
        [target_date], members=GEFS_DERIVED_PRODUCTS,
        leads=(9, 12, 15, 18, 21, 24, 27, 30), limits=limits, today=today,
        purpose="gefs_derived_product_compatibility_pilot",
    )


REVISED_HRRR_FIELDS = (
    "temperature_2m", "total_cloud_cover", "cloud_ceiling",
    "wind_u_10m", "wind_v_10m", "mean_sea_level_pressure",
)
REVISED_HRRR_LEADS = (2, 8, 14, 20)
REVISED_GEFS_LEADS = (9, 12, 15, 18, 21, 24, 27, 30)


def build_revised_daily_plans(target_date: date, *, today: date | None = None
                              ) -> tuple[HistoricalWeatherPlan, HistoricalWeatherPlan]:
    """Build the admitted low-request HRRR plus GEFS mean/spread day plan."""
    limits = HistoricalWeatherLimits(
        maximum_days=1, maximum_requests=320, maximum_bytes=320_000_000,
        maximum_hrrr_leads_per_day=4, maximum_gefs_leads_per_day=8,
        maximum_gefs_members=2,
    )
    hrrr = plan_hrrr(
        [target_date], leads=REVISED_HRRR_LEADS, fields=REVISED_HRRR_FIELDS,
        limits=limits, today=today, purpose="revised_hrrr_daily_history",
    )
    gefs = plan_gefs(
        [target_date], members=GEFS_DERIVED_PRODUCTS, leads=REVISED_GEFS_LEADS,
        limits=limits, today=today, purpose="revised_gefs_mean_spread_daily_history",
    )
    if hrrr.request_count + gefs.request_count != 60:
        raise ValueError("Revised daily weather plan request count differs from registration")
    return hrrr, gefs


def build_compatibility_pilot(target_date: date, *, today: date | None = None) -> dict:
    """Plan a small, explicit HRRR/GEFS pilot without making any request."""
    limits = HistoricalWeatherLimits(maximum_days=1, maximum_requests=100,
                                     maximum_bytes=512_000_000,
                                     maximum_hrrr_leads_per_day=4,
                                     maximum_gefs_leads_per_day=4,
                                     maximum_gefs_members=3)
    hrrr = plan_hrrr([target_date], leads=(2, 8, 14, 20), limits=limits, today=today,
                     purpose="hrrr_compatibility_pilot")
    gefs = plan_gefs([target_date], members=("c00", "p01", "p02"),
                     leads=(9, 15, 21, 27), limits=limits, today=today,
                     purpose="gefs_compatibility_pilot")
    requests = hrrr.request_count + gefs.request_count
    maximum_bytes = hrrr.maximum_bytes + gefs.maximum_bytes
    if requests > limits.maximum_requests or maximum_bytes > limits.maximum_bytes:
        raise ValueError("Combined compatibility pilot exceeds its registered budget")
    return {
        "plan_version": "klax-weather-compatibility-pilot-v1",
        "status": "PLANNED_COMPATIBILITY_PILOT_NO_DATA_ACQUIRED",
        "coverage_complete": False,
        "protected_final_allowed": False,
        "cache_only": True,
        "hrrr": hrrr,
        "gefs": gefs,
        "budget": {
            "maximum_days": 1, "planned_days": 1, "remaining_days": 0,
            "maximum_requests": limits.maximum_requests, "planned_requests": requests,
            "remaining_requests": limits.maximum_requests - requests,
            "maximum_bytes": limits.maximum_bytes, "planned_maximum_bytes": maximum_bytes,
            "remaining_bytes": limits.maximum_bytes - maximum_bytes,
            "hrrr_leads": 4, "gefs_members": 3, "gefs_leads": 4,
        },
        "remaining_limits": [
            "Pilot omits 20 of the 24 registered HRRR climate-day leads",
            "Pilot uses 3 of 31 GEFS members and 4 of 8 registered climate-day leads",
            "No archive availability, field compatibility, spatial extraction, or historical coverage is established until cache validation succeeds",
        ],
    }


def estimate_registered_training_development_coverage() -> dict:
    """Return a conservative ceiling, not permission or proof of acquisition."""
    training_days = (TRAINING_END - TRAINING_START).days + 1
    development_days = (DEVELOPMENT_END - DEVELOPMENT_START).days + 1
    days = training_days + development_days
    hrrr_leads = 24
    gefs_leads = 8
    gefs_members = len(GEFS_MEMBERS)
    hrrr_objects = days * hrrr_leads
    gefs_objects = days * gefs_leads * gefs_members
    hrrr_requests = hrrr_objects * (1 + len(HRRR_FIELDS))
    gefs_requests = gefs_objects * (1 + len(GEFS_FIELDS))
    hrrr_maximum_bytes = hrrr_objects * (INDEX_MAX_BYTES + len(HRRR_FIELDS) * HRRR_FIELD_MAX_BYTES)
    gefs_maximum_bytes = gefs_objects * (INDEX_MAX_BYTES + GEFS_TEMPERATURE_FIELD_MAX_BYTES)
    return {
        "estimate_version": "klax-v3-weather-coverage-ceiling-v1",
        "status": "ESTIMATE_ONLY_NO_DATA_ACQUIRED",
        "training_days": training_days, "development_days": development_days,
        "total_days": days, "protected_final_days": 0,
        "hrrr": {
            "leads_per_day": hrrr_leads, "fields_per_object": len(HRRR_FIELDS),
            "objects": hrrr_objects, "requests": hrrr_requests,
            "maximum_bytes": hrrr_maximum_bytes,
        },
        "gefs": {
            "members": gefs_members, "leads_per_day": gefs_leads,
            "objects": gefs_objects, "requests": gefs_requests,
            "maximum_bytes": gefs_maximum_bytes,
        },
        "combined": {
            "objects": hrrr_objects + gefs_objects,
            "requests": hrrr_requests + gefs_requests,
            "maximum_bytes": hrrr_maximum_bytes + gefs_maximum_bytes,
            "unacquired_requests": hrrr_requests + gefs_requests,
            "unacquired_maximum_bytes": hrrr_maximum_bytes + gefs_maximum_bytes,
        },
        "interpretation": (
            "Conservative byte reservations for field-range acquisition; actual index-selected ranges "
            "may be smaller. A compatibility pilot and revised finite batch budget are required before acquisition."
        ),
    }


def publish_source_feasibility(root: Path, target_date: date = DEVELOPMENT_START,
                               *, today: date | None = None) -> dict:
    """Publish an honest pre-acquisition decision for the registered sources."""
    root = Path(root).resolve()
    pilot = build_compatibility_pilot(target_date, today=today)
    estimate = estimate_registered_training_development_coverage()
    acquired_path = root / "data/manifests/v3_weather_compatibility_pilot.json"
    acquired = None
    acquired_candidate = None
    if acquired_path.is_file():
        candidate = json.loads(acquired_path.read_text(encoding="utf-8"))
        if (candidate.get("status") != "COMPATIBILITY_PILOT_ACQUIRED_AND_CACHE_VERIFIED"
                or candidate.get("target_date") != target_date.isoformat()
                or candidate.get("protected_final_read") is not False
                or candidate.get("coverage_complete") is not False):
            raise ValueError("Weather compatibility pilot manifest is inconsistent")
        cache_root = root / "data/raw/weather_v3/compatibility"
        acquired = {
            "path": acquired_path.relative_to(root).as_posix(),
            "sha256": sha256(acquired_path.read_bytes()).hexdigest(),
            "transferred_bytes": candidate.get("transferred_bytes"),
            "network_requests": candidate.get("network_requests"),
            "hrrr_cache_verification": verify_cache_only(pilot["hrrr"], cache_root),
            "gefs_cache_verification": verify_cache_only(pilot["gefs"], cache_root),
        }
        acquired_candidate = candidate
    derived_path = root / "data/manifests/v3_gefs_derived_product_pilot.json"
    derived = None
    derived_candidate = None
    if derived_path.is_file():
        candidate = json.loads(derived_path.read_text(encoding="utf-8"))
        if (candidate.get("status") != "GEFS_DERIVED_PRODUCT_PILOT_ACQUIRED_AND_CACHE_VERIFIED"
                or candidate.get("target_date") != target_date.isoformat()
                or candidate.get("protected_final_read") is not False
                or candidate.get("coverage_complete") is not False):
            raise ValueError("GEFS derived-product pilot manifest is inconsistent")
        cache_root = root / "data/raw/weather_v3/compatibility"
        derived_plan = build_gefs_derived_product_pilot(target_date, today=today)
        derived = {
            "path": derived_path.relative_to(root).as_posix(),
            "sha256": sha256(derived_path.read_bytes()).hexdigest(),
            "transferred_bytes": candidate.get("transferred_bytes"),
            "network_requests": candidate.get("network_requests"),
            "cache_verification": verify_cache_only(derived_plan, cache_root),
        }
        derived_candidate = candidate

    revised = None
    if acquired_candidate is not None and derived_candidate is not None:
        hrrr_daily = sum(
            row["index_bytes"] + sum(
                field["bytes"] for field in row["fields"]
                if field["field_id"] in REVISED_HRRR_FIELDS
            )
            for row in acquired_candidate["hrrr"]["records"]
        )
        gefs_daily = sum(
            row["index_bytes"] + sum(field["bytes"] for field in row["fields"])
            for row in derived_candidate["gefs"]["records"]
        )
        days = estimate["total_days"]
        projected_bytes = (hrrr_daily + gefs_daily) * days
        transfer_cap = 35_000_000_000
        requests = 60 * days
        revised = {
            "status": "ADMITTED_FINITE_PLAN_NOT_ACQUIRED",
            "training_and_development_days": days,
            "hrrr_leads": list(REVISED_HRRR_LEADS),
            "hrrr_fields": list(REVISED_HRRR_FIELDS),
            "gefs_products": list(GEFS_DERIVED_PRODUCTS),
            "gefs_leads": list(REVISED_GEFS_LEADS),
            "requests": requests,
            "request_rate_per_second": 2,
            "minimum_request_wall_seconds": requests / 2,
            "observed_pilot_bytes_per_day": hrrr_daily + gefs_daily,
            "projected_bytes_from_one_day_pilots": projected_bytes,
            "transfer_cap_bytes": transfer_cap,
            "headroom_bytes": transfer_cap - projected_bytes,
            "member_level_distribution_retained": False,
            "uncertainty_representation": "archived_GEFS_ensemble_mean_and_standard_deviation",
        }
    admitted = revised is not None and revised["headroom_bytes"] > 0
    report = {
        "schema_version": 1,
        "component": "source_feasibility",
        "status": ("REVISED_FINITE_BULK_PLAN_ADMITTED_NOT_COMPLETE" if admitted
                   else "PILOT_PASS_REVISED_BULK_PLAN_REQUIRED" if acquired is not None
                   else "PILOT_REQUIRED_BEFORE_BULK_ACQUISITION"),
        "historical_only": True,
        "network_used": False,
        "protected_final_read": False,
        "bulk_acquisition_authorized": admitted,
        "reason": (
            "The original full-member/full-lead ceiling remains rejected. Exact one-day pilots "
            "admit a smaller HRRR plus archived GEFS mean/spread plan under a 35 GB fail-closed cap."
            if admitted else
            "The conservative full-coverage ceiling is too large to admit without an index-range "
            "compatibility pilot and a smaller registered transfer budget."
        ),
        "pilot": {
            **{key: value for key, value in pilot.items() if key not in ("hrrr", "gefs")},
            "hrrr": pilot["hrrr"].to_dict(),
            "gefs": pilot["gefs"].to_dict(),
        },
        "acquired_pilot_evidence": acquired,
        "derived_gefs_pilot_evidence": derived,
        "revised_bulk_plan": revised,
        "full_coverage_ceiling": estimate,
        "required_next_evidence": ([
            "complete the admitted historical range under its request and byte caps",
            "GRIB decode and KLAX grid extraction compatibility",
            "coverage, gap, unit, cycle, lead, and availability audit",
        ] if admitted else [
            "bounded historical index downloads for one development date",
            "exact field-range downloads with hashes and provenance",
            "GRIB decode and KLAX grid extraction compatibility",
            "revised finite request and byte budget based on observed range sizes",
        ]),
    }
    write_json(root / "data/manifests/v3_source_feasibility.json", report)
    return report


@dataclass(frozen=True)
class IndexRange:
    field_id: str
    start: int
    end: int
    line: str


def select_index_ranges(index_text: str, item: ArchiveObjectPlan) -> tuple[IndexRange, ...]:
    """Resolve exact registered fields to bounded ranges from one cached index."""
    rows: list[tuple[int, list[str], str]] = []
    for line in index_text.splitlines():
        parts = line.split(":")
        if len(parts) < 6:
            raise ValueError("Malformed NOAA GRIB2 index line")
        try:
            offset = int(parts[1])
        except ValueError as exc:
            raise ValueError("Invalid NOAA GRIB2 index offset") from exc
        rows.append((offset, parts, line))
    if not rows or any(left[0] >= right[0] for left, right in zip(rows, rows[1:])):
        raise ValueError("NOAA index offsets are empty, duplicated, or unsorted")
    initialized = "d=" + item.initialized_at.strftime("%Y%m%d%H")
    forecast = "anl" if item.lead_hours == 0 else f"{item.lead_hours} hour fcst"
    result = []
    for selector in item.fields:
        matches = []
        for index, (offset, parts, line) in enumerate(rows):
            extras = tuple(part.strip() for part in parts[6:] if part.strip())
            extras_allowed = all(any(extra.startswith(prefix) for prefix in selector.allowed_extra_prefixes)
                                 for extra in extras)
            if (parts[2] == initialized and parts[3] == selector.variable
                    and parts[4] == selector.level and parts[5] == forecast and extras_allowed):
                if index + 1 >= len(rows):
                    raise ValueError("Selected final index record has no bounded byte range")
                matches.append(IndexRange(selector.field_id, offset, rows[index + 1][0] - 1, line))
        if len(matches) != 1:
            raise ValueError(f"Expected one {selector.field_id} field, found {len(matches)}")
        result.append(matches[0])
    return tuple(result)


def _safe_cache_path(cache_root: Path, *parts: str) -> Path:
    root = Path(cache_root).resolve()
    if "protected_final" in {part.casefold() for part in root.parts}:
        raise ValueError("Compatibility cache cannot be inside protected_final")
    target = root.joinpath(*parts).resolve()
    target.relative_to(root)
    if "protected_final" in {part.casefold() for part in target.parts}:
        raise ValueError("Compatibility cache cannot nominate protected_final")
    return target


def _verified_cache_file(path: Path, *, url: str, start: int | None, end: int | None,
                         maximum_bytes: int, require_grib: bool) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"cache-only compatibility input missing: {path}")
    sidecar = path.with_suffix(path.suffix + ".json")
    if not sidecar.is_file():
        raise ValueError(f"cached compatibility input lacks provenance: {path}")
    metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    required = {"url", "retrieved_at_utc", "range_start", "range_end", "bytes", "sha256",
                "etag", "last_modified", "historical_availability_proven"}
    if not isinstance(metadata, dict) or set(metadata) != required:
        raise ValueError("Cached compatibility provenance fields differ")
    if (metadata["url"], metadata["range_start"], metadata["range_end"]) != (url, start, end):
        raise ValueError("Cached compatibility source identity differs")
    retrieved = datetime.fromisoformat(metadata["retrieved_at_utc"])
    _aware_utc(retrieved, "retrieved_at_utc")
    contents = path.read_bytes()
    if not contents or len(contents) > maximum_bytes:
        raise ValueError("Cached compatibility input is empty or exceeds its byte reservation")
    if metadata["bytes"] != len(contents) or metadata["sha256"] != sha256(contents).hexdigest():
        raise ValueError("Cached compatibility hash or length differs")
    if metadata["historical_availability_proven"] is not False:
        raise ValueError("Retrieval metadata cannot claim historical publication proof")
    if require_grib and (not contents.startswith(b"GRIB") or len(contents) != end - start + 1):
        raise ValueError("Cached range is not the exact bounded GRIB2 message")
    return metadata


def verify_cache_only(plan: HistoricalWeatherPlan, cache_root: Path) -> dict:
    """Validate a compatibility cache; never acquire or discover missing data."""
    records = []
    total_bytes = 0
    for item in plan.objects:
        folder = _safe_cache_path(cache_root, item.cache_key)
        index_path = _safe_cache_path(folder, "source.idx")
        index_meta = _verified_cache_file(index_path, url=item.index_url, start=None, end=None,
                                          maximum_bytes=item.index_max_bytes, require_grib=False)
        ranges = select_index_ranges(index_path.read_text(encoding="utf-8"), item)
        field_records = []
        selectors = {field.field_id: field for field in item.fields}
        for selected in ranges:
            field_path = _safe_cache_path(folder, selected.field_id + ".grib2")
            field_meta = _verified_cache_file(
                field_path, url=item.url, start=selected.start, end=selected.end,
                maximum_bytes=selectors[selected.field_id].maximum_bytes, require_grib=True)
            field_records.append({
                "field_id": selected.field_id, "index_line": selected.line,
                "range_start": selected.start, "range_end": selected.end,
                "bytes": field_meta["bytes"], "sha256": field_meta["sha256"],
            })
            total_bytes += field_meta["bytes"]
        total_bytes += index_meta["bytes"]
        records.append({
            "source_id": item.source_id, "model": item.model, "member_id": item.member_id,
            "initialized_at": item.initialized_at.isoformat(), "lead_hours": item.lead_hours,
            "url": item.url, "index_url": item.index_url,
            "index_sha256": index_meta["sha256"], "fields": field_records,
            "historical_availability_proven": False,
        })
    return {
        "status": "CACHE_COMPATIBILITY_PASS",
        "scope": "Cached file identity, bounded ranges, hashes, and registered field inventory only",
        "coverage_complete": False,
        "network_used": False,
        "protected_final_read": False,
        "objects_verified": len(records), "cached_bytes_verified": total_bytes,
        "records": records,
        "limitations": [
            "Retrieval timestamps do not prove original historical publication time",
            "GRIB field decoding and KLAX grid extraction remain separate compatibility gates",
            "A pilot pass does not establish complete date/member/lead coverage",
        ],
    }


@dataclass(frozen=True)
class RawLocalObservation:
    source_provider: str
    source_record_id: str
    station: str
    report_type: str
    observed_at: datetime
    issued_at: datetime
    available_at: datetime
    temperature: float
    temperature_unit: str
    dewpoint: float | None
    dewpoint_unit: str
    wind_direction_degrees: float | None
    wind_speed: float | None
    wind_speed_unit: str
    pressure: float | None
    pressure_unit: str
    cloud_ceiling: float | None
    cloud_ceiling_unit: str
    visibility: float | None
    visibility_unit: str
    source_sha256: str

    def __post_init__(self) -> None:
        if self.source_provider not in {"NOAA_NCEI", "IEM_ASOS_ARCHIVE"}:
            raise ValueError("Unsupported historical observation provider")
        _identifier(self.source_record_id, "source record identifier")
        if self.station not in LOCAL_STATIONS:
            raise ValueError("Observation station is outside the registered LAX local set")
        if self.report_type not in {"METAR", "SPECI"}:
            raise ValueError("Observation must be METAR or SPECI")
        observed = _aware_utc(self.observed_at, "observed_at")
        issued = _aware_utc(self.issued_at, "issued_at")
        available = _aware_utc(self.available_at, "available_at")
        if issued < observed or available < issued:
            raise ValueError("Observation timestamp order is inconsistent")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "issued_at", issued)
        object.__setattr__(self, "available_at", available)
        for value, label, optional in (
            (self.temperature, "temperature", False), (self.dewpoint, "dewpoint", True),
            (self.wind_direction_degrees, "wind direction", True),
            (self.wind_speed, "wind speed", True), (self.pressure, "pressure", True),
            (self.cloud_ceiling, "cloud ceiling", True), (self.visibility, "visibility", True),
        ):
            _finite(value, label, optional=optional)
        for value, allowed, label in (
            (self.temperature_unit, {"C", "F"}, "temperature unit"),
            (self.dewpoint_unit, {"C", "F"}, "dewpoint unit"),
            (self.wind_speed_unit, {"KT", "M/S"}, "wind speed unit"),
            (self.pressure_unit, {"HPA", "PA"}, "pressure unit"),
            (self.cloud_ceiling_unit, {"FT", "M"}, "cloud ceiling unit"),
            (self.visibility_unit, {"MI", "M"}, "visibility unit"),
        ):
            if value not in allowed:
                raise ValueError(f"Unsupported {label}")
        _digest(self.source_sha256, "observation source SHA-256")


@dataclass(frozen=True)
class NormalizedLocalObservation:
    observation: AsOfObservation
    source_provider: str
    source_record_id: str
    report_type: str
    climate_date: date
    partition: str
    decision_at: datetime
    normalization_version: str = "klax-local-observation-normalization-v1"

    def to_dict(self) -> dict:
        value = self.observation
        return {
            "normalization_version": self.normalization_version,
            "source_provider": self.source_provider, "source_record_id": self.source_record_id,
            "report_type": self.report_type, "climate_date": self.climate_date.isoformat(),
            "partition": self.partition, "decision_at": self.decision_at.isoformat(),
            "station": value.station, "observed_at": value.observed_at.isoformat(),
            "issued_at": value.issued_at.isoformat(), "available_at": value.available_at.isoformat(),
            "temperature_f": value.temperature_f, "dewpoint_f": value.dewpoint_f,
            "wind_direction_degrees": value.wind_direction_degrees,
            "wind_speed_kt": value.wind_speed_kt, "pressure_hpa": value.pressure_hpa,
            "cloud_ceiling_ft": value.cloud_ceiling_ft,
            "visibility_miles": value.visibility_miles, "source_sha256": value.source_sha256,
            "as_of_validated": True, "protected_final": False,
        }


def _fahrenheit(value: float | None, unit: str) -> float | None:
    return None if value is None else (float(value) if unit == "F" else float(value) * 9 / 5 + 32)


def normalize_local_observation(
        raw: RawLocalObservation, *, climate_date: date, partition: str,
        decision_at: datetime) -> NormalizedLocalObservation:
    """Normalize one immutable report and enforce its simulated as-of cutoff."""
    if not isinstance(raw, RawLocalObservation):
        raise ValueError("raw must be a RawLocalObservation")
    if not isinstance(climate_date, date) or isinstance(climate_date, datetime):
        raise ValueError("climate_date must be a date")
    expected_partition = (
        "weather_training" if TRAINING_START <= climate_date <= TRAINING_END else
        "selection" if DEVELOPMENT_START <= climate_date <= DEVELOPMENT_END else None
    )
    if expected_partition is None:
        if climate_date >= PROTECTED_FINAL_START:
            raise ValueError("Protected-final observations cannot be normalized in development")
        raise ValueError("Observation climate date is outside registered V3 partitions")
    if partition != expected_partition:
        raise ValueError("Observation partition does not match its climate date")
    decision = _aware_utc(decision_at, "decision_at")
    observation = AsOfObservation(
        station=raw.station, observed_at=raw.observed_at, issued_at=raw.issued_at,
        available_at=raw.available_at,
        temperature_f=_fahrenheit(raw.temperature, raw.temperature_unit),
        dewpoint_f=_fahrenheit(raw.dewpoint, raw.dewpoint_unit),
        wind_direction_degrees=raw.wind_direction_degrees,
        wind_speed_kt=(None if raw.wind_speed is None else float(raw.wind_speed)
                       if raw.wind_speed_unit == "KT" else float(raw.wind_speed) * 1.9438444924406),
        pressure_hpa=(None if raw.pressure is None else float(raw.pressure)
                      if raw.pressure_unit == "HPA" else float(raw.pressure) / 100),
        cloud_ceiling_ft=(None if raw.cloud_ceiling is None else float(raw.cloud_ceiling)
                          if raw.cloud_ceiling_unit == "FT" else float(raw.cloud_ceiling) * 3.2808398950131),
        visibility_miles=(None if raw.visibility is None else float(raw.visibility)
                          if raw.visibility_unit == "MI" else float(raw.visibility) / 1609.344),
        source_sha256=raw.source_sha256,
    )
    observation.require_as_of(decision)
    return NormalizedLocalObservation(observation, raw.source_provider, raw.source_record_id,
                                      raw.report_type, climate_date, partition, decision)
