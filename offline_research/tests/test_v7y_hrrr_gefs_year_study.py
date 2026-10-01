from __future__ import annotations

import json
import inspect
import math
from pathlib import Path

import pandas as pd
import pytest

from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from scripts import run_v7y_hrrr_gefs_year_study as study
from v5.probability import SOURCE_PATH
from v5b_confirmation.predictions import _bounds


ROOT = Path(__file__).resolve().parents[1]


def test_partition_counts_are_fixed() -> None:
    days = study._days()
    overlap = [day for day in days if study._partition(day) == "calibration_overlap_diagnostic"]
    primary = [day for day in days if study._partition(day) == "post_calibration_primary"]
    assert len(days) == 361
    assert overlap == study._days(study.START, study.PRIMARY_START - study.timedelta(days=1))
    assert len(overlap) == 30
    assert len(primary) == 331


def test_contract_partition_and_tail_boundaries() -> None:
    universe, _ = study._load_universe(ROOT)
    assert sum(row["metadata_repaired_from_ticker_rules"] for rows in universe.values() for row in rows) == 25
    contracts = universe["2025-01-05"]
    bounds = [_bounds(row).integer_bounds() for row in contracts]
    assert bounds == [(None, 65), (66, 67), (68, 69), (70, 71), (72, 73), (74, None)]
    assert study._winner_ticker(contracts, 65).endswith("T66")
    assert study._winner_ticker(contracts, 66).endswith("B66.5")
    assert study._winner_ticker(contracts, 74).endswith("T73")


def test_one_day_probability_mapping_is_complete_and_outcome_blind() -> None:
    universe, _ = study._load_universe(ROOT)
    contracts = universe["2025-01-05"]
    hrrr, gefs, bindings = study._load_weather(ROOT, "2025-01-05")
    source = json.loads((ROOT / SOURCE_PATH).read_text(encoding="utf-8-sig"))
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    prediction = model.predict({
        "climate_date": "2025-01-05",
        "forecasts": pd.concat([hrrr, gefs], ignore_index=True).to_dict(orient="records"),
        "observations": [],
        "contains_settlement_label": False,
        "as_of_join_validated": True,
    }, tuple(_bounds(row) for row in contracts))
    mapping = {row["ticker"]: float(value) for row, value in zip(contracts, prediction.probabilities, strict=True)}
    assert set(mapping) == {row["ticker"] for row in contracts}
    assert math.isclose(sum(mapping.values()), 1.0, abs_tol=1e-10)
    assert len(bindings) == 3


def _timing_row(*, day: str, nominal: str, modified: str, effective: str,
                eligible: list[str], policy_id: str) -> dict:
    return {
        "nominal_issue_time_utc": nominal,
        "forecast_reference_time_utc": nominal,
        "source_last_modified_at_utc": modified,
        "effective_information_available_at_utc": effective,
        "eligible_decision_times_utc": eligible,
        "availability_policy_id": policy_id,
        "availability_delay_hours": 6,
        "climate_date": day,
    }


def test_late_gefs_row_preserves_actual_schedule_and_passes_fixed_18z_gate() -> None:
    row = _timing_row(
        day="2025-12-19",
        nominal="2025-12-19T00:00:00+00:00",
        modified="2025-12-19T14:29:46+00:00",
        effective="2025-12-19T14:29:46+00:00",
        eligible=["15:00", "18:00"],
        policy_id=study.V7Y_WEATHER_POLICY_ID,
    )
    study._validate_row_timing(
        row, "2025-12-19", expected_policy_id=study.V7Y_WEATHER_POLICY_ID
    )


def test_timing_recomputation_rejects_effective_timestamp_tampering() -> None:
    row = _timing_row(
        day="2025-12-19",
        nominal="2025-12-19T00:00:00+00:00",
        modified="2025-12-19T14:29:46+00:00",
        effective="2025-12-19T14:29:45+00:00",
        eligible=["15:00", "18:00"],
        policy_id=study.V7Y_WEATHER_POLICY_ID,
    )
    with pytest.raises(study.StudyError, match="availability binding"):
        study._validate_row_timing(
            row, "2025-12-19", expected_policy_id=study.V7Y_WEATHER_POLICY_ID
        )


def test_legacy_january_row_recomputes_under_its_bound_policy() -> None:
    row = _timing_row(
        day="2025-01-05",
        nominal="2025-01-05T06:00:00+00:00",
        modified="2025-01-05T06:54:52+00:00",
        effective="2025-01-05T12:00:00+00:00",
        eligible=["12:00", "15:00", "18:00"],
        policy_id=study.LEGACY_WEATHER_POLICY_ID,
    )
    study._validate_row_timing(
        row, "2025-01-05", expected_policy_id=study.LEGACY_WEATHER_POLICY_ID
    )
    row["availability_policy_id"] = study.V7Y_WEATHER_POLICY_ID
    with pytest.raises(study.StudyError, match="availability binding"):
        study._validate_row_timing(
            row, "2025-01-05", expected_policy_id=study.LEGACY_WEATHER_POLICY_ID
        )


def test_policy_registration_and_repair_parent_have_distinct_bound_seals() -> None:
    registration = study.verify_policy_registration(ROOT)
    binding = study.policy_registration_binding(registration)
    prestate = study._repair_prestate_binding(ROOT, registration)
    assert set(binding) == {
        "path",
        "bytes",
        "sha256",
        "policy_sha256",
        "repair_prestate_path",
        "repair_prestate_bytes",
        "repair_prestate_file_sha256",
        "repair_prestate_sha256",
    }
    assert binding["repair_prestate_file_sha256"] == prestate["sha256"]
    assert binding["repair_prestate_sha256"] == prestate["snapshot_sha256"]
    assert registration["policy_sha256"] == registration["policy"]["policy_sha256"]
    assert registration["sha256"] != registration["policy_sha256"]
    assert prestate["snapshot_sha256"] == registration["policy"]["repair_prestate_sha256"]
    assert prestate["prior_recovery_sha256"] != prestate["snapshot_sha256"]


def test_july_weather_paths_use_isolated_v7y_feature_tree() -> None:
    hrrr, gefs, manifest, family = study._weather_paths(ROOT, "2025-07-01")
    assert family == "v7y"
    assert hrrr == ROOT / study.V7Y_WEATHER_OUTPUT_ROOT / "date=2025-07-01" / "hrrr_points.parquet"
    assert gefs.name == "gefs_summary_points.parquet"
    assert manifest.name == "manifest.json"


def test_weather_gate_refuses_incomplete_backfill_before_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(study, "load_state", lambda _: {
        "status": "RUNNING",
        "completed_dates": ["2025-07-01"],
        "unavailable_dates": [],
        "failed_dates": {},
    })
    called = False

    def forbidden_audit(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("audit must not start before all dates complete")

    monkeypatch.setattr(study, "run_weather_audit", forbidden_audit)
    with pytest.raises(study.StudyError, match="all 184"):
        study._weather_integrity_snapshot(ROOT)
    assert called is False


def test_outcome_parser_is_imported_only_inside_score_path() -> None:
    source = inspect.getsource(study._labels)
    assert "from klax_lab.climate import parse_product" in source


def test_clilax_archive_uses_only_preexisting_source_exclusions() -> None:
    labels, bindings, audit = study._labels(ROOT)
    assert len(labels) == 359
    assert set(study._days()) - set(labels) == set(study.LABEL_EXCLUSIONS)
    assert audit["excluded_dates"] == study.LABEL_EXCLUSIONS
    assert audit["quarantined_members"] == study.KNOWN_CLILAX_QUARANTINE
    assert audit["exclusions_registered_before_v7y_scoring"] is True
    assert set(bindings) == {
        study.CLIMATE_MANIFEST.as_posix(),
        "data/raw/climate/CLILAX_2024-01-01_2026-01-08.zip",
    }
