"""First-wave settlement and market-relative evaluation for V6.2."""
from __future__ import annotations

import math
from pathlib import Path
import random
from statistics import mean

from v5a.development_search import _contains
from v5b_next.weather_evaluation import load_development
from v5b_next.weather_model import forecast_sequence, bracket_probabilities

from v6_1.common import checked


FAMILIES = {
    "S01_SOURCE_SEASON_CLIMATOLOGY": "climatology",
    "S02_HRRR_SOURCE_SPECIFIC": "hrrr_bias",
    "S03_GEFS_SOURCE_SPECIFIC": "gefs_bias",
    "S04_CALIBRATED_HRRR_GEFS": "equal_blend_bias",
}


def _ordered_yes(context: dict, date: str) -> list[dict]:
    return sorted([row for row in context["rows_by_date"][date] if row["contract_side"] == "YES"],
                  key=lambda row: (-10000 if row["floor_strike"] is None
                                   else float(row["floor_strike"]), row["market_ticker"]))


def _date_loss(context: dict, date: str, probability_by_ticker: dict[str, float], epsilon: float) -> dict:
    rows = _ordered_yes(context, date)
    probabilities = [probability_by_ticker[row["market_ticker"]] for row in rows]
    if abs(sum(probabilities) - 1) > 1e-8:
        raise ValueError("Candidate probability vector is incoherent")
    winner = [int(_contains(row, context["labels"][date])) for row in rows]
    if sum(winner) != 1:
        raise ValueError("Settlement bracket mapping is not exhaustive")
    index = winner.index(1)
    return {"climate_date": date,
            "brier": sum((value - actual) ** 2 for value, actual in zip(probabilities, winner)),
            "log_loss": -math.log(max(epsilon, probabilities[index])),
            "winning_ticker": rows[index]["market_ticker"]}


def _model_probabilities(context: dict) -> tuple[dict[str, dict], list[str]]:
    score_dates = context["dates"][20:]
    models: dict[str, dict] = {"S00_UNIFORM_CONTROL": {}}
    for date in score_dates:
        tickers = [row["market_ticker"] for row in _ordered_yes(context, date)]
        models["S00_UNIFORM_CONTROL"][date] = {ticker: 1 / len(tickers) for ticker in tickers}
    for candidate, family in FAMILIES.items():
        forecasts = forecast_sequence(context["weather_features"], context["label_records"],
                                      family, feature_names={
                                          "hrrr": "hrrr_temperature_f_max",
                                          "gefs": "gefs_mean_temperature_f_max",
                                          "cloud": "hrrr_cloud_percent_mean",
                                          "u": "hrrr_wind_u_mean_m_s",
                                          "v": "hrrr_wind_v_mean_m_s",
                                          "disagreement": "absolute_sampled_max_disagreement_f",
                                      })
        if [item["climate_date"] for item in forecasts] != score_dates:
            raise ValueError("Weather family scored-date scope differs")
        models[candidate] = {}
        for forecast in forecasts:
            date = forecast["climate_date"]
            models[candidate][date] = bracket_probabilities(forecast, _ordered_yes(context, date))
    models["FROZEN_WEATHER_REFERENCE"] = {
        date: {row["market_ticker"]: context["probabilities"][(date, row["market_ticker"])]
               for row in _ordered_yes(context, date)} for date in score_dates}
    return models, score_dates


def _summary(losses: list[dict]) -> dict:
    return {"date_count": len(losses), "multiclass_brier": mean(row["brier"] for row in losses),
            "clipped_log_loss": mean(row["log_loss"] for row in losses)}


def _bootstrap_lower(values: list[float], *, block: int, resamples: int, seed: int) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    samples = []
    for _ in range(resamples):
        draw = []
        while len(draw) < n:
            start = rng.randrange(n)
            draw.extend(values[(start + offset) % n] for offset in range(block))
        samples.append(mean(draw[:n]))
    samples.sort()
    lower = samples[int(.05 * (resamples - 1))]
    p_value = (1 + sum(value <= 0 for value in samples)) / (resamples + 1)
    return lower, p_value


def run(root: Path, config: dict) -> dict:
    root = Path(root).resolve()
    context = load_development(root)
    models, score_dates = _model_probabilities(context)
    if len(score_dates) != config["development_forecast_scored_dates"]:
        raise ValueError("Registered development forecast scope differs")
    epsilon = float(config["log_loss_epsilon"])
    settlement = {}
    for candidate, by_date in models.items():
        losses = [_date_loss(context, date, by_date[date], epsilon) for date in score_dates]
        settlement[candidate] = {**_summary(losses), "daily_losses": losses}
    uniform = settlement["S00_UNIFORM_CONTROL"]
    climatology = settlement["S01_SOURCE_SEASON_CLIMATOLOGY"]
    frozen = settlement["FROZEN_WEATHER_REFERENCE"]
    for candidate in ("S02_HRRR_SOURCE_SPECIFIC", "S03_GEFS_SOURCE_SPECIFIC", "S04_CALIBRATED_HRRR_GEFS"):
        item = settlement[candidate]
        item["beats_uniform_both"] = item["multiclass_brier"] < uniform["multiclass_brier"] and item["clipped_log_loss"] < uniform["clipped_log_loss"]
        item["beats_climatology_both"] = item["multiclass_brier"] < climatology["multiclass_brier"] and item["clipped_log_loss"] < climatology["clipped_log_loss"]
        item["beats_frozen_weather_both"] = item["multiclass_brier"] < frozen["multiclass_brier"] and item["clipped_log_loss"] < frozen["clipped_log_loss"]
        item["settlement_gate_passed"] = all(item[key] for key in (
            "beats_uniform_both", "beats_climatology_both", "beats_frozen_weather_both"))

    market_artifact = checked(root / "data/development/v6_1/market_baseline_v2.json")
    market_records = {row["climate_date"]: row for row in market_artifact["records"]}
    paired_dates = [date for date in score_dates if date in market_records]
    if len(paired_dates) < config["minimum_market_relative_paired_dates"]:
        raise ValueError("Paid-book market intersection is below the registered minimum")
    s04 = models["S04_CALIBRATED_HRRR_GEFS"]
    market_daily, blend_daily = [], []
    improvements_brier, improvements_log = [], []
    for date in paired_dates:
        record = market_records[date]
        tickers = record["bracket_ids_in_settlement_order"]
        market_map = dict(zip(tickers, record["market_probabilities"]))
        blend_map = {ticker: (market_map[ticker] + s04[date][ticker]) / 2 for ticker in tickers}
        market_loss = _date_loss(context, date, market_map, epsilon)
        blend_loss = _date_loss(context, date, blend_map, epsilon)
        market_daily.append(market_loss); blend_daily.append(blend_loss)
        improvements_brier.append(market_loss["brier"] - blend_loss["brier"])
        improvements_log.append(market_loss["log_loss"] - blend_loss["log_loss"])
    bootstrap = config["bootstrap"]
    brier_lower, brier_p = _bootstrap_lower(improvements_brier, block=bootstrap["block_length_days"],
                                            resamples=bootstrap["resamples"], seed=bootstrap["seed"])
    log_lower, log_p = _bootstrap_lower(improvements_log, block=bootstrap["block_length_days"],
                                        resamples=bootstrap["resamples"], seed=bootstrap["seed"] + 1)
    fold_of = {date: min(4, index * 5 // len(score_dates)) for index, date in enumerate(score_dates)}
    folds = []
    for fold in range(5):
        indices = [index for index, date in enumerate(paired_dates) if fold_of[date] == fold]
        folds.append({"fold": fold + 1, "paired_dates": len(indices),
                      "mean_brier_improvement": mean(improvements_brier[i] for i in indices) if indices else None,
                      "mean_log_loss_improvement": mean(improvements_log[i] for i in indices) if indices else None,
                      "improves_both": bool(indices) and mean(improvements_brier[i] for i in indices) > 0
                      and mean(improvements_log[i] for i in indices) > 0})
    last_two = [index for index, date in enumerate(paired_dates) if fold_of[date] >= 3]
    last_two_both = bool(last_two) and mean(improvements_brier[i] for i in last_two) > 0 and mean(improvements_log[i] for i in last_two) > 0
    m01 = {
        "paired_date_count": len(paired_dates), "paired_dates": paired_dates,
        "market_only": _summary(market_daily), "candidate": _summary(blend_daily),
        "mean_brier_improvement": mean(improvements_brier),
        "mean_log_loss_improvement": mean(improvements_log),
        "brier_bootstrap_lower_95": brier_lower, "log_loss_bootstrap_lower_95": log_lower,
        "brier_one_sided_p": brier_p, "log_loss_one_sided_p": log_p,
        "folds": folds, "positive_both_fold_count": sum(row["improves_both"] for row in folds),
        "last_two_folds_combined_improve_both": last_two_both,
    }
    m01["market_gate_passed"] = (
        m01["mean_brier_improvement"] > 0 and m01["mean_log_loss_improvement"] > 0
        and brier_lower > 0 and log_lower > 0
        and m01["positive_both_fold_count"] >= config["minimum_market_relative_positive_folds"]
        and last_two_both)
    candidate_status = {}
    for candidate in config["catalog_ids"]:
        if candidate in {"S00_UNIFORM_CONTROL", "S01_SOURCE_SEASON_CLIMATOLOGY", "M00_MARKET_ONLY_CONTROL"}:
            status = "CONTROL_EVALUATED"
        elif candidate in {"S02_HRRR_SOURCE_SPECIFIC", "S03_GEFS_SOURCE_SPECIFIC", "S04_CALIBRATED_HRRR_GEFS"}:
            status = "SETTLEMENT_GATE_PASS" if settlement[candidate]["settlement_gate_passed"] else "SETTLEMENT_GATE_FAIL"
        elif candidate == "M01_MARKET_PLUS_CALIBRATED_WEATHER":
            status = "MARKET_GATE_PASS" if m01["market_gate_passed"] else "MARKET_GATE_FAIL"
        elif candidate in {"S05_LOCAL_OBSERVATION_CORRECTION", "S06_REGIME_CONDITIONAL_ENSEMBLE"}:
            status = "PENDING_OBSERVATION_FEATURE_IMPLEMENTATION"
        elif candidate.startswith("M"):
            status = "PENDING_OR_PREREQUISITE_BLOCKED"
        elif candidate.startswith("E"):
            status = "DIAGNOSTIC_ONLY_INSUFFICIENT_GRADE_A_SAMPLE"
        elif candidate == "A07_SYNTHETIC_OUTCOME_LEAK_SENTINEL":
            status = "CONTROL_REJECTED_BEFORE_SCORING_AS_REQUIRED"
        else:
            status = "PENDING_ADVERSARIAL_WAVE"
        candidate_status[candidate] = status
    return {
        "schema_version": "klax-v6.2-first-wave-v1",
        "settlement_results": settlement,
        "market_results": {"M00_MARKET_ONLY_CONTROL": m01["market_only"],
                           "M01_MARKET_PLUS_CALIBRATED_WEATHER": m01},
        "candidate_status": candidate_status,
        "evaluated_candidate_ids": sorted(candidate for candidate, status in candidate_status.items()
                                          if "EVALUATED" in status or "GATE_" in status or "CONTROL_REJECTED" in status),
        "grade_a_economics_mode": "DIAGNOSTIC_ONLY",
        "confirmation_authorized": False,
        "protected_confirmation_labels_read": False,
        "orders": 0,
    }

