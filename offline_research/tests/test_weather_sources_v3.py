"""Offline fixtures for the bounded V3 weather-source foundation."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from klax_lab.weather_sources_v3 import (
    HistoricalWeatherLimits, RawLocalObservation, build_compatibility_pilot,
    build_gefs_derived_product_pilot,
    build_revised_daily_plans,
    estimate_registered_training_development_coverage,
    gefs_url, hrrr_url,
    normalize_local_observation, plan_gefs, plan_hrrr, select_index_ranges,
    publish_source_feasibility, verify_cache_only,
)


UTC = timezone.utc
DAY = date(2025, 1, 5)
NOW = date(2026, 9, 25)
INIT = datetime(2025, 1, 5, 6, tzinfo=UTC)


def test_primary_archive_url_conventions_are_exact_and_historical_only():
    assert hrrr_url(INIT, 2) == (
        "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20250105/conus/"
        "hrrr.t06z.wrfsfcf02.grib2")
    assert gefs_url(INIT, "c00", 9) == (
        "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20250105/06/atmos/pgrb2ap5/"
        "gec00.t06z.pgrb2a.0p50.f009")
    assert gefs_url(INIT, "p30", 30).endswith("/gep30.t06z.pgrb2a.0p50.f030")
    with pytest.raises(ValueError, match="extended cycles"):
        hrrr_url(INIT.replace(hour=5), 2)
    with pytest.raises(ValueError, match="p01 through p30"):
        gefs_url(INIT, "mean", 9)
    assert gefs_url(INIT.replace(hour=0), "avg", 9).endswith(
        "/geavg.t00z.pgrb2a.0p50.f009")
    assert gefs_url(INIT.replace(hour=0), "spr", 9).endswith(
        "/gespr.t00z.pgrb2a.0p50.f009")


def test_hrrr_inventory_uses_archive_mslma_mean_sea_level_field():
    plan = plan_hrrr([DAY], leads=(2,), fields=("mean_sea_level_pressure",),
                     today=NOW)
    field = plan.objects[0].fields[0]
    assert (field.variable, field.level) == ("MSLMA", "mean sea level")


def test_plans_enforce_dates_members_leads_requests_and_bytes():
    limits = HistoricalWeatherLimits(maximum_days=1, maximum_requests=4,
                                     maximum_bytes=20_000_000,
                                     maximum_hrrr_leads_per_day=1,
                                     maximum_gefs_leads_per_day=1,
                                     maximum_gefs_members=1)
    hrrr = plan_hrrr([DAY], leads=(2,), fields=("temperature_2m",), limits=limits, today=NOW)
    assert hrrr.request_count == 2
    assert hrrr.maximum_bytes == 10_000_000
    assert hrrr.to_dict()["budget"]["remaining_bytes"] == 10_000_000
    gefs = plan_gefs([DAY], members=("p01",), leads=(9,), limits=limits, today=NOW)
    assert gefs.request_count == 2
    assert gefs.maximum_bytes == 6_000_000
    assert all("latest" not in item.url for item in gefs.objects)

    with pytest.raises(ValueError, match="Protected-final"):
        plan_hrrr([date(2025, 7, 1)], leads=(2,), fields=("temperature_2m",),
                  limits=limits, today=NOW)
    with pytest.raises(ValueError, match="strictly historical"):
        plan_gefs([NOW], members=("p01",), leads=(9,), limits=limits, today=NOW)
    with pytest.raises(ValueError, match="per-day budget"):
        plan_gefs([DAY], members=("p01", "p02"), leads=(9,), limits=limits, today=NOW)
    with pytest.raises(ValueError, match="request budget"):
        plan_hrrr([DAY], leads=(2,), limits=limits, today=NOW)


def test_compatibility_pilot_reports_exact_scope_and_remaining_limits():
    pilot = build_compatibility_pilot(DAY, today=NOW)
    assert pilot["status"] == "PLANNED_COMPATIBILITY_PILOT_NO_DATA_ACQUIRED"
    assert pilot["coverage_complete"] is False
    assert pilot["cache_only"] is True
    assert pilot["budget"] == {
        "maximum_days": 1, "planned_days": 1, "remaining_days": 0,
        "maximum_requests": 100, "planned_requests": 56, "remaining_requests": 44,
        "maximum_bytes": 512_000_000, "planned_maximum_bytes": 304_000_000,
        "remaining_bytes": 208_000_000, "hrrr_leads": 4, "gefs_members": 3,
        "gefs_leads": 4,
    }
    assert len(pilot["hrrr"].objects) == 4
    assert len(pilot["gefs"].objects) == 12


def test_gefs_derived_mean_spread_plan_binds_exact_index_qualifiers():
    plan = build_gefs_derived_product_pilot(DAY, today=NOW)
    assert plan.request_count == 32
    assert len(plan.objects) == 16
    qualifiers = {item.member_id: item.fields[0].allowed_extra_prefixes for item in plan.objects}
    assert qualifiers == {"avg": ("ens mean",), "spr": ("ens std dev",)}


def test_revised_daily_plan_is_finite_and_uses_mean_spread_uncertainty():
    hrrr, gefs = build_revised_daily_plans(DAY, today=NOW)
    assert hrrr.request_count == 28 and gefs.request_count == 32
    assert {field.field_id for field in hrrr.objects[0].fields} == {
        "temperature_2m", "total_cloud_cover", "cloud_ceiling",
        "wind_u_10m", "wind_v_10m", "mean_sea_level_pressure",
    }
    assert {item.member_id for item in gefs.objects} == {"avg", "spr"}


def test_full_registered_coverage_estimate_excludes_final_and_claims_no_acquisition():
    estimate = estimate_registered_training_development_coverage()
    assert estimate["status"] == "ESTIMATE_ONLY_NO_DATA_ACQUIRED"
    assert (estimate["training_days"], estimate["development_days"], estimate["total_days"]) == (366, 177, 543)
    assert estimate["protected_final_days"] == 0
    assert estimate["hrrr"] == {
        "leads_per_day": 24, "fields_per_object": 7, "objects": 13032,
        "requests": 104256, "maximum_bytes": 755856000000,
    }
    assert estimate["gefs"] == {
        "members": 31, "leads_per_day": 8, "objects": 134664,
        "requests": 269328, "maximum_bytes": 807984000000,
    }
    assert estimate["combined"]["objects"] == 147696
    assert estimate["combined"]["requests"] == 373584
    assert estimate["combined"]["maximum_bytes"] == 1563840000000
    assert estimate["combined"]["unacquired_maximum_bytes"] == 1563840000000


def test_source_feasibility_manifest_refuses_unpiloted_bulk_acquisition(tmp_path):
    report = publish_source_feasibility(tmp_path, DAY, today=NOW)
    assert report["status"] == "PILOT_REQUIRED_BEFORE_BULK_ACQUISITION"
    assert report["bulk_acquisition_authorized"] is False
    assert report["network_used"] is False and report["protected_final_read"] is False
    assert report["acquired_pilot_evidence"] is None
    assert report["pilot"]["budget"]["planned_requests"] == 56
    assert report["full_coverage_ceiling"]["combined"]["maximum_bytes"] == 1563840000000
    assert (tmp_path / "data/manifests/v3_source_feasibility.json").is_file()


def _one_object_plan():
    limits = HistoricalWeatherLimits(maximum_days=1, maximum_requests=4,
                                     maximum_bytes=20_000_000,
                                     maximum_hrrr_leads_per_day=1,
                                     maximum_gefs_leads_per_day=1,
                                     maximum_gefs_members=1)
    return plan_hrrr([DAY], leads=(2,), fields=("temperature_2m",), limits=limits,
                     today=NOW, purpose="cache_fixture")


def _sidecar(path: Path, url: str, contents: bytes, start=None, end=None):
    path.with_suffix(path.suffix + ".json").write_text(json.dumps({
        "url": url, "retrieved_at_utc": "2026-09-25T00:00:00+00:00",
        "range_start": start, "range_end": end, "bytes": len(contents),
        "sha256": sha256(contents).hexdigest(), "etag": "fixture",
        "last_modified": "historical-fixture", "historical_availability_proven": False,
    }))


def test_cache_only_pilot_validates_exact_index_range_and_provenance(tmp_path):
    plan = _one_object_plan()
    item = plan.objects[0]
    folder = tmp_path / item.cache_key
    folder.mkdir()
    index = ("1:0:d=2025010506:TMP:2 m above ground:2 hour fcst:\n"
             "2:16:d=2025010506:DPT:2 m above ground:2 hour fcst:\n").encode()
    index_path = folder / "source.idx"
    index_path.write_bytes(index)
    _sidecar(index_path, item.index_url, index)
    field = b"GRIBfixture!7777"
    field_path = folder / "temperature_2m.grib2"
    field_path.write_bytes(field)
    _sidecar(field_path, item.url, field, 0, 15)

    ranges = select_index_ranges(index.decode(), item)
    assert [(row.field_id, row.start, row.end) for row in ranges] == [("temperature_2m", 0, 15)]
    result = verify_cache_only(plan, tmp_path)
    assert result["status"] == "CACHE_COMPATIBILITY_PASS"
    assert result["network_used"] is False
    assert result["coverage_complete"] is False
    assert result["objects_verified"] == 1
    assert result["records"][0]["historical_availability_proven"] is False


def test_cache_only_missing_or_protected_input_fails_without_fallback(tmp_path):
    plan = _one_object_plan()
    with pytest.raises(FileNotFoundError, match="cache-only"):
        verify_cache_only(plan, tmp_path)
    protected = tmp_path / "protected_final"
    with pytest.raises(ValueError, match="protected_final"):
        verify_cache_only(plan, protected)


def raw_observation(**overrides):
    values = dict(
        source_provider="NOAA_NCEI", source_record_id="KLAX-20250105T1200Z",
        station="KLAX", report_type="METAR",
        observed_at=datetime(2025, 1, 5, 12, tzinfo=UTC),
        issued_at=datetime(2025, 1, 5, 12, 2, tzinfo=UTC),
        available_at=datetime(2025, 1, 5, 12, 5, tzinfo=UTC),
        temperature=15.0, temperature_unit="C", dewpoint=10.0, dewpoint_unit="C",
        wind_direction_degrees=250.0, wind_speed=5.0, wind_speed_unit="M/S",
        pressure=101325.0, pressure_unit="PA", cloud_ceiling=300.0,
        cloud_ceiling_unit="M", visibility=16093.44, visibility_unit="M",
        source_sha256="a" * 64,
    )
    values.update(overrides)
    return RawLocalObservation(**values)


def test_typed_local_observation_normalization_converts_units_and_enforces_asof():
    decision = datetime(2025, 1, 5, 14, tzinfo=UTC)
    normalized = normalize_local_observation(raw_observation(), climate_date=DAY,
                                             partition="selection", decision_at=decision)
    row = normalized.to_dict()
    assert row["temperature_f"] == 59.0
    assert row["dewpoint_f"] == 50.0
    assert row["wind_speed_kt"] == pytest.approx(9.719222462203)
    assert row["pressure_hpa"] == 1013.25
    assert row["cloud_ceiling_ft"] == pytest.approx(984.25196850393)
    assert row["visibility_miles"] == pytest.approx(10.0)
    assert row["as_of_validated"] is True and row["protected_final"] is False

    with pytest.raises(ValueError, match="not available"):
        normalize_local_observation(raw_observation(available_at=decision + timedelta(minutes=1)),
                                    climate_date=DAY, partition="selection", decision_at=decision)
    with pytest.raises(ValueError, match="Protected-final"):
        normalize_local_observation(raw_observation(), climate_date=date(2025, 7, 1),
                                    partition="selection", decision_at=decision)


def test_observation_interface_rejects_revision_order_and_unregistered_station():
    with pytest.raises(ValueError, match="timestamp order"):
        raw_observation(issued_at=datetime(2025, 1, 5, 11, tzinfo=UTC))
    with pytest.raises(ValueError, match="station"):
        raw_observation(station="KJFK")
