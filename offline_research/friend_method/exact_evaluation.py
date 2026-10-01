"""Exact GFS/NAM/NBM development evaluation of the supplied friend method.

This module has no acquisition, network, order, or protected-holdout entry point.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

import numpy as np

from friend_method.evaluation import bracket_probabilities, fit_variance, _score_variant
from v5b.evaluation import load_development


SOURCES = ("gfs", "nam", "nbm")


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid numeric feature: {name}")
    return float(value)


def simplex_ridge_weights(matrix: np.ndarray, outcomes: np.ndarray, ridge_lambda: float) -> np.ndarray:
    """Solve a small nonnegative sum-to-one ridge problem by active sets."""
    matrix = np.asarray(matrix, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    if matrix.ndim != 2 or outcomes.shape != (matrix.shape[0],):
        raise ValueError("matrix/outcome shape mismatch")
    if matrix.shape[0] < 2 or matrix.shape[1] < 2 or ridge_lambda < 0:
        raise ValueError("invalid simplex fit inputs")
    if not np.all(np.isfinite(matrix)) or not np.all(np.isfinite(outcomes)):
        raise ValueError("simplex fit requires finite inputs")
    source_count = matrix.shape[1]
    center = np.full(source_count, 1.0 / source_count)
    best: tuple[float, tuple[float, ...], np.ndarray] | None = None
    for active_count in range(1, source_count + 1):
        for active in combinations(range(source_count), active_count):
            active_matrix = matrix[:, active]
            gram = active_matrix.T @ active_matrix + ridge_lambda * np.eye(active_count)
            rhs = active_matrix.T @ outcomes + ridge_lambda * center[list(active)]
            kkt = np.block([
                [gram, np.ones((active_count, 1))],
                [np.ones((1, active_count)), np.zeros((1, 1))],
            ])
            target = np.concatenate([rhs, [1.0]])
            try:
                fitted = np.linalg.solve(kkt, target)[:active_count]
            except np.linalg.LinAlgError:
                fitted = np.linalg.lstsq(kkt, target, rcond=None)[0][:active_count]
            if np.any(fitted < -1e-10):
                continue
            weights = np.zeros(source_count)
            weights[list(active)] = np.maximum(fitted, 0.0)
            weights /= weights.sum()
            residual = matrix @ weights - outcomes
            objective = float(residual @ residual + ridge_lambda * np.sum((weights - center) ** 2))
            candidate = (objective, tuple(float(value) for value in weights), weights)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
    if best is None:
        raise RuntimeError("no feasible simplex solution")
    return best[2]


def fit_location(rows: list[dict], outcomes: list[float], current: dict,
                 ridge_lambda: float) -> tuple[float, dict]:
    if len(rows) != len(outcomes) or len(rows) < 2:
        raise ValueError("at least two aligned training rows required")
    raw = np.asarray([[_number(row[source], source) for source in SOURCES] for row in rows], dtype=float)
    y = np.asarray(outcomes, dtype=float)
    biases = np.mean(raw - y[:, None], axis=0)
    corrected = raw - biases
    weights = simplex_ridge_weights(corrected, y, ridge_lambda)
    current_raw = np.asarray([_number(current[source], source) for source in SOURCES], dtype=float)
    current_corrected = current_raw - biases
    location = float(weights @ current_corrected)
    spread_variance = float(weights @ ((current_corrected - location) ** 2))
    return location, {
        "bias_f": {source: float(biases[index]) for index, source in enumerate(SOURCES)},
        "weights": {source: float(weights[index]) for index, source in enumerate(SOURCES)},
        "between_source_variance_f2": spread_variance,
        "simplex_ridge_lambda": float(ridge_lambda),
        "effective_sources": list(SOURCES),
        "gfs_seamless_duplicate_of": "gfs",
    }


def _prequential_records(rows: list[dict], outcomes: list[float], ridge_lambda: float,
                         minimum_history: int = 10) -> list[dict]:
    records = []
    for index in range(minimum_history, len(rows)):
        prediction, metadata = fit_location(rows[:index], outcomes[:index], rows[index], ridge_lambda)
        records.append({
            "error_f": float(outcomes[index] - prediction),
            "spread_variance_f2": metadata["between_source_variance_f2"],
        })
    return records


def _date_seed(base_seed: int, day: str) -> int:
    # Window variants must share bootstrap draws so identical inputs reproduce
    # identical probabilities rather than differing through Monte Carlo noise.
    digest = hashlib.sha256(f"{base_seed}|{day}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def load_exact_features(root: str | Path, dates: list[str], window: dict) -> list[dict]:
    root = Path(root).resolve()
    archive = root / "data/raw/friend_method_weather_v1"
    output = []
    for day in dates:
        payload = json.loads((archive / f"date={day}" / "daily.json").read_text(encoding="utf-8-sig"))
        if not payload["complete"] or set(payload["models"]) != {"gfs", "gfs_seamless", "nam", "nbm"}:
            raise ValueError(f"incomplete exact weather date: {day}")
        gfs = _number(payload["models"]["gfs"]["daily_high_f"], "gfs")
        seamless = _number(payload["models"]["gfs_seamless"]["daily_high_f"], "gfs_seamless")
        if gfs != seamless or payload["models"]["gfs_seamless"]["duplicate_of"] != "gfs":
            raise ValueError(f"GFS duplicate policy differs: {day}")
        nbm_field = window["nbm_daily_high_field"]
        output.append({
            "climate_date": day,
            "decision_at": payload["decision_time_utc"],
            "gfs": gfs,
            "nam": _number(payload["models"]["nam"]["daily_high_f"], "nam"),
            "nbm": _number(payload["models"]["nbm"][nbm_field], "nbm"),
            "window_id": window["id"],
        })
    return output


def forecast_sequence(context: dict, config: dict, window: dict) -> list[dict]:
    features = load_exact_features(context["root"], context["dates"], window)
    labels_by_date = {row["climate_date"]: row for row in context["label_records"]}
    warmup = int(config["evaluation_scope"]["warmup_dates"])
    minimum_history = int(config["forecast"]["minimum_history_dates"])
    ridge_lambda = float(config["forecast"]["simplex_ridge_lambda"])
    output = []
    for index, current in enumerate(features):
        day = current["climate_date"]
        decision = _utc(current["decision_at"])
        prior = [row for row in features[:index] if _utc(labels_by_date[row["climate_date"]]["issued_at"]) < decision]
        if index < warmup:
            continue
        if len(prior) < minimum_history:
            raise ValueError("insufficient prior published labels")
        outcomes = [float(labels_by_date[row["climate_date"]]["reported_high_f"]) for row in prior]
        location, location_fit = fit_location(prior, outcomes, current, ridge_lambda)
        records = _prequential_records(prior, outcomes, ridge_lambda)
        sigma, variance_fit = fit_variance(
            records,
            location_fit["between_source_variance_f2"],
            float(config["forecast"]["sigma_floor_f"]),
        )
        yes = sorted(
            [row for row in context["rows_by_date"][day] if row["contract_side"] == "YES"],
            key=lambda row: (-10000 if row["floor_strike"] is None else row["floor_strike"], row["market_ticker"]),
        )
        central = bracket_probabilities(location, sigma, yes)
        scenarios = {ticker: [] for ticker in central}
        rng = np.random.default_rng(_date_seed(int(config["forecast"]["bootstrap_seed"]), day))
        for _ in range(int(config["forecast"]["bootstrap_refits"])):
            selected = rng.integers(0, len(prior), len(prior))
            sample_rows = [prior[int(position)] for position in selected]
            sample_outcomes = [outcomes[int(position)] for position in selected]
            scenario_location, _ = fit_location(sample_rows, sample_outcomes, current, ridge_lambda)
            scenario = bracket_probabilities(scenario_location, sigma, yes)
            for ticker, probability in scenario.items():
                scenarios[ticker].append(probability)
        quantile = float(config["forecast"]["conservative_probability_quantile"])
        conservative = {
            ticker: float(np.quantile(values, quantile, method="linear"))
            for ticker, values in scenarios.items()
        }
        output.append({
            "climate_date": day,
            "decision_at": current["decision_at"],
            "window_id": window["id"],
            "source_highs_f": {source: current[source] for source in SOURCES},
            "location_f": location,
            "sigma_f": sigma,
            "central_probabilities": central,
            "conservative_probabilities": conservative,
            "location_fit": location_fit,
            "variance_fit": variance_fit,
            "training_dates": [row["climate_date"] for row in prior],
        })
    expected = context["dates"][warmup:]
    if [row["climate_date"] for row in output] != expected:
        raise ValueError("common scoring cohort differs")
    return output


def evaluate(root: str | Path, config: dict) -> dict:
    root = Path(root).resolve()
    context = load_development(root)
    context["label_records"] = json.loads(
        (root / "data/development/v5a/development_labels.json").read_text(encoding="utf-8-sig")
    )["labels"]
    context["root"] = str(root)
    if len(context["dates"]) != int(config["evaluation_scope"]["development_dates"]):
        raise ValueError("development date count differs")
    results, forecasts_by_window = {}, {}
    for window in config["weather"]["windows"]:
        forecasts = forecast_sequence(context, config, window)
        forecasts_by_window[window["id"]] = forecasts
        actuals = [float(context["labels"][row["climate_date"]]) for row in forecasts]
        errors = [row["location_f"] - actual for row, actual in zip(forecasts, actuals)]
        for variant in config["strategy"]["variants"]:
            result = _score_variant(context, forecasts, config, variant)
            result["schema"] = "friend-method-exact-development-result-v1"
            result["window_id"] = window["id"]
            result["window_role"] = window["role"]
            result["exact_requested_models_used"] = ["gfs", "gfs_seamless", "nam", "nbm"]
            result["effective_sources"] = list(SOURCES)
            result["gfs_seamless_duplicate_of"] = "gfs"
            result["forecast_metrics"] = {
                "forecast_count": len(forecasts),
                "mae_location_f": mean(abs(error) for error in errors),
                "rmse_location_f": math.sqrt(mean(error * error for error in errors)),
                "mean_error_f": mean(errors),
                "mean_sigma_f": mean(row["sigma_f"] for row in forecasts),
                "mean_weights": {
                    source: mean(row["location_fit"]["weights"][source] for row in forecasts)
                    for source in SOURCES
                },
            }
            result["limitations"] = [
                "Repeatedly exposed 64-date development history; this is not independent confirmation.",
                "B and B+ historical execution evidence does not prove fills or queue position.",
                "GFS Seamless is a duplicate alias and contributes no separate numerical weight.",
                "The 00Z archive supports a conservative as-of policy, but original trader receipt timestamps are unavailable.",
                "One unit per qualifying bucket is a diagnostic cap rather than a depth-sized portfolio allocation.",
            ]
            results[f"{window['id']}/{variant}"] = result
    return {
        "schema": "friend-method-exact-evaluation-v1",
        "config": config,
        "forecast_count_per_window": int(config["evaluation_scope"]["scoring_dates"]),
        "forecasts_by_window": forecasts_by_window,
        "results": results,
        "primary_result_id": config["strategy"]["primary_variant"],
        "exact_four_model_test_completed": True,
        "effective_source_count": 3,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
