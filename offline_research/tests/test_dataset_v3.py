"""Tests for the deterministic V3 training, calibration, and evaluation freeze."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from klax_lab.dataset_v3 import (
    CALIBRATION_END, DEVELOPMENT_END, DEVELOPMENT_START, EVALUATION_START,
    TRAINING_END, TRAINING_START, build_five_chronological_folds,
    build_frozen_development_dataset, freeze_project_development_artifacts,
    normalized_input_manifest, publish_frozen_dataset_component_manifests,
    verify_frozen_development_dataset,
)
from klax_lab.features_v3 import raw_feature_map


UTC = timezone.utc
COMPONENTS = (
    "training_settlement", "training_observation", "training_forecast",
    "settlement", "market", "observation", "forecast",
)


def digest(letter: str) -> str:
    return letter * 64


def _weather_rows(day: date, partition: str, observations: list, forecasts: list):
    day_text = day.isoformat()
    for station in ("KLAX", "KSMO"):
        for hour in (11, 14, 16):
            observed = datetime.combine(day, datetime.min.time(), UTC).replace(hour=hour)
            observations.append({
                "climate_date": day_text, "partition": partition, "station": station,
                "source_record_id": f"{station}-{day_text}-{hour}",
                "observed_at": observed.isoformat(), "issued_at": observed.isoformat(),
                "available_at": (observed + timedelta(minutes=15)).isoformat(),
                "temperature_f": 60 + hour / 10, "dewpoint_f": 55.0,
                "wind_direction_degrees": 250.0, "wind_speed_kt": 8.0,
                "pressure_hpa": 1013.0, "cloud_ceiling_ft": 900.0,
                "visibility_miles": 7.0, "source_sha256": digest("e"),
                "as_of_validated": True, "protected_final": False,
            })
    for model, source in (("hrrr", "f"), ("gefs", "1")):
        for cycle_hour in (6, 13):
            initialized = datetime.combine(day, datetime.min.time(), UTC).replace(hour=cycle_hour)
            available = initialized + timedelta(minutes=30)
            for lead in (2, 4):
                for member in (("avg", "spr") if model == "gefs" else (None,)):
                    forecasts.append({
                        "climate_date": day_text, "partition": partition, "model": model,
                        "member_id": member, "initialized_at": initialized.isoformat(),
                        "available_at": available.isoformat(),
                        "valid_at": (initialized + timedelta(hours=lead)).isoformat(),
                        "field_id": "temperature_2m", "location_id": f"KLAX-{lead}",
                        "value": (2.0 if member == "spr" else 65 + lead), "is_missing": False,
                        "as_of_validated": True, "source_sha256": digest(source),
                    })


def fixture_rows(evaluation_day_count: int = 7, training_day_count: int = 7):
    training_settlements, training_observations, training_forecasts = [], [], []
    for offset in range(training_day_count):
        day = TRAINING_START + timedelta(days=offset)
        training_settlements.append({
            "climate_date": day.isoformat(), "partition": "weather_training",
            "station": "KLAX", "reported_high_f": 70,
            "source_sha256": digest("9"), "reconciliation_status": "passed",
        })
        _weather_rows(day, "weather_training", training_observations, training_forecasts)

    settlements, markets, observations, forecasts = [], [], [], []
    development_days = [
        DEVELOPMENT_START + timedelta(days=offset)
        for offset in range((CALIBRATION_END - DEVELOPMENT_START).days + 1)
    ] + [EVALUATION_START + timedelta(days=offset) for offset in range(evaluation_day_count)]
    for day in development_days:
        day_text = day.isoformat()
        event = f"KXHIGHLAX-{day:%y%b%d}".upper()
        low, high = event + "-T69", event + "-T70"
        settlements.append({
            "schema_version": 1, "climate_date": day_text, "partition": "selection",
            "station": "KLAX", "reported_high_f": 70, "event_ticker": event,
            "contract_count": 2, "winning_contract_count": 1,
            "source_sha256": digest("a"), "reconciliation_status": "passed",
            "contract_outcome_reconciliation": [
                {"ticker": low, "interval": {"upper_integer_f": 69}, "yes_outcome": 0,
                 "settlement_label_reconciled": True, "contract_source_sha256": digest("b")},
                {"ticker": high, "interval": {"lower_integer_f": 70}, "yes_outcome": 1,
                 "settlement_label_reconciled": True, "contract_source_sha256": digest("b")},
            ],
        })
        for ticker in (low, high):
            for hour in (11, 12, 14, 15):
                stamp = int(datetime.combine(day, datetime.min.time(), UTC).replace(
                    hour=hour, minute=59).timestamp())
                markets.append({
                    "climate_date": day_text, "partition": "selection", "ticker": ticker,
                    "end_period_ts": stamp, "period_minutes": 1,
                    "yes_bid_close": "0.40", "yes_ask_close": "0.43",
                    "source_sha256": digest("c"),
                })
            trade_time = datetime.combine(day, datetime.min.time(), UTC).replace(hour=14, minute=30)
            markets.append({
                "climate_date": day_text, "partition": "selection", "ticker": ticker,
                "trade_id": f"{ticker}-trade", "created_time": trade_time.isoformat(),
                "created_ts": int(trade_time.timestamp()), "quantity_contracts": "2.5",
                "yes_price_dollars": "0.42", "no_price_dollars": "0.58",
                "source_sha256": digest("d"),
            })
        _weather_rows(day, "selection", observations, forecasts)
    return [training_settlements, training_observations, training_forecasts,
            settlements, markets, observations, forecasts]


def manifests(tables):
    return [normalized_input_manifest(name, rows, upstream_manifests=[{"source": name}])
            for name, rows in zip(COMPONENTS, tables)]


def exclusions(tables):
    training_days = {row["climate_date"] for row in tables[0]}
    development_days = {row["climate_date"] for row in tables[3]}
    training = {
        (TRAINING_START + timedelta(days=index)).isoformat(): "synthetic_test_exclusion"
        for index in range((TRAINING_END - TRAINING_START).days + 1)
        if (TRAINING_START + timedelta(days=index)).isoformat() not in training_days
    }
    development = {
        (DEVELOPMENT_START + timedelta(days=index)).isoformat(): "synthetic_test_exclusion"
        for index in range((DEVELOPMENT_END - DEVELOPMENT_START).days + 1)
        if (DEVELOPMENT_START + timedelta(days=index)).isoformat() not in development_days
    }
    return training, development


def build(path: Path, tables=None):
    tables = tables or fixture_rows()
    bound = manifests(tables)
    training_exclusions, development_exclusions = exclusions(tables)
    return build_frozen_development_dataset(
        path,
        training_settlement_rows=tables[0], training_settlement_manifest=bound[0],
        training_observation_rows=tables[1], training_observation_manifest=bound[1],
        training_forecast_rows=tables[2], training_forecast_manifest=bound[2],
        settlement_rows=tables[3], settlement_manifest=bound[3],
        market_rows=tables[4], market_manifest=bound[4],
        observation_rows=tables[5], observation_manifest=bound[5],
        forecast_rows=tables[6], forecast_manifest=bound[6],
        decision_times_utc=("12:00", "15:00"), required_stations=("KLAX", "KSMO"),
        required_forecast_models=("hrrr", "gefs"),
        minimum_forecast_records_per_model={"hrrr": 2, "gefs": 2},
        training_exclusions=training_exclusions,
        development_exclusions=development_exclusions,
    )


def test_build_is_deterministic_hash_bound_and_separates_all_roles(tmp_path: Path):
    tables = fixture_rows()
    first = build(tmp_path / "freeze", tables)
    before = {path.name: path.read_bytes() for path in (tmp_path / "freeze").iterdir()}
    second = build(tmp_path / "freeze", [list(reversed(rows)) for rows in tables])
    after = {path.name: path.read_bytes() for path in (tmp_path / "freeze").iterdir()}
    assert first == second and before == after
    verified = verify_frozen_development_dataset(tmp_path / "freeze")
    assert verified["status"] == "PASS"
    assert verified["calibration_label_rows"] == 30
    assert verified["evaluation_label_rows"] == 7
    for name in ("weather_training_features.jsonl", "development_calibration_features.jsonl",
                 "development_evaluation_features.jsonl"):
        rows = [json.loads(line) for line in before[name].decode().splitlines()]
        assert all("reported_high_f" not in row and "yes_outcome" not in json.dumps(row)
                   for row in rows)
    assert first["protected_final_read"] is False


def test_as_of_join_uses_latest_completed_evidence_and_excludes_late_cycles(tmp_path: Path):
    build(tmp_path / "freeze")
    rows = [json.loads(line) for line in
            (tmp_path / "freeze/development_calibration_features.jsonl").read_text(
                encoding="utf-8").splitlines()]
    noon = rows[0]
    assert noon["decision_at"].endswith("12:00:00+00:00")
    assert {row["observed_at"][11:16] for row in noon["observations"]} == {"11:00"}
    assert {row["initialized_at"][11:16] for row in noon["forecasts"]} == {"06:00"}
    assert all(datetime.fromtimestamp(
        contract["market"]["latest_completed_candle"]["end_period_ts"], UTC
    ) <= datetime.fromisoformat(noon["decision_at"]) for contract in noon["contracts"])
    assert all(contract["market"]["public_trades_last_60_minutes"]["trade_count"] == 0
               for contract in noon["contracts"])
    three = rows[1]
    assert {row["observed_at"][11:16] for row in three["observations"]} == {"14:00"}
    assert {row["initialized_at"][11:16] for row in three["forecasts"]} == {"13:00"}
    assert all(contract["market"]["public_trades_last_60_minutes"]["trade_count"] == 1
               for contract in three["contracts"])


def test_frozen_weather_row_is_compatible_with_downstream_feature_api(tmp_path: Path):
    build(tmp_path / "freeze")
    row = json.loads((tmp_path / "freeze/weather_training_features.jsonl").read_text(
        encoding="utf-8").splitlines()[0])
    values = raw_feature_map(
        row, feature_set="temperature_only", forecast_source_set="gefs_summary",
    )
    assert "gefs.temperature_mean_f.mean" in values
    assert row["data_role"] == "weather_model_training"


def test_five_folds_exclude_calibration_and_are_contiguous_complete_balanced(tmp_path: Path):
    manifest = build(tmp_path / "freeze", fixture_rows(12))
    folds = json.loads((tmp_path / "freeze/folds.json").read_text(encoding="utf-8"))
    assert [fold["day_count"] for fold in folds["folds"]] == [3, 3, 2, 2, 2]
    flattened = [day for fold in folds["folds"] for day in fold["dates"]]
    assert flattened == folds["evaluation_dates"]
    assert folds["dataset_id"] == manifest["dataset_id"]
    assert (folds["calibration_dates"][0], folds["calibration_dates"][-1]) == (
        "2025-01-05", "2025-02-03")
    assert flattened[0] == "2025-02-04"
    assert all(fold["permitted_calibration_dates"] == folds["calibration_dates"]
               for fold in folds["folds"])


def test_rejects_manifest_hash_mismatch_and_duplicate_grain(tmp_path: Path):
    tables = fixture_rows()
    bound = manifests(tables)
    bound[4]["rows_sha256"] = digest("0")
    training_exclusions, development_exclusions = exclusions(tables)
    with pytest.raises(ValueError, match="rows_sha256"):
        build_frozen_development_dataset(
            tmp_path / "bad-binding",
            training_settlement_rows=tables[0], training_settlement_manifest=bound[0],
            training_observation_rows=tables[1], training_observation_manifest=bound[1],
            training_forecast_rows=tables[2], training_forecast_manifest=bound[2],
            settlement_rows=tables[3], settlement_manifest=bound[3],
            market_rows=tables[4], market_manifest=bound[4],
            observation_rows=tables[5], observation_manifest=bound[5],
            forecast_rows=tables[6], forecast_manifest=bound[6],
            decision_times_utc=("12:00",), training_exclusions=training_exclusions,
            development_exclusions=development_exclusions,
        )
    duplicated = fixture_rows()
    duplicated[5].append(deepcopy(duplicated[5][0]))
    with pytest.raises(ValueError, match="Duplicate observation grain"):
        build(tmp_path / "duplicate", duplicated)


def test_missing_as_of_component_fails_completeness(tmp_path: Path):
    tables = fixture_rows()
    first_day = tables[3][0]["climate_date"]
    tables[4][:] = [row for row in tables[4]
                     if not (row["climate_date"] == first_day and "end_period_ts" in row
                             and row["end_period_ts"] <= int(datetime(2025, 1, 5, 12, tzinfo=UTC).timestamp()))]
    with pytest.raises(ValueError, match="No completed one-minute candle"):
        build(tmp_path / "missing", tables)


def test_calibration_prefix_cannot_be_excluded(tmp_path: Path):
    tables = fixture_rows()
    day = tables[3][0]["climate_date"]
    tables[3] = [row for row in tables[3] if row["climate_date"] != day]
    with pytest.raises(ValueError, match="calibration prefix"):
        build(tmp_path / "bad-prefix", tables)


@pytest.mark.parametrize("component", range(7))
def test_protected_final_is_rejected_from_every_component(tmp_path: Path, component: int):
    tables = fixture_rows()
    tables[component][0]["climate_date"] = "2025-07-01"
    tables[component][0]["partition"] = "protected_final"
    with pytest.raises(ValueError, match="Protected-final"):
        build(tmp_path / f"final-{component}", tables)
    assert not (tmp_path / f"final-{component}").exists()


def test_tampering_and_immutable_overwrite_are_detected(tmp_path: Path):
    build(tmp_path / "freeze")
    feature_path = tmp_path / "freeze/development_evaluation_features.jsonl"
    feature_path.write_bytes(feature_path.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="artifact changed"):
        verify_frozen_development_dataset(tmp_path / "freeze")
    with pytest.raises(ValueError, match="Cannot overwrite frozen artifact"):
        build(tmp_path / "freeze")


def test_fold_builder_rejects_unsorted_calibration_or_final_dates():
    dataset_id = digest("f")
    calibration = [(DEVELOPMENT_START + timedelta(days=index)).isoformat()
                   for index in range((CALIBRATION_END - DEVELOPMENT_START).days + 1)]
    with pytest.raises(ValueError, match="sorted and unique"):
        build_five_chronological_folds(
            ["2025-02-05", "2025-02-04", "2025-02-06", "2025-02-07", "2025-02-08"],
            dataset_id=dataset_id, calibration_dates=calibration,
        )
    with pytest.raises(ValueError, match="Protected-final"):
        build_five_chronological_folds(
            ["2025-02-04", "2025-02-05", "2025-02-06", "2025-02-07", "2025-07-01"],
            dataset_id=dataset_id, calibration_dates=calibration,
        )


def test_project_adapter_fails_closed_before_missing_real_inputs_create_output(tmp_path: Path):
    with pytest.raises(ValueError, match="Missing normalized training_settlement artifact"):
        freeze_project_development_artifacts(
            tmp_path,
            training_settlement_path=Path("data/normalized/training_targets.parquet"),
            training_settlement_manifest_path=Path("data/manifests/training_targets.json"),
            training_forecast_paths=(Path("data/normalized/training_forecasts.parquet"),),
            training_forecast_manifest_paths=(Path("data/manifests/training_forecasts.json"),),
            forecast_paths=(Path("data/normalized/development_forecasts.parquet"),),
            forecast_manifest_paths=(Path("data/manifests/development_forecasts.json"),),
            decision_times_utc=("12:00",),
        )
    assert not (tmp_path / "data/frozen/v3_development").exists()


def test_component_publication_verifies_real_freeze_and_never_claims_readiness(tmp_path: Path):
    destination = tmp_path / "data/frozen/v3-development-fixture"
    build(destination)
    result = publish_frozen_dataset_component_manifests(tmp_path, destination)
    assert result["frozen_dataset"]["ready_for_v3_campaign"] is False
    assert result["five_fold_split"]["ready_for_v3_campaign"] is False
    assert result["five_fold_split"]["evaluation_dates"][0] == "2025-02-04"
    assert (tmp_path / "data/manifests/v3_dataset.json").is_file()
    assert (tmp_path / "data/manifests/v3_five_fold_split.json").is_file()


def test_optional_observation_stations_are_preserved_without_becoming_required(tmp_path: Path):
    tables = fixture_rows()
    bound = manifests(tables)
    training_exclusions, development_exclusions = exclusions(tables)
    manifest = build_frozen_development_dataset(
        tmp_path / "optional-observations",
        training_settlement_rows=tables[0], training_settlement_manifest=bound[0],
        training_observation_rows=tables[1], training_observation_manifest=bound[1],
        training_forecast_rows=tables[2], training_forecast_manifest=bound[2],
        settlement_rows=tables[3], settlement_manifest=bound[3],
        market_rows=tables[4], market_manifest=bound[4],
        observation_rows=tables[5], observation_manifest=bound[5],
        forecast_rows=tables[6], forecast_manifest=bound[6],
        decision_times_utc=("12:00",), required_stations=("KLAX",),
        optional_observation_stations=("KSMO", "KTOA"),
        required_forecast_models=("hrrr", "gefs"),
        minimum_forecast_records_per_model={"hrrr": 2, "gefs": 2},
        training_exclusions=training_exclusions,
        development_exclusions=development_exclusions,
    )
    row = json.loads(
        (tmp_path / "optional-observations/weather_training_features.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert {item["station"] for item in row["observations"]} == {"KLAX", "KSMO"}
    assert manifest["required_stations"] == ["KLAX"]
    assert manifest["optional_observation_stations_preserved_when_available"] == [
        "KSMO", "KTOA",
    ]


def test_published_row_and_fold_schemas_are_valid_json():
    root = Path(__file__).resolve().parents[1]
    names = (
        "frozen-development-feature-v3.schema.json",
        "frozen-development-label-v3.schema.json",
        "frozen-weather-training-feature-v3.schema.json",
        "frozen-weather-training-label-v3.schema.json",
        "five-fold-split-v3.schema.json",
    )
    schemas = [json.loads((root / "schemas" / name).read_text(encoding="utf-8"))
               for name in names]
    assert all(schema["$schema"].endswith("2020-12/schema") for schema in schemas)
