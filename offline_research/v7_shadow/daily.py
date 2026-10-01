"""Create one immutable V7 prediction/order freeze without reading outcomes."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from friend_method.evaluation import bracket_probabilities as gaussian_bracket_probabilities
from friend_method.evaluation import fit_variance
from friend_method.exact_evaluation import _date_seed, _prequential_records, fit_location, load_exact_features
from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from past7_replay.audited import (
    _apply_arrival,
    _choose_friend_order,
    _choose_no_order,
    _classify_raw_row,
)
from past7_replay.engine import (
    _load_training,
    _ordered_yes,
    _probability_rows,
    _read_json,
    _seal,
    _single_source_forecast,
    _write_immutable_json,
)
from v5.probability import SOURCE_PATH
from v5b.evaluation import DEFAULTS as V5B_DEFAULTS
from v5b_confirmation.predictions import _bounds
from v5b_next.weather_evaluation import FEATURE_NAMES
from v5b_next.weather_features import aggregate_day
from v5b_next.weather_model import _fit_predict, bracket_probabilities, quantile, utc

from .campaign import CAMPAIGN_ID, CONFIG, REGISTRATION, STATE, _file_hash, _read_object, _verify_seal, target_dates


V5B_STRATEGY = Path("runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json")
FRIEND_CONFIG = Path("configs/friend_method_exact_test.json")
V5F_CONFIG = Path("configs/v5f_stacked.json")

METHODS = (
    "v5b_causal_no",
    "v6_gefs_spread_equal_blend_no",
    "diagnostic_nam_only",
    "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack",
    "diagnostic_gfs_only",
    "diagnostic_nbm_only",
)


class V7DailyError(ValueError):
    pass


def _verify_object_seal(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    body = {key: item for key, item in value.items() if key != field}
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    if value.get(field) != digest:
        raise V7DailyError(f"{field} mismatch")


def _load_inputs(root: Path, day: str) -> tuple[list[dict], dict[int, dict[tuple[str, str, str], dict]], dict[str, str]]:
    daily = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / day / "input"
    universe_path = daily / "contract_universe.json"
    universe = _read_object(universe_path)
    _verify_object_seal(universe)
    if universe.get("status") != "FROZEN_OUTCOME_BLIND" or universe.get("outcomes_read") is not False:
        raise V7DailyError("daily universe is not outcome blind")
    records = list(universe.get("records", []))
    if len(records) != 12 or {row["contract_side"] for row in records} != {"YES", "NO"}:
        raise V7DailyError("daily universe must contain six YES and six NO records")
    contracts = _ordered_yes(records)
    if any(row["climate_date"] != day for row in records):
        raise V7DailyError("daily universe date differs")

    books_path = daily / "probalytics/target_books.jsonl"
    rows = [json.loads(line) for line in books_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    snapshots: dict[int, dict[tuple[str, str, str], dict]] = {0: {}, 5: {}}
    for raw in rows:
        offset = int(raw["target_offset_s"])
        if offset not in snapshots:
            continue
        if raw["climate_date"] != day:
            raise V7DailyError("Probalytics row date differs")
        key = (day, str(raw["market_platform_id"]), str(raw["outcome_name"]).upper())
        if key in snapshots[offset]:
            raise V7DailyError("duplicate Probalytics book row")
        snapshots[offset][key] = _classify_raw_row(raw)
    expected = {(day, row["market_ticker"], side) for row in contracts for side in ("YES", "NO")}
    if set(snapshots[0]) != expected or set(snapshots[5]) != expected:
        raise V7DailyError("offset-zero or offset-five book coverage differs")
    bindings = {
        universe_path.relative_to(root).as_posix(): _file_hash(universe_path),
        books_path.relative_to(root).as_posix(): _file_hash(books_path),
    }
    return contracts, snapshots, bindings


def _weather_frames(root: Path, day: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    folder = root / "data/normalized/v5p_probability_features" / f"date={day}"
    manifest_path = folder / "manifest.json"
    manifest = _read_object(manifest_path)
    if (
        manifest.get("status") != "NORMALIZED_FEATURES_ONLY"
        or manifest.get("climate_date") != day
        or manifest.get("decision_time_utc") != "18:00"
        or manifest.get("protected_confirmation_labels_read") is not False
    ):
        raise V7DailyError("unsafe normalized weather manifest")
    h_path, g_path = folder / "hrrr_points.parquet", folder / "gefs_summary_points.parquet"
    expected = {record["path"]: record for record in manifest["outputs"]}
    for path in (h_path, g_path):
        record = expected[path.relative_to(root).as_posix()]
        if record["sha256"] != _file_hash(path) or record["bytes"] != path.stat().st_size:
            raise V7DailyError("normalized weather binding differs")
    return pd.read_parquet(h_path), pd.read_parquet(g_path), {
        path.relative_to(root).as_posix(): _file_hash(path)
        for path in (manifest_path, h_path, g_path)
    }


def _v5b_probabilities(root: Path, day: str, contracts: list[dict], hrrr: pd.DataFrame, gefs: pd.DataFrame) -> tuple[dict[str, float], dict]:
    source_path = root / SOURCE_PATH
    source = _read_object(source_path)
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    prediction = model.predict({
        "climate_date": day,
        "forecasts": pd.concat([hrrr, gefs], ignore_index=True).to_dict(orient="records"),
        "observations": [],
        "contains_settlement_label": False,
        "as_of_join_validated": True,
    }, tuple(_bounds(row) for row in contracts))
    values = {row["market_ticker"]: float(value) for row, value in zip(contracts, prediction.probabilities)}
    if not math.isclose(sum(values.values()), 1.0, abs_tol=1e-10):
        raise V7DailyError("V5B probability mass differs")
    return values, {
        "fitted_model_sha256": model.identity,
        "source_path": SOURCE_PATH.as_posix(),
        "source_sha256": _file_hash(source_path),
        "regime": prediction.regime,
        "target_updates_used": False,
    }


def _v6_forecast(root: Path, day: str, contracts: list[dict], hrrr: pd.DataFrame, gefs: pd.DataFrame) -> dict:
    training, labels = _load_training(root)
    labels_by_date = {row["climate_date"]: row for row in labels}
    target = aggregate_day(hrrr, gefs, day)
    decision = utc(target["decision_at"])
    prior = [row for row in training if utc(labels_by_date[row["climate_date"]]["issued_at"]) < decision]
    if len(prior) != 64:
        raise V7DailyError("V6 did not retain the 64-date training state")
    outcomes = [labels_by_date[row["climate_date"]]["reported_high_f"] for row in prior]
    location, fit = _fit_predict(prior, outcomes, target, "equal_blend_bias", FEATURE_NAMES)
    scale = float(np.clip(float(target["gefs_spread_delta_f_at_mean_max"]), 0.5, 10.0))
    prequential: dict[str, tuple[float, float]] = {}
    for index, current in enumerate(training):
        earlier = [
            row for row in training[:index]
            if utc(labels_by_date[row["climate_date"]]["issued_at"]) < utc(current["decision_at"])
        ]
        if len(earlier) < 10:
            continue
        ys = [labels_by_date[row["climate_date"]]["reported_high_f"] for row in earlier]
        loc, _ = _fit_predict(earlier, ys, current, "equal_blend_bias", FEATURE_NAMES)
        prequential[current["climate_date"]] = (loc, float(np.clip(float(current["gefs_spread_delta_f_at_mean_max"]), 0.5, 10.0)))
    error_rows = [row for row in prior if row["climate_date"] in prequential]
    standardized = [
        (float(labels_by_date[row["climate_date"]]["reported_high_f"]) - prequential[row["climate_date"]][0])
        / prequential[row["climate_date"]][1]
        for row in error_rows
    ]
    forecast = {
        "climate_date": day,
        "decision_at": target["decision_at"],
        "method_id": "gefs_spread_equal_blend",
        "location_f": float(location),
        "scale_f": scale,
        "kernel_sigma_f": 1.0,
        "prequential_residuals_f": [float(value * scale) for value in standardized],
        "standardized_prequential_residuals": standardized,
        "training_dates": [row["climate_date"] for row in prior],
        "training_count": len(prior),
        "residual_dates": [row["climate_date"] for row in error_rows],
        "fit_metadata": fit,
        "target_updates_used": False,
    }
    forecast["interval_80_f"] = [quantile(forecast, 0.1), quantile(forecast, 0.9)]
    forecast["interval_95_f"] = [quantile(forecast, 0.025), quantile(forecast, 0.975)]
    forecast["central_probabilities"] = bracket_probabilities(forecast, contracts)
    return forecast


def _friend_forecasts(root: Path, day: str, contracts: list[dict]) -> tuple[dict, dict[str, dict]]:
    training_meta, labels = _load_training(root)
    training_dates = [row["climate_date"] for row in training_meta]
    config = _read_json(root / FRIEND_CONFIG)
    window = next(item for item in config["weather"]["windows"] if item["role"] == "primary")
    training = load_exact_features(root, training_dates, window)
    current = load_exact_features(root, [day], window)[0]
    outcomes = [float(row["reported_high_f"]) for row in labels]
    ridge = float(config["forecast"]["simplex_ridge_lambda"])
    floor = float(config["forecast"]["sigma_floor_f"])
    refits = int(config["forecast"]["bootstrap_refits"])
    seed = int(config["forecast"]["bootstrap_seed"])
    q = float(config["forecast"]["conservative_probability_quantile"])
    location, location_fit = fit_location(training, outcomes, current, ridge)
    sigma, variance_fit = fit_variance(_prequential_records(training, outcomes, ridge), location_fit["between_source_variance_f2"], floor)
    central = gaussian_bracket_probabilities(location, sigma, contracts)
    scenarios = {ticker: [] for ticker in central}
    rng = np.random.default_rng(_date_seed(seed, day))
    for _ in range(refits):
        selected = rng.integers(0, len(training), len(training))
        sampled_rows = [training[int(position)] for position in selected]
        sampled_outcomes = [outcomes[int(position)] for position in selected]
        sample_location, _ = fit_location(sampled_rows, sampled_outcomes, current, ridge)
        scenario = gaussian_bracket_probabilities(sample_location, sigma, contracts)
        for ticker, value in scenario.items():
            scenarios[ticker].append(value)
    conservative = {ticker: float(np.quantile(values, q, method="linear")) for ticker, values in scenarios.items()}
    combined = {
        "climate_date": day,
        "decision_at": current["decision_at"],
        "source_highs_f": {source: current[source] for source in ("gfs", "nam", "nbm")},
        "location_f": location,
        "sigma_f": sigma,
        "central_probabilities": central,
        "conservative_probabilities": conservative,
        "location_fit": location_fit,
        "variance_fit": variance_fit,
        "training_dates": training_dates,
        "target_updates_used": False,
    }
    diagnostics = {
        source: _single_source_forecast(source, training, outcomes, current, contracts, floor, refits, seed, q)
        for source in ("gfs", "nam", "nbm")
    }
    return combined, diagnostics


def freeze_day(project_root: str | Path, target: str) -> dict:
    root = Path(project_root).resolve()
    config = _read_object(root / CONFIG)
    allowed = target_dates(config)
    if target not in allowed or date.fromisoformat(target) >= datetime.now(UTC).date():
        raise V7DailyError("target must be a registered, elapsed V7 date")
    registration = _read_object(root / REGISTRATION)
    _verify_seal(registration, "registration_sha256")
    contracts, snapshots, input_bindings = _load_inputs(root, target)
    hrrr, gefs, weather_bindings = _weather_frames(root, target)

    v5b, v5b_meta = _v5b_probabilities(root, target, contracts, hrrr, gefs)
    v6 = _v6_forecast(root, target, contracts, hrrr, gefs)
    friend, diagnostics = _friend_forecasts(root, target, contracts)
    wide = gaussian_bracket_probabilities(friend["location_f"], friend["sigma_f"] * 2.0, contracts)
    heavy = {ticker: 0.8 * friend["central_probabilities"][ticker] + 0.2 * wide[ticker] for ticker in wide}
    v5f = {ticker: 0.5 * v5b[ticker] + 0.5 * heavy[ticker] for ticker in v5b}

    v5b_strategy = _read_json(root / V5B_STRATEGY, sealed=True)
    friend_config = _read_json(root / FRIEND_CONFIG)
    v5f_config = _read_json(root / V5F_CONFIG)
    if v5f_config["parameters"] != {
        "v5b_weight": 0.5, "friend_heavy_tail_weight": 0.5,
        "wide_component_weight": 0.2, "wide_sigma_multiplier": 2.0,
    }:
        raise V7DailyError("V5F weights differ")
    v6_policy = {**V5B_DEFAULTS, "selection_mode": "expected_profit", "allowed_sides": ["NO"]}
    definitions = {
        "v5b_causal_no": (v5b, None, v5b_meta),
        "v6_gefs_spread_equal_blend_no": (v6["central_probabilities"], None, {key: value for key, value in v6.items() if key != "central_probabilities"}),
        "diagnostic_nam_only": (diagnostics["nam"]["central_probabilities"], diagnostics["nam"]["conservative_probabilities"], diagnostics["nam"]),
        "friend_exact_primary_gfs_nam_nbm": (friend["central_probabilities"], friend["conservative_probabilities"], friend),
        "v5f_cross_family_stack": (v5f, None, {"friend_heavy_tail_probabilities": heavy, "target_updates_used": False}),
        "diagnostic_gfs_only": (diagnostics["gfs"]["central_probabilities"], diagnostics["gfs"]["conservative_probabilities"], diagnostics["gfs"]),
        "diagnostic_nbm_only": (diagnostics["nbm"]["central_probabilities"], diagnostics["nbm"]["conservative_probabilities"], diagnostics["nbm"]),
    }
    records = {}
    event = contracts[0]["event_ticker"]
    settlement_sources = {row["settlement_sources"][0]["name"] for row in contracts}
    if len(settlement_sources) != 1:
        raise V7DailyError("contract settlement sources differ within event")
    for method in METHODS:
        probabilities, conservative, metadata = definitions[method]
        if method in {"v5b_causal_no", "v5f_cross_family_stack"}:
            order = _choose_no_order(target, probabilities, contracts, snapshots[0], v5b_strategy["parameters"])
        elif method == "v6_gefs_spread_equal_blend_no":
            order = _choose_no_order(target, probabilities, contracts, snapshots[0], v6_policy)
        else:
            order = _choose_friend_order(target, probabilities, conservative, contracts, snapshots[0], friend_config["strategy"])
        records[method] = {
            "climate_date": target,
            "event_ticker": event,
            "settlement_source": next(iter(settlement_sources)),
            "decision_at_utc": target + "T18:00:00+00:00",
            "arrival_at_utc": target + "T18:00:05+00:00",
            "probabilities": _probability_rows(probabilities, contracts),
            "forecast": metadata,
            "order": _apply_arrival(order, target, snapshots[5]),
        }
    freeze = _seal({
        "schema_version": "klax-v7-daily-prediction-order-freeze-v1",
        "campaign_id": CAMPAIGN_ID,
        "registration_sha256": registration["registration_sha256"],
        "status": "FROZEN_BEFORE_OUTCOME_READ",
        "climate_date": target,
        "methods": records,
        "input_bindings": input_bindings | weather_bindings | {
            V5B_STRATEGY.as_posix(): _file_hash(root / V5B_STRATEGY),
            FRIEND_CONFIG.as_posix(): _file_hash(root / FRIEND_CONFIG),
            V5F_CONFIG.as_posix(): _file_hash(root / V5F_CONFIG),
        },
        "outcomes_read": False,
        "settlement_labels_read": False,
        "network_used_by_prediction": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "order_authorization_count": 0,
    })
    output = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / target / "prediction-order-freeze.json"
    _write_immutable_json(output, freeze)

    state_path = root / STATE
    state = _read_object(state_path)
    _verify_seal(state, "recovery_sha256")
    completed = sorted(set(state["completed_prediction_freeze_dates"]) | {target})
    body = {key: value for key, value in state.items() if key != "recovery_sha256"}
    body.update({
        "status": "COLLECTING_SHADOW_DATES" if len(completed) < 42 else "PREDICTIONS_COMPLETE_AWAITING_TERMINAL_SCORE",
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "completed_acquisition_dates": completed,
        "completed_prediction_freeze_dates": completed,
        "last_completed_date": target,
        "last_daily_freeze_path": output.relative_to(root).as_posix(),
        "last_daily_freeze_sha256": _file_hash(output),
    })
    body["recovery_sha256"] = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    pending = state_path.with_name(state_path.name + ".pending")
    pending.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(state_path)
    return freeze


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    value = freeze_day(args.project_root, args.date)
    print(json.dumps({
        "campaign_id": value["campaign_id"],
        "climate_date": value["climate_date"],
        "status": value["status"],
        "method_count": len(value["methods"]),
        "outcomes_read": value["outcomes_read"],
        "orders_placed": value["paper_orders_placed"] + value["live_orders_placed"],
        "self_sha256": value["self_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()

