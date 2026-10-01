"""Finite chronological conditional-uncertainty and weather-analog research."""
from copy import deepcopy
import math
from pathlib import Path
import json

import numpy as np

from v5b.campaign import checked, filehash, digest
from v5b.evaluation import evaluate_candidate as score_market
from v5b_next.weather_evaluation import load_development as load_weather, FEATURE_NAMES
from v5b_next.weather_model import _fit_predict, utc, quantile, bracket_probabilities
from v6.research_specs import validate_parameters, METHODS, blocked_capabilities

REFERENCE_FREEZE = "runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json"
SPREAD = "gefs_spread_delta_f_at_mean_max"
DISAGREEMENT = "absolute_sampled_max_disagreement_f"
CONDITIONING = ("hrrr_cloud_percent_mean", "hrrr_wind_u_mean_m_s", "hrrr_wind_v_mean_m_s", DISAGREEMENT, SPREAD)
ANALOG_KEYS = ("hrrr_temperature_f_max", "gefs_mean_temperature_f_max") + CONDITIONING
POLICIES = {
    "no_dollar_all": {"selection_mode": "expected_profit", "allowed_sides": ["NO"]},
    "no_dollar_paid": {"selection_mode": "expected_profit", "allowed_sides": ["NO"], "allowed_grades": ["A", "B_PLUS"]},
    "both_dollar_paid": {"selection_mode": "expected_profit", "allowed_sides": ["YES", "NO"], "allowed_grades": ["A", "B_PLUS"]},
}


def validate_spec(spec):
    return validate_parameters(spec)


def load_development(root):
    root = Path(root).resolve()
    context = load_weather(root)
    path = root / REFERENCE_FREEZE
    reference = checked(path)
    if reference["holdout_access_authorized"] is not False or reference["orders"] != 0:
        raise ValueError("frozen reference boundary differs")
    candidate_path = path.parent / "candidates" / (reference["candidate_id"] + ".json")
    if filehash(candidate_path) != reference["candidate_sha256"]:
        raise ValueError("frozen reference candidate binding differs")
    candidate = checked(candidate_path)
    if candidate["parameters"] != reference["parameters"]:
        raise ValueError("reference parameters differ")
    context["reference_parameters"] = reference["parameters"]
    context["reference_candidate_id"] = reference["candidate_id"]
    context["input_bindings"].update({REFERENCE_FREEZE: filehash(path),
        candidate_path.relative_to(root).as_posix(): filehash(candidate_path)})
    return context


def _number(row, key):
    value = row[key]
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f"invalid finite weather feature: {key}")
    if key in (SPREAD, DISAGREEMENT) and value < 0:
        raise ValueError("uncertainty proxy must be nonnegative")
    return float(value)


def _nearest(rows, current, keys, count):
    matrix = np.array([[_number(row, key) for key in keys] for row in rows])
    scale = matrix.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.)
    point = np.array([_number(current, key) for key in keys])
    distances = ((matrix - point) / scale) ** 2
    return sorted(range(len(rows)), key=lambda i: (float(distances[i].sum()), rows[i]["climate_date"]))[:count]


def _conditional_scale(rows, errors, current):
    if len(rows) < 10:
        return max(.5, float(np.mean(np.abs(errors)))) if errors else 1.
    x = np.array([[_number(row, key) for key in CONDITIONING] for row in rows])
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale = np.where(scale > 1e-12, scale, 1.)
    z = (x - center) / scale
    y = np.log(np.maximum(np.abs(errors), .5))
    beta = np.linalg.solve(z.T @ z + 10. * np.eye(len(CONDITIONING)), z.T @ (y - y.mean()))
    point = (np.array([_number(current, key) for key in CONDITIONING]) - center) / scale
    return math.exp(float(np.clip(y.mean() + point @ beta, math.log(.5), math.log(10.))))


def forecast_sequence(features, label_records, method):
    """Fit location and scale from previously published observations only."""
    if method not in METHODS:
        raise ValueError("unsupported V6 method")
    dates = [row["climate_date"] for row in features]
    labels = {row["climate_date"]: row for row in label_records}
    if len(features) < 21 or dates != sorted(set(dates)) or len(labels) != len(label_records) or set(labels) != set(dates):
        raise ValueError("exact chronological development cohort required")
    for label in label_records:
        _number(label, "reported_high_f")
        utc(label["issued_at"])
    prior_predictions, out = {}, []
    for i, current in enumerate(features):
        day = current["climate_date"]
        decision = utc(current["decision_at"])
        if decision.date().isoformat() != day or (decision.hour, decision.minute, decision.second, decision.microsecond) != (18, 0, 0, 0):
            raise ValueError("exact18UTC development decision required")
        prior = [row for row in features[:i] if utc(labels[row["climate_date"]]["issued_at"]) < decision]
        if len(prior) < 10:
            if i >= 20:
                raise ValueError("insufficient published training dates")
            continue
        observed = [labels[row["climate_date"]]["reported_high_f"] for row in prior]
        error_rows = [row for row in prior if row["climate_date"] in prior_predictions]
        errors = [labels[row["climate_date"]]["reported_high_f"] - prior_predictions[row["climate_date"]][0] for row in error_rows]
        neighbors = []
        if method == "weather_analog_knn":
            neighbor_indices = _nearest(prior, current, ANALOG_KEYS, 5)
            neighbors = [prior[j]["climate_date"] for j in neighbor_indices]
            base = (_number(current, FEATURE_NAMES["hrrr"]) + _number(current, FEATURE_NAMES["gefs"])) / 2
            correction = [observed[j] - (_number(prior[j], FEATURE_NAMES["hrrr"]) + _number(prior[j], FEATURE_NAMES["gefs"])) / 2 for j in neighbor_indices]
            location = base + float(np.mean(correction))
            current_scale = 1.
        else:
            family = "cloud_wind_disagreement_ridge" if method == "conditional_cloud_wind_scale" else "equal_blend_bias"
            location, _ = _fit_predict(prior, observed, current, family, FEATURE_NAMES)
            if method == "conditional_cloud_wind_scale":
                current_scale = _conditional_scale(error_rows, errors, current)
            else:
                proxy = SPREAD if method == "gefs_spread_equal_blend" else DISAGREEMENT
                current_scale = float(np.clip(_number(current, proxy), .5, 10.))
        if i < 20:
            prior_predictions[day] = (location, current_scale)
            continue
        if len(prior) < 20 or len(errors) < 10:
            raise ValueError("shared20-date warmup/residual readiness failed")
        if method == "weather_analog_knn":
            positions = _nearest(error_rows, current, ANALOG_KEYS, 10)
            residual_rows = [error_rows[j] for j in positions]
        else:
            residual_rows = error_rows
        standardized = [(labels[row["climate_date"]]["reported_high_f"] - prior_predictions[row["climate_date"]][0]) /
                        prior_predictions[row["climate_date"]][1] for row in residual_rows]
        residuals = [float(z * current_scale) for z in standardized]
        forecast = {"climate_date": day, "decision_at": current["decision_at"], "method_id": method,
            "location_f": float(location), "scale_f": float(current_scale),
            "kernel_sigma_f": 1., "prequential_residuals_f": residuals,
            "standardized_prequential_residuals": standardized,
            "predictive_mean_f": float(location + np.mean(residuals)),
            "training_dates": [r["climate_date"] for r in prior], "training_count": len(prior),
            "residual_dates": [r["climate_date"] for r in residual_rows], "residual_count": len(residuals),
            "analog_location_neighbor_dates": neighbors, "scale_clip_f": [.5, 10.],
            "scale_fit_uses_prior_prequential_errors": True}
        forecast["interval_80_f"] = [quantile(forecast, .1), quantile(forecast, .9)]
        forecast["interval_95_f"] = [quantile(forecast, .025), quantile(forecast, .975)]
        out.append(forecast)
        prior_predictions[day] = (location, current_scale)
    return out


def _scoped(context):
    dates = context["dates"][20:]
    return {**context, "dates": dates, "labels": {d: context["labels"][d] for d in dates},
            "rows_by_date": {d: context["rows_by_date"][d] for d in dates}}


def reference_replay(context):
    """Replay the immutable V5B strategy as a control, never a new candidate."""
    if "reference_parameters" not in context:
        raise ValueError("bound V5B strategy reference required")
    return {"reference_candidate_id": context["reference_candidate_id"], "counts_as_new_candidate": False,
        "original_64_date_result": score_market(context, context["reference_parameters"]),
        "common_44_date_result": score_market(_scoped(context), context["reference_parameters"]),
        "interpretation": "Reference replay and common-cohort comparison, not fresh independent evidence."}


def evaluate_candidate(context, spec):
    spec = validate_spec(spec)
    if len(context["dates"]) != 64 or context.get("partition") != "development":
        raise ValueError("frozen64-date development context required")
    forecasts = forecast_sequence(context["weather_features"], context["label_records"], spec["method_id"])
    dates = context["dates"][20:]
    if [f["climate_date"] for f in forecasts] != dates:
        raise ValueError("all methods must score the same44dates")
    probabilities = {}
    for f in forecasts:
        day = f["climate_date"]
        contracts = [r for r in context["rows_by_date"][day] if r["contract_side"] == "YES"]
        for ticker, value in bracket_probabilities(f, contracts).items():
            if spec["stage"] == "synthesis":
                value = .5 * value + .5 * context["probabilities"][day, ticker]
            probabilities[day, ticker] = value
    cohort = _scoped(context)
    result = score_market({**cohort, "probabilities": probabilities, "raw_probabilities": probabilities}, POLICIES[spec["policy_id"]])
    reference = score_market(cohort, POLICIES[spec["policy_id"]])
    result["baseline_multiclass_brier"] = reference["multiclass_brier"]
    result["execution_parameters"] = result["parameters"]
    result["parameters"] = spec
    result["schema_version"] = "v6-development-evaluation-v1"
    result["promotion_eligible"] = spec["stage"] != "precursor"
    result["common_scoring_dates"] = dates
    result["warmup_dates"] = context["dates"][:20]
    result["weather_forecasts"] = forecasts
    precursor = {"method_forecast_count": len(forecasts), "mean_scale_f": float(np.mean([f["scale_f"] for f in forecasts])),
        "mae_predictive_mean_f": float(np.mean([abs(f["predictive_mean_f"] - context["labels"][f["climate_date"]]) for f in forecasts])),
        "policy_chain_multiclass_brier": result["multiclass_brier"], "reference_multiclass_brier": reference["multiclass_brier"],
        "policy_chain_log_loss": result["multiclass_log_loss"], "model_distribution_is_preblend": True,
        "synthesis_weight_new": .5 if spec["stage"] == "synthesis" else 1.}
    for level in (80, 95):
        intervals = [f[f"interval_{level}_f"] for f in forecasts]
        precursor[f"method_coverage_{level}"] = sum(lo <= context["labels"][day] <= hi for day, (lo, hi) in zip(dates, intervals)) / len(dates)
        precursor[f"method_mean_width_{level}_f"] = float(np.mean([hi - lo for lo, hi in intervals]))
    result["precursor"] = precursor
    result["forecast_metrics"] = dict(precursor)
    result["calibration"] = {"method": "prior_only_conditional_prequential_residual_kernel",
        "chronological_only": True, "minimum_training_count": min(f["training_count"] for f in forecasts),
        "minimum_residual_count": min(f["residual_count"] for f in forecasts), "kernel_sigma_f": 1.}
    trades_by_date = {t["climate_date"]: t for t in result["trades"]}
    if len(trades_by_date) != len(result["trades"]) or not set(trades_by_date).issubset(dates):
        raise ValueError("behavioral ledger requires at most one trade on each scoring date")
    ledger = []
    for day in dates:
        trade = trades_by_date.get(day)
        if trade is None:
            ledger.append({"climate_date": day, "abstention": True})
        else:
            ledger.append({"climate_date": day, "market_ticker": trade["market_ticker"],
                "contract_side": trade["contract_side"], "quantity": 1,
                "entry_price_cents": trade["entry_price_cents"], "fee_dollars": trade["fee_dollars"],
                "execution_evidence_grade": trade["execution_evidence_grade"], "abstention": False})
    if len(ledger) != 44 or [row["climate_date"] for row in ledger] != dates or len(set(dates)) != 44:
        raise ValueError("behavioral ledger must cover exactly44 unique chronological scoring dates")
    result["behavioral_ledger"] = ledger
    result["behavioral_fingerprint"] = digest(ledger)
    result["blocked_capabilities"] = blocked_capabilities()
    result["limitations"] = ["Repeatedly exposed development evidence; no independent confirmation.",
        "Sparse sampled maxima, GEFS spread and model disagreement are imperfect weather proxies.",
        "Kernel bandwidth1F, scale bounds0.5..10F, ridge10 and analog k5/k10 are fixed before scoring.",
        "A/B+/B historical quotes do not prove fills; unavailable evidence abstains.",
        "Forecast intervals and MAE describe the new weather distribution before any synthesis probability blend.",
        "Precursor economics are diagnostic only and never promotion-eligible."]
    json.dumps(result, allow_nan=False)
    return result
