"""Deterministic, development-only evaluation for V5C through V5F.

The evaluator has no acquisition, network, order, or protected-holdout API.
Every successor reuses the frozen V5B NO policy and the same 44 post-warmup
development dates.  Only the preregistered probability or abstention mechanism
changes between versions.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from statistics import mean

from friend_method.evaluation import bracket_probabilities
from v5b.campaign import checked, filehash
from v5b.evaluation import evaluate_candidate, load_development


FORECAST_ARTIFACT = (
    "runs/friend_method_exact_tests/"
    "friend-method-exact-20260928T053624502376Z/"
    "forecasts-kalshi_fixed_pst_f008_f031.json"
)
V5B_POINTER = "runs/v5b_current_campaign.json"
VERSIONS = ("V5C", "V5D", "V5E", "V5F")


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid numeric value: {name}")
    return float(value)


def _normalise(values: dict[str, float]) -> dict[str, float]:
    if not values or any(not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError("invalid probability vector")
    total = sum(values.values())
    if total <= 0:
        raise ValueError("zero probability mass")
    result = {key: value / total for key, value in values.items()}
    if abs(sum(result.values()) - 1.0) > 1e-10:
        raise ValueError("probability normalization failed")
    return result


def validate_config(config: dict) -> dict:
    expected = {
        "schema_version", "version", "method", "description", "common_scoring_dates",
        "warmup_dates", "allow_network", "allow_orders", "allow_protected_labels",
        "parameters",
    }
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("successor config fields differ")
    if config["version"] not in VERSIONS or config["common_scoring_dates"] != 44 or config["warmup_dates"] != 20:
        raise ValueError("successor cohort differs")
    if any(config[key] is not False for key in ("allow_network", "allow_orders", "allow_protected_labels")):
        raise ValueError("offline boundaries must be false")
    methods = {
        "V5C": "consensus_veto",
        "V5D": "disagreement_abstention",
        "V5E": "heavy_tail_probability",
        "V5F": "fixed_stacked_probability",
    }
    if config["method"] != methods[config["version"]] or not isinstance(config["description"], str):
        raise ValueError("version/method mismatch")
    p = config["parameters"]
    if not isinstance(p, dict):
        raise ValueError("parameters must be an object")
    if config["version"] == "V5C":
        if set(p) != {"maximum_friend_yes_probability"} or not 0 < _finite(p["maximum_friend_yes_probability"], "maximum_friend_yes_probability") < 1:
            raise ValueError("invalid V5C consensus threshold")
    elif config["version"] == "V5D":
        if set(p) != {"maximum_probability_gap", "maximum_source_high_range_f"}:
            raise ValueError("invalid V5D parameter set")
        if not 0 < _finite(p["maximum_probability_gap"], "maximum_probability_gap") < 1:
            raise ValueError("invalid V5D probability gap")
        if not 0 < _finite(p["maximum_source_high_range_f"], "maximum_source_high_range_f") <= 20:
            raise ValueError("invalid V5D source range")
    elif config["version"] == "V5E":
        if set(p) != {"wide_component_weight", "wide_sigma_multiplier"}:
            raise ValueError("invalid V5E parameter set")
        if not 0 < _finite(p["wide_component_weight"], "wide_component_weight") < 1:
            raise ValueError("invalid V5E mixture weight")
        if not 1 < _finite(p["wide_sigma_multiplier"], "wide_sigma_multiplier") <= 5:
            raise ValueError("invalid V5E sigma multiplier")
    elif config["version"] == "V5F":
        if set(p) != {"v5b_weight", "friend_heavy_tail_weight", "wide_component_weight", "wide_sigma_multiplier"}:
            raise ValueError("invalid V5F parameter set")
        weights = _finite(p["v5b_weight"], "v5b_weight") + _finite(p["friend_heavy_tail_weight"], "friend_heavy_tail_weight")
        if abs(weights - 1.0) > 1e-12 or min(p["v5b_weight"], p["friend_heavy_tail_weight"]) <= 0:
            raise ValueError("V5F blend weights must be positive and sum to one")
        if not 0 < _finite(p["wide_component_weight"], "wide_component_weight") < 1:
            raise ValueError("invalid V5F mixture weight")
        if not 1 < _finite(p["wide_sigma_multiplier"], "wide_sigma_multiplier") <= 5:
            raise ValueError("invalid V5F sigma multiplier")
    return json.loads(json.dumps(config))


def load_context(root: str | Path) -> dict:
    root = Path(root).resolve()
    market = load_development(root)
    pointer = json.loads((root / V5B_POINTER).read_text(encoding="utf-8-sig"))
    run = (root / pointer["run_path"]).resolve()
    if not run.is_relative_to((root / "runs/campaigns_v5b").resolve()):
        raise ValueError("V5B run path escaped")
    freeze = checked(run / "strategy-freeze.json")
    candidate = checked(run / "candidates" / f"{freeze['candidate_id']}.json")
    candidate_path = run / "candidates" / f"{freeze['candidate_id']}.json"
    if filehash(candidate_path) != freeze["candidate_sha256"] or candidate["parameters"] != freeze["parameters"]:
        raise ValueError("frozen V5B strategy binding differs")
    artifact = checked(root / FORECAST_ARTIFACT)
    forecasts = artifact["forecasts"]
    scoring_dates = market["dates"][20:]
    if len(market["dates"]) != 64 or len(scoring_dates) != 44:
        raise ValueError("development chronology differs")
    if [row["climate_date"] for row in forecasts] != scoring_dates:
        raise ValueError("friend forecast/common-date alignment differs")
    by_date = {row["climate_date"]: row for row in forecasts}
    for date in scoring_dates:
        yes = [row for row in market["rows_by_date"][date] if row["contract_side"] == "YES"]
        tickers = {row["market_ticker"] for row in yes}
        if tickers != set(by_date[date]["central_probabilities"]):
            raise ValueError(f"friend/V5B ticker universe differs: {date}")
    return {
        "root": root,
        "market": market,
        "scoring_dates": scoring_dates,
        "forecasts": by_date,
        "baseline_parameters": freeze["parameters"],
        "v5b_campaign_id": freeze["campaign_id"],
        "v5b_candidate_id": freeze["candidate_id"],
        "v5b_candidate_sha256": freeze["candidate_sha256"],
        "v5b_candidate_artifact_self_sha256": candidate["self_sha256"],
        "friend_forecast_sha256": artifact["self_sha256"],
    }


def _contracts(context: dict, date: str) -> list[dict]:
    return sorted(
        [row for row in context["market"]["rows_by_date"][date] if row["contract_side"] == "YES"],
        key=lambda row: (-10000 if row["floor_strike"] is None else float(row["floor_strike"]), row["market_ticker"]),
    )


def _baseline_probabilities(context: dict) -> dict[tuple[str, str], float]:
    return {
        (date, ticker): context["market"]["probabilities"][(date, ticker)]
        for date in context["scoring_dates"]
        for ticker in context["forecasts"][date]["central_probabilities"]
    }


def _friend_probabilities(context: dict, wide_weight: float = 0.0, wide_multiplier: float = 2.0) -> dict[tuple[str, str], float]:
    output: dict[tuple[str, str], float] = {}
    for date in context["scoring_dates"]:
        forecast = context["forecasts"][date]
        central = _normalise({k: float(v) for k, v in forecast["central_probabilities"].items()})
        if wide_weight:
            wide = bracket_probabilities(
                _finite(forecast["location_f"], "location_f"),
                _finite(forecast["sigma_f"], "sigma_f") * wide_multiplier,
                _contracts(context, date),
            )
            central = _normalise({
                ticker: (1.0 - wide_weight) * central[ticker] + wide_weight * wide[ticker]
                for ticker in central
            })
        for ticker, value in central.items():
            output[(date, ticker)] = value
    return output


def _masked_rows(context: dict, veto: set[tuple[str, str]]) -> dict[str, list[dict]]:
    output = {}
    for date in context["scoring_dates"]:
        output[date] = []
        for row in context["market"]["rows_by_date"][date]:
            item = deepcopy(row)
            if (date, row["market_ticker"]) in veto:
                item["execution_price_cents"] = None
                item["bid_price_cents"] = None
            output[date].append(item)
    return output


def _score(context: dict, probabilities: dict[tuple[str, str], float], rows_by_date: dict[str, list[dict]] | None = None) -> dict:
    dates = context["scoring_dates"]
    scoring = {
        **context["market"],
        "dates": dates,
        "labels": {date: context["market"]["labels"][date] for date in dates},
        "rows_by_date": rows_by_date or {date: context["market"]["rows_by_date"][date] for date in dates},
        "probabilities": probabilities,
        "raw_probabilities": probabilities,
        "partition": "development",
    }
    return evaluate_candidate(scoring, context["baseline_parameters"])


def _selection_calibration(result: dict) -> dict:
    trades = result["trades"]
    if not trades:
        return {"selected_trades": 0, "wins": 0, "win_rate": 0.0,
                "mean_predicted_side_probability": 0.0, "probability_minus_win_rate": 0.0}
    predicted = mean(float(row["model_probability"]) for row in trades)
    win_rate = mean(int(bool(row["won"])) for row in trades)
    return {"selected_trades": len(trades), "wins": sum(bool(row["won"]) for row in trades),
            "win_rate": win_rate, "mean_predicted_side_probability": predicted,
            "probability_minus_win_rate": predicted - win_rate}


def evaluate_version(context: dict, config: dict, baseline: dict | None = None) -> dict:
    config = validate_config(config)
    baseline_probs = _baseline_probabilities(context)
    baseline = baseline or _score(context, baseline_probs)
    method = config["method"]
    veto: set[tuple[str, str]] = set()
    probabilities = baseline_probs
    diagnostics: dict = {}
    if method == "consensus_veto":
        friend = _friend_probabilities(context)
        threshold = float(config["parameters"]["maximum_friend_yes_probability"])
        veto = {key for key, value in friend.items() if value > threshold}
        diagnostics = {"maximum_friend_yes_probability": threshold, "vetoed_contract_dates": len(veto)}
    elif method == "disagreement_abstention":
        friend = _friend_probabilities(context)
        gap = float(config["parameters"]["maximum_probability_gap"])
        source_limit = float(config["parameters"]["maximum_source_high_range_f"])
        high_range_by_date = {
            date: max(context["forecasts"][date]["source_highs_f"].values()) - min(context["forecasts"][date]["source_highs_f"].values())
            for date in context["scoring_dates"]
        }
        veto = {
            key for key in friend
            if abs(baseline_probs[key] - friend[key]) > gap or high_range_by_date[key[0]] > source_limit
        }
        diagnostics = {
            "maximum_probability_gap": gap,
            "maximum_source_high_range_f": source_limit,
            "vetoed_contract_dates": len(veto),
            "dates_above_source_range": sum(value > source_limit for value in high_range_by_date.values()),
            "mean_source_high_range_f": mean(high_range_by_date.values()),
        }
    elif method == "heavy_tail_probability":
        p = config["parameters"]
        probabilities = _friend_probabilities(context, float(p["wide_component_weight"]), float(p["wide_sigma_multiplier"]))
        diagnostics = dict(p)
    elif method == "fixed_stacked_probability":
        p = config["parameters"]
        friend = _friend_probabilities(context, float(p["wide_component_weight"]), float(p["wide_sigma_multiplier"]))
        probabilities = {
            key: float(p["v5b_weight"]) * baseline_probs[key] + float(p["friend_heavy_tail_weight"]) * friend[key]
            for key in baseline_probs
        }
        diagnostics = dict(p)
    else:
        raise ValueError("unsupported successor method")
    rows = _masked_rows(context, veto) if veto else None
    result = _score(context, probabilities, rows)
    result["baseline_multiclass_brier"] = baseline["multiclass_brier"]
    result["schema_version"] = "v5-successor-development-result-v1"
    result["successor_version"] = config["version"]
    result["successor_method"] = method
    result["successor_parameters"] = config["parameters"]
    result["frozen_v5b_parameters"] = context["baseline_parameters"]
    result["common_scoring_dates"] = context["scoring_dates"]
    result["warmup_dates"] = context["market"]["dates"][:20]
    result["diagnostics"] = diagnostics
    result["selection_calibration"] = _selection_calibration(result)
    result["comparison_to_common_v5b"] = {
        "return_delta": result["aggregate_realized_net_return"] - baseline["aggregate_realized_net_return"],
        "selected_day_delta": result["selected_days"] - baseline["selected_days"],
        "positive_fold_delta": result["positive_fold_count"] - baseline["positive_fold_count"],
        "worst_fold_delta": result["worst_nonempty_fold_return"] - baseline["worst_nonempty_fold_return"],
        "brier_delta": result["multiclass_brier"] - baseline["multiclass_brier"],
    }
    result["v5b_campaign_id"] = context["v5b_campaign_id"]
    result["v5b_candidate_id"] = context["v5b_candidate_id"]
    result["friend_forecast_sha256"] = context["friend_forecast_sha256"]
    result["development_only"] = True
    result["protected_confirmation_labels_read"] = False
    result["actual_orders_placed"] = False
    result["limitations"] = [
        "All 44 scoring dates have already been exposed to prior research; this is comparative development evidence only.",
        "The 28-day protected holdout remains sealed and was not read.",
        "B and B+ quote evidence does not establish historical fills or queue position.",
        "Successor thresholds and weights are fixed research hypotheses, not optimized estimates.",
        "The common 44-date period is too short to confirm a sustainable ten-percent return.",
    ]
    json.dumps(result, allow_nan=False)
    return result


def evaluate_suite(root: str | Path, configs: list[dict]) -> dict:
    context = load_context(root)
    validated = [validate_config(config) for config in configs]
    if [config["version"] for config in validated] != list(VERSIONS):
        raise ValueError("successor configs must be ordered V5C through V5F")
    baseline = _score(context, _baseline_probabilities(context))
    baseline["selection_calibration"] = _selection_calibration(baseline)
    results = {config["version"]: evaluate_version(context, config, baseline) for config in validated}
    return {"baseline": baseline, "results": results,
            "common_scoring_dates": context["scoring_dates"],
            "v5b_campaign_id": context["v5b_campaign_id"],
            "v5b_candidate_id": context["v5b_candidate_id"],
            "v5b_candidate_sha256": context["v5b_candidate_sha256"],
            "friend_forecast_sha256": context["friend_forecast_sha256"]}

