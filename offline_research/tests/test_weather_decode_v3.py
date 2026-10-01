"""Offline V3 GRIB decoding and provenance tests."""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from klax_lab.weather_decode_v3 import KLAX_POINT, decode_weather_plan
from klax_lab.weather_sources_v3 import (
    HistoricalWeatherLimits, plan_gefs, plan_hrrr, select_index_ranges,
)
from grib_test_support import new_regular_ll_sfc_grib2


UTC = timezone.utc
DAY = date(2025, 1, 5)
NOW = date(2026, 9, 25)


def _message(*, initialized: datetime, lead: int, value: float,
             derived: int | None = None) -> bytes:
    import eccodes

    handle = new_regular_ll_sfc_grib2(eccodes)
    try:
        if derived is not None:
            eccodes.codes_set(handle, "productDefinitionTemplateNumber", 2)
            eccodes.codes_set(handle, "typeOfGeneratingProcess", 4)
            eccodes.codes_set(handle, "derivedForecast", derived)
            eccodes.codes_set(handle, "numberOfForecastsInEnsemble", 30)
        else:
            eccodes.codes_set(handle, "typeOfGeneratingProcess", 2)
        values = {
            "centre": "kwbc",
            "Ni": 4, "Nj": 3,
            "latitudeOfFirstGridPointInDegrees": 35,
            "latitudeOfLastGridPointInDegrees": 33,
            "longitudeOfFirstGridPointInDegrees": 240,
            "longitudeOfLastGridPointInDegrees": 243,
            "iDirectionIncrementInDegrees": 1,
            "jDirectionIncrementInDegrees": 1,
            "dataDate": int(initialized.strftime("%Y%m%d")),
            "dataTime": initialized.hour * 100,
            "typeOfLevel": "heightAboveGround", "level": 2,
            "shortName": "2t", "step": lead,
        }
        for key, setting in values.items():
            eccodes.codes_set(handle, key, setting)
        eccodes.codes_set_values(handle, [value] * 12)
        return bytes(eccodes.codes_get_message(handle))
    finally:
        eccodes.codes_release(handle)


def _sidecar(path: Path, *, url: str, contents: bytes,
             start: int | None, end: int | None, last_modified: str) -> None:
    path.with_suffix(path.suffix + ".json").write_text(json.dumps({
        "url": url,
        "retrieved_at_utc": "2026-09-25T12:00:00+00:00",
        "range_start": start, "range_end": end,
        "bytes": len(contents), "sha256": sha256(contents).hexdigest(),
        "etag": "fixture-etag", "last_modified": last_modified,
        "historical_availability_proven": False,
    }), encoding="utf-8")


def _cache_plan(root: Path, plan, values: dict[str, tuple[float, int | None]]) -> None:
    for item in plan.objects:
        folder = root / item.cache_key
        folder.mkdir(parents=True)
        field = item.fields[0]
        value, derived = values[item.member_id or "hrrr"]
        body = _message(initialized=item.initialized_at, lead=item.lead_hours,
                        value=value, derived=derived)
        qualifier = (":ens mean" if item.member_id == "avg" else
                     ":ens std dev" if item.member_id == "spr" else "")
        initialized = "d=" + item.initialized_at.strftime("%Y%m%d%H")
        forecast = f"{item.lead_hours} hour fcst"
        index = (
            f"1:0:{initialized}:{field.variable}:{field.level}:{forecast}{qualifier}\n"
            f"2:{len(body)}:{initialized}:DUMMY:surface:{forecast}:\n"
        ).encode()
        index_path = folder / "source.idx"
        index_path.write_bytes(index)
        _sidecar(index_path, url=item.index_url, contents=index, start=None, end=None,
                 last_modified="Sun, 05 Jan 2025 07:00:00 GMT")
        ranges = select_index_ranges(index.decode(), item)
        assert len(ranges) == 1 and ranges[0].end == len(body) - 1
        field_path = folder / f"{field.field_id}.grib2"
        field_path.write_bytes(body)
        _sidecar(field_path, url=item.url, contents=body, start=0, end=len(body) - 1,
                 last_modified="Sun, 05 Jan 2025 07:00:00 GMT")


def _hrrr_plan():
    limits = HistoricalWeatherLimits(
        maximum_days=1, maximum_requests=2, maximum_bytes=10_000_000,
        maximum_hrrr_leads_per_day=1, maximum_gefs_leads_per_day=1,
        maximum_gefs_members=1,
    )
    return plan_hrrr(
        [DAY], cycle_hour=6, leads=(2,), fields=("temperature_2m",),
        limits=limits, today=NOW, purpose="decode_fixture",
    )


def _gefs_plan():
    limits = HistoricalWeatherLimits(
        maximum_days=1, maximum_requests=4, maximum_bytes=20_000_000,
        maximum_hrrr_leads_per_day=1, maximum_gefs_leads_per_day=1,
        maximum_gefs_members=2,
    )
    return plan_gefs(
        [DAY], members=("avg", "spr"), leads=(9,), limits=limits,
        today=NOW, purpose="decode_fixture",
    )


def test_hrrr_decode_preserves_identity_time_units_space_and_provenance(tmp_path):
    plan = _hrrr_plan()
    _cache_plan(tmp_path, plan, {"hrrr": (283.15, None)})
    rows = decode_weather_plan(plan, tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "hrrr" and row["field_id"] == "temperature_2m"
    assert row["forecast_reference_time_utc"] == "2025-01-05T06:00:00+00:00"
    assert row["cycle_hour_utc"] == 6
    assert row["valid_time_utc"] == "2025-01-05T08:00:00+00:00"
    assert row["lead_hours"] == 2 and row["raw_units"] == "K"
    assert row["value"] == pytest.approx(50.0, abs=0.02) and row["units"] == "degF"
    assert row["point_id"] == KLAX_POINT.point_id
    assert row["target_longitude"] == -118.3866
    assert -180 <= row["grid_longitude"] <= 180 and row["grid_distance_km"] < 100
    assert row["source_range_start"] == 0 and row["source_range_end"] >= 0
    assert len(row["source_sha256"]) == 64 and len(row["index_sha256"]) == 64
    assert row["source_last_modified_at_utc"] == "2025-01-05T07:00:00+00:00"
    assert row["information_available_at_utc"] == "2025-01-05T07:00:00+00:00"
    assert row["historical_availability_proven"] is False
    assert row["as_of_validated"] is False


def test_gefs_mean_and_spread_are_distinguished_and_converted_correctly(tmp_path):
    plan = _gefs_plan()
    _cache_plan(tmp_path, plan, {"avg": (283.15, 0), "spr": (1.2, 2)})
    rows = decode_weather_plan(plan, tmp_path)
    assert len(rows) == 2
    by_member = {row["member_id"]: row for row in rows}
    assert by_member["avg"]["ensemble_statistic"] == "mean"
    assert by_member["avg"]["value"] == pytest.approx(50.0, abs=0.02)
    assert by_member["avg"]["units"] == "degF"
    assert by_member["spr"]["ensemble_statistic"] == "standard_deviation"
    assert by_member["spr"]["value"] == pytest.approx(2.16, abs=0.02)
    assert by_member["spr"]["units"] == "delta_degF"
    assert {row["ensemble_size"] for row in rows} == {30}
    assert {row["derived_forecast_code"] for row in rows} == {0, 2}


def test_corrupt_fragment_and_wrong_gefs_semantics_fail_closed(tmp_path):
    plan = _hrrr_plan()
    _cache_plan(tmp_path, plan, {"hrrr": (283.15, None)})
    item = plan.objects[0]
    path = tmp_path / item.cache_key / "temperature_2m.grib2"
    body = path.read_bytes()[:-1] + b"X"
    path.write_bytes(body)
    _sidecar(path, url=item.url, contents=body, start=0, end=len(body) - 1,
             last_modified="Sun, 05 Jan 2025 07:00:00 GMT")
    with pytest.raises(ValueError, match="closing marker"):
        decode_weather_plan(plan, tmp_path)

    other = tmp_path / "other"
    gefs = _gefs_plan()
    _cache_plan(other, gefs, {"avg": (283.15, 2), "spr": (1.2, 2)})
    with pytest.raises(ValueError, match="ensemble semantics"):
        decode_weather_plan(gefs, other)


def test_missing_fragment_and_protected_cache_path_have_no_fallback(tmp_path):
    plan = _hrrr_plan()
    with pytest.raises(FileNotFoundError, match="cache-only"):
        decode_weather_plan(plan, tmp_path)
    with pytest.raises(ValueError, match="protected_final"):
        decode_weather_plan(plan, tmp_path / "protected_final")
