"""Deterministic seven-day replay with a hard prediction/outcome boundary.

The prediction phase has no outcome path or outcome loader.  It freezes the
original 64-date June 1-August 3 training state and predicts every target date
independently, so September observations can never update a later target.
Only ``score_prediction_freeze`` accepts a resolved-outcome path.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

import numpy as np
import pandas as pd

from friend_method.evaluation import bracket_probabilities as gaussian_bracket_probabilities
from friend_method.evaluation import fit_variance
from friend_method.exact_evaluation import (
    _date_seed,
    _prequential_records,
    fit_location as fit_friend_location,
    load_exact_features,
)
from v5a.development_search import _fee
from v5b.evaluation import DEFAULTS as V5B_DEFAULTS
from v5b.evaluation import _adjust, validate_spec
from v5b_next.weather_evaluation import FEATURE_NAMES
from v5b_next.weather_features import aggregate_day
from v5b_next.weather_model import _fit_predict, bracket_probabilities, quantile, utc


TARGET_DATES = tuple(f"2026-09-{day:02d}" for day in range(21, 28))
TRAINING_START = "2026-06-01"
TRAINING_END = "2026-08-03"
REPLAY_RELATIVE = Path("runs/replays/past7-20260921-20260927")
FRIEND_TARGET_RELATIVE = Path("data/raw/friend_method_weather_v1")
OUTPUT_RELATIVE = Path("outputs")

UNIVERSE = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
V5B_PROBABILITIES = Path("data/normalized/v5b_untouched/frozen_leader_probabilities.parquet")
V5B_SELECTION_FREEZE = Path("data/manifests/v5b_untouched_selection_freeze.json")
V5B_STRATEGY_FREEZE = Path(
    "runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json"
)
TRAINING_FEATURES = Path("data/development/v5b_next/weather_features.json")
TRAINING_LABELS = Path("data/development/v5a/development_labels.json")
FRIEND_CONFIG = Path("configs/friend_method_exact_test.json")
V5F_CONFIG = Path("configs/v5f_stacked.json")
V6_CANDIDATE = Path(
    "runs/campaigns_v6/v6-development-20260927T214208879719Z/candidates/"
    "6c7dcb09ece3c69cee32e0bcba5de4c5ed34632bcc7274e0e1e7d76be199d896.json"
)

ORDERBOOK_RELATIVE = Path("raw/probalytics/orderbook_1800_verified.csv")
OUTCOMES_RELATIVE = Path("raw/probalytics/resolved_markets.tsv")
PREDICTION_JSON = Path("outputs/prediction_selection_freeze.json")
PREDICTION_CSV = Path("outputs/predictions.csv")
SELECTION_CSV = Path("outputs/selections.csv")
SCORED_JSON = Path("outputs/scored_results.json")
SCORED_CSV = Path("outputs/scored_results.csv")
SUMMARY_CSV = Path("outputs/method_summary.csv")

NAMED_METHODS = (
    "v5b_frozen_no",
    "v6_gefs_spread_equal_blend_no",
    "friend_exact_primary_gfs_nam_nbm",
    "v5f_cross_family_stack",
)
DIAGNOSTIC_METHODS = (
    "diagnostic_gfs_only",
    "diagnostic_nam_only",
    "diagnostic_nbm_only",
)
ALL_METHODS = NAMED_METHODS + DIAGNOSTIC_METHODS


class ReplayError(ValueError):
    """The replay cannot proceed without weakening a frozen invariant."""


def _canonical_hash(value: dict, field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _seal(value: dict) -> dict:
    result = json.loads(json.dumps(value, allow_nan=False))
    result["self_sha256"] = _canonical_hash(result)
    return result


def _verify_seal(value: dict) -> None:
    if value.get("self_sha256") != _canonical_hash(value):
        raise ReplayError("sealed JSON hash differs")


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, *, sealed: bool = False) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ReplayError(f"JSON object required: {path}")
    if sealed:
        _verify_seal(value)
    return value


def _write_immutable_json(path: Path, value: dict) -> None:
    content = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ReplayError(f"immutable replay output differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(content, encoding="utf-8", newline="\n")
    pending.replace(path)


def _write_immutable_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    from io import StringIO

    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue()
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ReplayError(f"immutable replay CSV differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(content, encoding="utf-8", newline="")
    pending.replace(path)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _probability_decimal(value: float) -> Decimal:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ReplayError("probability must be finite and in [0,1]")
    return Decimal(str(value))


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _ordered_yes(rows: Iterable[dict]) -> list[dict]:
    result = sorted(
        (row for row in rows if row["contract_side"] == "YES"),
        key=lambda row: (
            -10000 if row["floor_strike"] is None else float(row["floor_strike"]),
            row["market_ticker"],
        ),
    )
    if len(result) != 6 or len({row["market_ticker"] for row in result}) != 6:
        raise ReplayError("target date must have six unique YES brackets")
    return result


def _load_target_universe(root: Path) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    universe = _read_json(root / UNIVERSE, sealed=True)
    if universe.get("status") != "FROZEN_OUTCOME_BLIND" or universe.get("outcomes_read") is not False:
        raise ReplayError("target universe is not outcome blind")
    rows_by_date = {day: [] for day in TARGET_DATES}
    for row in universe["records"]:
        if row["climate_date"] in rows_by_date:
            rows_by_date[row["climate_date"]].append(row)
    contracts: dict[str, dict] = {}
    for day in TARGET_DATES:
        rows = rows_by_date[day]
        yes = _ordered_yes(rows)
        if len(rows) != 12 or {row["contract_side"] for row in rows} != {"YES", "NO"}:
            raise ReplayError(f"incomplete target universe: {day}")
        for row in yes:
            sources = row.get("settlement_sources") or []
            if len(sources) != 1 or sources[0].get("name") != "The Weather Company":
                raise ReplayError("target settlement source differs")
            contracts[row["market_ticker"]] = row
    return rows_by_date, contracts


def _cents(value: str, name: str) -> int:
    amount = Decimal(value) * Decimal(100)
    if amount != amount.to_integral_value() or not 0 <= amount <= 100:
        raise ReplayError(f"invalid cent price: {name}")
    return int(amount)


def _load_market_evidence(
    replay_root: Path, rows_by_date: dict[str, list[dict]]
) -> dict[tuple[str, str], dict]:
    path = replay_root / ORDERBOOK_RELATIVE
    expected = {
        "market_ticker", "outcome_side", "quote_at_utc", "best_bid",
        "best_bid_size", "best_ask", "best_ask_size",
    }
    universe_rows = {
        (row["market_ticker"], row["contract_side"]): row
        for rows in rows_by_date.values() for row in rows
    }
    result: dict[tuple[str, str], dict] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or []) != expected:
            raise ReplayError("paid orderbook schema differs")
        for raw in reader:
            side = raw["outcome_side"].upper()
            key = (raw["market_ticker"], side)
            if key not in universe_rows or key in result:
                raise ReplayError("unknown or duplicate paid orderbook row")
            day = universe_rows[key]["climate_date"]
            quote = _timestamp(raw["quote_at_utc"])
            decision = _timestamp(day + "T18:00:00+00:00")
            if not decision <= quote <= decision + timedelta(seconds=5):
                raise ReplayError("quote lies outside the fixed +5 second arrival window")
            bid, ask = _cents(raw["best_bid"], "best_bid"), _cents(raw["best_ask"], "best_ask")
            if not 0 <= bid < ask <= 100:
                raise ReplayError("paid book is crossed or empty")
            bid_size, ask_size = Decimal(raw["best_bid_size"]), Decimal(raw["best_ask_size"])
            if bid_size <= 0 or ask_size <= 0:
                raise ReplayError("paid book sizes must be positive")
            result[key] = {
                "market_ticker": key[0], "contract_side": side,
                "quote_at_utc": quote.isoformat(), "bid_price_cents": bid,
                "entry_price_cents": ask, "bid_size": _decimal_text(bid_size),
                "ask_size": _decimal_text(ask_size),
                # The supplied file is a fresh, verified paid full-book extract
                # at the registered arrival.  Its levels can differ from the
                # older V5B freeze, which remains a separate immutable control.
                "execution_evidence_grade": "A",
                "execution_source_kind": "PROBALYTICS_PAID_FULL_BOOK_VERIFIED_PLUS5S",
                "assumed_fill": False,
            }
    return result


def _frozen_universe_evidence(rows_by_date: dict[str, list[dict]]) -> dict[tuple[str, str], dict]:
    """Reconstruct only the evidence that was available to the frozen V5B selector."""
    output = {}
    for rows in rows_by_date.values():
        for row in rows:
            ask, bid = row.get("execution_price_cents"), row.get("bid_price_cents")
            if ask is None:
                continue
            output[row["market_ticker"], row["contract_side"]] = {
                "market_ticker": row["market_ticker"], "contract_side": row["contract_side"],
                "entry_price_cents": int(ask), "bid_price_cents": int(bid) if bid is not None else -100,
                "bid_size": "", "ask_size": "", "quote_at_utc": row.get("quote_at_utc"),
                "execution_evidence_grade": row["execution_evidence_grade"],
                "execution_source_kind": row["execution_source_kind"],
                "assumed_fill": bool(row["assumed_fill"]),
            }
    return output


def _load_training(root: Path) -> tuple[list[dict], list[dict]]:
    features = _read_json(root / TRAINING_FEATURES)["features"]
    labels = _read_json(root / TRAINING_LABELS, sealed=True)["labels"]
    dates = [row["climate_date"] for row in features]
    if (
        len(dates) != 64 or dates != sorted(set(dates))
        or dates[0] != TRAINING_START or dates[-1] != TRAINING_END
        or [row["climate_date"] for row in labels] != dates
        or set(dates) & set(TARGET_DATES)
    ):
        raise ReplayError("original 64-date training state differs")
    for row in labels:
        if utc(row["issued_at"]) <= utc(row["climate_date"] + "T18:00:00+00:00"):
            raise ReplayError("training label was available before its forecast decision")
    return features, labels


def _load_v6_target_features(root: Path) -> tuple[dict[str, dict], dict[str, str]]:
    output, bindings = {}, {}
    for day in TARGET_DATES:
        folder = root / "data/normalized/v5p_probability_features" / f"date={day}"
        manifest_path = folder / "manifest.json"
        manifest = _read_json(manifest_path)
        if (
            manifest.get("climate_date") != day or manifest.get("decision_time_utc") != "18:00"
            or manifest.get("status") != "NORMALIZED_FEATURES_ONLY"
            or manifest.get("protected_confirmation_labels_read") is not False
        ):
            raise ReplayError("unsafe target HRRR/GEFS manifest")
        frames = {}
        for model, filename in (("hrrr", "hrrr_points.parquet"), ("gefs", "gefs_summary_points.parquet")):
            path = folder / filename
            relative = path.relative_to(root).as_posix()
            expected = next(item for item in manifest["outputs"] if item["path"] == relative)
            if _file_hash(path) != expected["sha256"] or path.stat().st_size != expected["bytes"]:
                raise ReplayError("target normalized weather binding differs")
            frames[model] = pd.read_parquet(path)
            bindings[relative] = _file_hash(path)
        output[day] = aggregate_day(frames["hrrr"], frames["gefs"], day)
        bindings[manifest_path.relative_to(root).as_posix()] = _file_hash(manifest_path)
    return output, bindings


def _v6_frozen_forecasts(
    training_features: list[dict], labels: list[dict], targets: dict[str, dict],
    contracts_by_date: dict[str, list[dict]],
) -> dict[str, dict]:
    labels_by_date = {row["climate_date"]: row for row in labels}
    prior_predictions: dict[str, tuple[float, float]] = {}
    for index, current in enumerate(training_features):
        decision = utc(current["decision_at"])
        prior = [
            row for row in training_features[:index]
            if utc(labels_by_date[row["climate_date"]]["issued_at"]) < decision
        ]
        if len(prior) < 10:
            continue
        outcomes = [labels_by_date[row["climate_date"]]["reported_high_f"] for row in prior]
        location, _ = _fit_predict(prior, outcomes, current, "equal_blend_bias", FEATURE_NAMES)
        scale = float(np.clip(float(current["gefs_spread_delta_f_at_mean_max"]), 0.5, 10.0))
        prior_predictions[current["climate_date"]] = (location, scale)
    output = {}
    for day in TARGET_DATES:
        current = targets[day]
        decision = utc(current["decision_at"])
        prior = [
            row for row in training_features
            if utc(labels_by_date[row["climate_date"]]["issued_at"]) < decision
        ]
        if len(prior) != 64:
            raise ReplayError("target V6 forecast did not use the frozen 64-date state")
        outcomes = [labels_by_date[row["climate_date"]]["reported_high_f"] for row in prior]
        location, fit = _fit_predict(prior, outcomes, current, "equal_blend_bias", FEATURE_NAMES)
        scale = float(np.clip(float(current["gefs_spread_delta_f_at_mean_max"]), 0.5, 10.0))
        error_rows = [row for row in prior if row["climate_date"] in prior_predictions]
        standardized = [
            (float(labels_by_date[row["climate_date"]]["reported_high_f"])
             - prior_predictions[row["climate_date"]][0])
            / prior_predictions[row["climate_date"]][1]
            for row in error_rows
        ]
        if len(standardized) < 10:
            raise ReplayError("V6 frozen state lacks prequential residuals")
        residuals = [float(value * scale) for value in standardized]
        forecast = {
            "climate_date": day, "decision_at": current["decision_at"],
            "method_id": "gefs_spread_equal_blend", "location_f": float(location),
            "scale_f": scale, "kernel_sigma_f": 1.0,
            "prequential_residuals_f": residuals,
            "standardized_prequential_residuals": standardized,
            "predictive_mean_f": float(location + np.mean(residuals)),
            "training_dates": [row["climate_date"] for row in prior],
            "training_count": len(prior),
            "residual_dates": [row["climate_date"] for row in error_rows],
            "residual_count": len(error_rows), "fit_metadata": fit,
            "scale_clip_f": [0.5, 10.0], "target_updates_used": False,
        }
        forecast["interval_80_f"] = [quantile(forecast, 0.1), quantile(forecast, 0.9)]
        forecast["interval_95_f"] = [quantile(forecast, 0.025), quantile(forecast, 0.975)]
        forecast["central_probabilities"] = bracket_probabilities(forecast, contracts_by_date[day])
        output[day] = forecast
    return output


def _friend_config(root: Path) -> tuple[dict, dict]:
    config = _read_json(root / FRIEND_CONFIG)
    window = next(item for item in config["weather"]["windows"] if item["role"] == "primary")
    if window != {
        "id": "kalshi_fixed_pst_f008_f031", "nbm_daily_high_field": "daily_high_f", "role": "primary"
    }:
        raise ReplayError("friend primary weather window differs")
    return config, window


def _validate_friend_target_archive(replay_root: Path) -> dict[str, str]:
    archive = replay_root / FRIEND_TARGET_RELATIVE
    manifest_path = archive / "manifest.json"
    if not manifest_path.is_file():
        raise ReplayError("isolated friend target weather manifest is not ready")
    manifest = _read_json(manifest_path, sealed=True)
    if (
        manifest.get("date_start") != TARGET_DATES[0]
        or manifest.get("date_end") != TARGET_DATES[-1]
        or manifest.get("calendar_days") != 7 or manifest.get("complete_days") != 7
        or manifest.get("failed_tasks")
        or manifest.get("protected_confirmation_labels_read") is not False
        or manifest.get("actual_orders_placed") is not False
    ):
        raise ReplayError("isolated friend target weather is incomplete or unsafe")
    bindings = {manifest_path.relative_to(replay_root).as_posix(): _file_hash(manifest_path)}
    for day in TARGET_DATES:
        daily = archive / f"date={day}" / "daily.json"
        value = _read_json(daily)
        if value.get("climate_date") != day or not value.get("complete") or value.get("contains_settlement_label"):
            raise ReplayError(f"unsafe friend target weather day: {day}")
        bindings[daily.relative_to(replay_root).as_posix()] = _file_hash(daily)
    return bindings


def _friend_forecasts(
    root: Path, replay_root: Path, training_dates: list[str], labels: list[dict],
    contracts_by_date: dict[str, list[dict]],
) -> tuple[dict[str, dict], dict[str, dict[str, dict]]]:
    config, window = _friend_config(root)
    training = load_exact_features(root, training_dates, window)
    targets = load_exact_features(replay_root, list(TARGET_DATES), window)
    outcomes = [float(row["reported_high_f"]) for row in labels]
    ridge = float(config["forecast"]["simplex_ridge_lambda"])
    floor = float(config["forecast"]["sigma_floor_f"])
    refits = int(config["forecast"]["bootstrap_refits"])
    base_seed = int(config["forecast"]["bootstrap_seed"])
    probability_quantile = float(config["forecast"]["conservative_probability_quantile"])
    prequential = _prequential_records(training, outcomes, ridge)
    combined, diagnostics = {}, {source: {} for source in ("gfs", "nam", "nbm")}
    for current in targets:
        day = current["climate_date"]
        contracts = contracts_by_date[day]
        location, location_fit = fit_friend_location(training, outcomes, current, ridge)
        sigma, variance_fit = fit_variance(prequential, location_fit["between_source_variance_f2"], floor)
        central = gaussian_bracket_probabilities(location, sigma, contracts)
        scenarios = {ticker: [] for ticker in central}
        rng = np.random.default_rng(_date_seed(base_seed, day))
        for _ in range(refits):
            selected = rng.integers(0, len(training), len(training))
            rows = [training[int(position)] for position in selected]
            sampled = [outcomes[int(position)] for position in selected]
            scenario_location, _ = fit_friend_location(rows, sampled, current, ridge)
            scenario = gaussian_bracket_probabilities(scenario_location, sigma, contracts)
            for ticker, value in scenario.items():
                scenarios[ticker].append(value)
        conservative = {
            ticker: float(np.quantile(values, probability_quantile, method="linear"))
            for ticker, values in scenarios.items()
        }
        combined[day] = {
            "climate_date": day, "decision_at": current["decision_at"],
            "window_id": window["id"],
            "source_highs_f": {source: current[source] for source in ("gfs", "nam", "nbm")},
            "location_f": location, "sigma_f": sigma,
            "central_probabilities": central, "conservative_probabilities": conservative,
            "location_fit": location_fit, "variance_fit": variance_fit,
            "training_dates": list(training_dates), "training_count": len(training_dates),
            "target_updates_used": False,
        }
        for source in diagnostics:
            diagnostics[source][day] = _single_source_forecast(
                source, training, outcomes, current, contracts, floor,
                refits, base_seed, probability_quantile,
            )
    return combined, diagnostics


def _single_source_forecast(
    source: str, training: list[dict], outcomes: list[float], current: dict,
    contracts: list[dict], sigma_floor: float, refits: int, base_seed: int,
    probability_quantile: float,
) -> dict:
    errors = []
    for index in range(10, len(training)):
        bias = mean(float(row[source]) - outcomes[pos] for pos, row in enumerate(training[:index]))
        prediction = float(training[index][source]) - bias
        errors.append({"error_f": outcomes[index] - prediction, "spread_variance_f2": 0.0})
    bias = mean(float(row[source]) - outcome for row, outcome in zip(training, outcomes))
    location = float(current[source]) - bias
    sigma, variance_fit = fit_variance(errors, 0.0, sigma_floor)
    central = gaussian_bracket_probabilities(location, sigma, contracts)
    scenarios = {ticker: [] for ticker in central}
    rng = np.random.default_rng(_date_seed(base_seed, current["climate_date"]))
    for _ in range(refits):
        selected = rng.integers(0, len(training), len(training))
        sampled_bias = mean(
            float(training[int(position)][source]) - outcomes[int(position)] for position in selected
        )
        scenario = gaussian_bracket_probabilities(float(current[source]) - sampled_bias, sigma, contracts)
        for ticker, value in scenario.items():
            scenarios[ticker].append(value)
    conservative = {
        ticker: float(np.quantile(values, probability_quantile, method="linear"))
        for ticker, values in scenarios.items()
    }
    return {
        "climate_date": current["climate_date"], "decision_at": current["decision_at"],
        "exploratory_diagnostic": True, "source": source,
        "source_high_f": float(current[source]), "bias_f": float(bias),
        "location_f": location, "sigma_f": sigma,
        "central_probabilities": central, "conservative_probabilities": conservative,
        "variance_fit": variance_fit,
        "training_dates": [row["climate_date"] for row in training],
        "training_count": len(training), "target_updates_used": False,
    }


def _probability_rows(probabilities: dict[str, float], contracts: list[dict]) -> list[dict]:
    result = []
    for order, contract in enumerate(contracts):
        ticker = contract["market_ticker"]
        value = float(probabilities[ticker])
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ReplayError("invalid bracket probability")
        result.append({
            "contract_order": order, "market_ticker": ticker,
            "strike_type": contract["strike_type"],
            "floor_strike": contract["floor_strike"], "cap_strike": contract["cap_strike"],
            "yes_probability": value, "no_probability": 1.0 - value,
        })
    if abs(sum(row["yes_probability"] for row in result) - 1.0) > 1e-8:
        raise ReplayError("probability vector is not coherent")
    return result


def _selection_record(
    day: str, contract: dict, evidence: dict, probability: float,
    expected_probability: float | None = None,
) -> dict:
    price = int(evidence["entry_price_cents"])
    fee = _fee(day, price)
    outlay = Decimal(price) / Decimal(100) + fee
    expected = _probability_decimal(probability if expected_probability is None else expected_probability)
    profit = expected - outlay
    return {
        "status": "SELECTED", "reason": None,
        "market_ticker": contract["market_ticker"],
        "contract_side": evidence["contract_side"],
        "entry_price_cents": price, "bid_price_cents": int(evidence["bid_price_cents"]),
        "spread_cents": price - int(evidence["bid_price_cents"]),
        "bid_size": evidence["bid_size"], "ask_size": evidence["ask_size"],
        "quote_at_utc": evidence["quote_at_utc"],
        "execution_evidence_grade": evidence["execution_evidence_grade"],
        "execution_source_kind": evidence["execution_source_kind"],
        "assumed_fill": evidence["assumed_fill"], "quantity": 1,
        "model_probability": float(probability),
        "selection_probability": float(expected),
        "fee_dollars": _decimal_text(fee),
        "entry_outlay_dollars": _decimal_text(outlay),
        "expected_profit_dollars": _decimal_text(profit),
        "expected_net_return": _decimal_text(profit / outlay),
    }


def _abstention(reason: str, unavailable: list[str] | None = None) -> dict:
    return {
        "status": "ABSTAINED", "reason": reason,
        "unavailable_market_sides": sorted(unavailable or []), "quantity": 0,
    }


def _select_no(
    day: str, probabilities: dict[str, float], contracts: list[dict],
    evidence: dict[tuple[str, str], dict], parameters: dict,
) -> dict:
    p = validate_spec(parameters)
    adjusted = _adjust([probabilities[row["market_ticker"]] for row in contracts], p)
    ordered = sorted(adjusted, reverse=True)
    entropy = -sum(value * math.log(value) for value in adjusted if value > 0) / math.log(len(adjusted))
    if entropy > p["maximum_entropy"] + 1e-12:
        return _abstention("ENTROPY")
    if ordered[0] - ordered[1] < p["minimum_probability_gap"]:
        return _abstention("PROBABILITY_GAP")
    adjusted_by_ticker = {row["market_ticker"]: value for row, value in zip(contracts, adjusted)}
    candidates, unavailable = [], []
    for contract in contracts:
        ticker = contract["market_ticker"]
        item = evidence.get((ticker, "NO"))
        if item is None:
            unavailable.append(ticker + ":NO")
            continue
        grade = item["execution_evidence_grade"]
        if grade not in p["allowed_grades"] or "NO" not in p["allowed_sides"]:
            continue
        tail = contract["strike_type"] in {"less", "greater"}
        if (p["tail_policy"] == "tails" and not tail) or (p["tail_policy"] == "interior" and tail):
            continue
        price = item["entry_price_cents"] + p["additional_adverse_price_cents"]
        spread = item["entry_price_cents"] - item["bid_price_cents"]
        if (
            not p["minimum_price_cents"] <= price <= p["maximum_price_cents"]
            or not 0 < price < 100 or not 0 <= spread <= p["maximum_spread_cents"]
        ):
            continue
        side_probability = max(
            0.0, 1.0 - adjusted_by_ticker[ticker] - p["probability_haircut"]
        )
        fee = _fee(day, price)
        outlay = Decimal(price) / Decimal(100) + fee
        profit = _probability_decimal(side_probability) - outlay
        if profit / outlay < Decimal(str(p["minimum_expected_net_return"])):
            continue
        changed = {**item, "entry_price_cents": price}
        record = _selection_record(day, contract, changed, side_probability)
        candidates.append(record)
    if not candidates:
        return _abstention("NO_ELIGIBLE_CONTRACT", unavailable)
    field = "expected_net_return" if p["selection_mode"] == "expected_return" else "expected_profit_dollars"
    return max(
        candidates,
        key=lambda row: (
            Decimal(row[field]), -row["entry_price_cents"], row["market_ticker"], row["contract_side"]
        ),
    )


def _select_friend_yes(
    day: str, central: dict[str, float], conservative: dict[str, float],
    contracts: list[dict], evidence: dict[tuple[str, str], dict], config: dict,
) -> dict:
    strategy = config["strategy"]
    candidates, unavailable = [], []
    for contract in contracts:
        ticker = contract["market_ticker"]
        item = evidence.get((ticker, "YES"))
        if item is None:
            unavailable.append(ticker + ":YES")
            continue
        if item["execution_evidence_grade"] not in set(strategy["allowed_execution_grades"]):
            continue
        price = item["entry_price_cents"]
        spread = price - item["bid_price_cents"]
        if not 0 < price < 100 or not 0 <= spread <= int(strategy["maximum_spread_cents"]):
            continue
        fee = _fee(day, price)
        outlay = Decimal(price) / Decimal(100) + fee
        conservative_profit = _probability_decimal(conservative[ticker]) - outlay
        if conservative_profit < Decimal(str(strategy["minimum_conservative_edge_dollars"])):
            continue
        candidates.append(_selection_record(
            day, contract, item, central[ticker], expected_probability=conservative[ticker]
        ))
    if not candidates:
        return _abstention("NO_CONSERVATIVE_LONG_YES_EDGE", unavailable)
    # The original primary is multi-bucket.  This replay's preregistered common
    # cap is one contract/day, so selection is frozen to its documented ranking.
    return max(
        candidates,
        key=lambda row: (
            Decimal(row["expected_profit_dollars"]), -row["entry_price_cents"], row["market_ticker"]
        ),
    )


def _load_v5b_probabilities(root: Path, contracts_by_date: dict[str, list[dict]]) -> dict[str, dict[str, float]]:
    frame = pd.read_parquet(root / V5B_PROBABILITIES)
    frame = frame[frame.climate_date.astype(str).isin(TARGET_DATES)]
    output = {}
    for day in TARGET_DATES:
        rows = frame[frame.climate_date.astype(str) == day].sort_values("contract_order")
        tickers = [row["market_ticker"] for row in contracts_by_date[day]]
        if len(rows) != 6 or rows.market_ticker.astype(str).tolist() != tickers:
            raise ReplayError("frozen V5B probability/contract alignment differs")
        values = {str(row.market_ticker): float(row.yes_probability) for row in rows.itertuples()}
        if abs(sum(values.values()) - 1.0) > 1e-8:
            raise ReplayError("frozen V5B target probability mass differs")
        output[day] = values
    return output


def _verify_v5b_frozen_selections(
    root: Path, probabilities: dict[str, dict[str, float]], contracts_by_date: dict[str, list[dict]],
    evidence: dict[tuple[str, str], dict], parameters: dict,
) -> dict[str, dict]:
    freeze = _read_json(root / V5B_SELECTION_FREEZE, sealed=True)
    selected = {row["climate_date"]: row for row in freeze["selections"] if row["climate_date"] in TARGET_DATES}
    output = {}
    for day in TARGET_DATES:
        reconstructed = _select_no(day, probabilities[day], contracts_by_date[day], evidence, parameters)
        frozen = selected.get(day)
        if frozen is None:
            if reconstructed["status"] != "ABSTAINED":
                raise ReplayError(f"V5B frozen abstention does not reproduce: {day}")
            output[day] = reconstructed
            continue
        expected = (frozen["market_ticker"], frozen["contract_side"], frozen["entry_price_cents"])
        actual = (
            reconstructed.get("market_ticker"), reconstructed.get("contract_side"),
            reconstructed.get("entry_price_cents"),
        )
        if actual != expected:
            raise ReplayError(f"V5B frozen selection does not reproduce: {day}")
        for field in ("model_probability", "expected_profit_dollars", "expected_net_return"):
            if not math.isclose(float(reconstructed[field]), float(frozen[field]), rel_tol=0, abs_tol=1e-10):
                raise ReplayError(f"V5B frozen selection economics differ: {day}/{field}")
        output[day] = {**reconstructed, "selection_source": "immutable_v5b_selection_freeze"}
    return output


def _prediction_csv_rows(methods: dict[str, dict]) -> list[dict]:
    rows = []
    for method_id in ALL_METHODS:
        method = methods[method_id]
        for record in method["records"]:
            for probability in record["probabilities"]:
                rows.append({
                    "method_id": method_id, "method_class": method["method_class"],
                    "climate_date": record["climate_date"], "event_ticker": record["event_ticker"],
                    **probability,
                })
    return rows


def _selection_csv_rows(methods: dict[str, dict]) -> list[dict]:
    fields = [
        "method_id", "method_class", "climate_date", "status", "reason", "market_ticker",
        "contract_side", "entry_price_cents", "bid_price_cents", "spread_cents",
        "execution_evidence_grade", "quote_at_utc", "quantity", "model_probability",
        "selection_probability", "fee_dollars", "entry_outlay_dollars",
        "expected_profit_dollars", "expected_net_return", "unavailable_market_sides",
    ]
    rows = []
    for method_id in ALL_METHODS:
        method = methods[method_id]
        for record in method["records"]:
            selection = record["selection"]
            rows.append({field: "" for field in fields} | {
                "method_id": method_id, "method_class": method["method_class"],
                "climate_date": record["climate_date"],
                **{key: ("|".join(value) if isinstance(value, list) else value)
                   for key, value in selection.items() if key in fields},
            })
    return rows


def build_prediction_freeze(project_root: str | Path, replay_root: str | Path | None = None) -> dict:
    """Create immutable predictions and selections without opening any outcome file."""
    root = Path(project_root).resolve()
    replay = (Path(replay_root).resolve() if replay_root else root / REPLAY_RELATIVE)
    rows_by_date, contracts = _load_target_universe(root)
    contracts_by_date = {day: _ordered_yes(rows_by_date[day]) for day in TARGET_DATES}
    evidence = _load_market_evidence(replay, rows_by_date)
    frozen_v5b_evidence = _frozen_universe_evidence(rows_by_date)
    training_features, labels = _load_training(root)
    target_v6, v6_bindings = _load_v6_target_features(root)
    friend_target_bindings = _validate_friend_target_archive(replay)
    friend_config, _ = _friend_config(root)

    v5b_strategy = _read_json(root / V5B_STRATEGY_FREEZE, sealed=True)
    if v5b_strategy["candidate_id"] != "c4d9b5adac5b672b56850f7f840bce1b266ad06a8925849dde7af5a9a1ec5b6d":
        raise ReplayError("V5B frozen control candidate differs")
    v5b_probabilities = _load_v5b_probabilities(root, contracts_by_date)
    v5b_selections = _verify_v5b_frozen_selections(
        root, v5b_probabilities, contracts_by_date, frozen_v5b_evidence,
        v5b_strategy["parameters"]
    )

    v6_forecasts = _v6_frozen_forecasts(
        training_features, labels, target_v6, contracts_by_date
    )
    friend_forecasts, diagnostic_forecasts = _friend_forecasts(
        root, replay, [row["climate_date"] for row in training_features], labels,
        contracts_by_date,
    )
    v6_policy = {**V5B_DEFAULTS, "selection_mode": "expected_profit", "allowed_sides": ["NO"]}
    v5f_config = _read_json(root / V5F_CONFIG)
    if v5f_config["parameters"] != {
        "v5b_weight": 0.5, "friend_heavy_tail_weight": 0.5,
        "wide_component_weight": 0.2, "wide_sigma_multiplier": 2.0,
    }:
        raise ReplayError("V5F frozen weights differ")

    method_meta = {
        "v5b_frozen_no": ("named_strategy", "immutable V5B frozen NO control"),
        "v6_gefs_spread_equal_blend_no": ("named_strategy", "V6 diagnostic leader, economic-stage NO policy"),
        "friend_exact_primary_gfs_nam_nbm": ("named_strategy", "friend primary fixed-PST window with one-contract replay cap"),
        "v5f_cross_family_stack": ("named_strategy", "fixed 50/50 V5B plus friend-heavy-tail comparator"),
        "diagnostic_gfs_only": ("exploratory_diagnostic", "GFS-only friend-rule diagnostic"),
        "diagnostic_nam_only": ("exploratory_diagnostic", "NAM-only friend-rule diagnostic"),
        "diagnostic_nbm_only": ("exploratory_diagnostic", "NBM-only friend-rule diagnostic"),
    }
    methods = {
        method: {"method_class": meta[0], "description": meta[1], "records": []}
        for method, meta in method_meta.items()
    }
    for day in TARGET_DATES:
        event = rows_by_date[day][0]["event_ticker"]
        base = v5b_probabilities[day]
        v6 = v6_forecasts[day]
        friend = friend_forecasts[day]
        wide = gaussian_bracket_probabilities(
            friend["location_f"], friend["sigma_f"] * 2.0, contracts_by_date[day]
        )
        heavy = {
            ticker: 0.8 * friend["central_probabilities"][ticker] + 0.2 * wide[ticker]
            for ticker in friend["central_probabilities"]
        }
        v5f = {ticker: 0.5 * base[ticker] + 0.5 * heavy[ticker] for ticker in base}
        definitions = {
            "v5b_frozen_no": (base, v5b_selections[day], {
                "candidate_id": v5b_strategy["candidate_id"], "target_updates_used": False,
            }),
            "v6_gefs_spread_equal_blend_no": (
                v6["central_probabilities"],
                _select_no(day, v6["central_probabilities"], contracts_by_date[day], evidence, v6_policy),
                {key: value for key, value in v6.items() if key != "central_probabilities"},
            ),
            "friend_exact_primary_gfs_nam_nbm": (
                friend["central_probabilities"],
                _select_friend_yes(day, friend["central_probabilities"], friend["conservative_probabilities"],
                                   contracts_by_date[day], evidence, friend_config),
                {key: value for key, value in friend.items()
                 if key not in {"central_probabilities", "conservative_probabilities"}}
                | {"conservative_probabilities": friend["conservative_probabilities"]},
            ),
            "v5f_cross_family_stack": (
                v5f, _select_no(day, v5f, contracts_by_date[day], evidence, v5b_strategy["parameters"]),
                {"v5b_weight": 0.5, "friend_heavy_tail_weight": 0.5,
                 "wide_component_weight": 0.2, "wide_sigma_multiplier": 2.0,
                 "friend_heavy_tail_probabilities": heavy, "target_updates_used": False},
            ),
        }
        for source in ("gfs", "nam", "nbm"):
            forecast = diagnostic_forecasts[source][day]
            definitions[f"diagnostic_{source}_only"] = (
                forecast["central_probabilities"],
                _select_friend_yes(day, forecast["central_probabilities"], forecast["conservative_probabilities"],
                                   contracts_by_date[day], evidence, friend_config),
                {key: value for key, value in forecast.items()
                 if key not in {"central_probabilities", "conservative_probabilities"}}
                | {"conservative_probabilities": forecast["conservative_probabilities"]},
            )
        for method_id, (probabilities, selection, forecast_metadata) in definitions.items():
            methods[method_id]["records"].append({
                "climate_date": day, "event_ticker": event,
                "settlement_source": "The Weather Company", "decision_at": day + "T18:00:00+00:00",
                "probabilities": _probability_rows(probabilities, contracts_by_date[day]),
                "forecast": forecast_metadata, "selection": selection,
            })

    for method_id, method in methods.items():
        records = method["records"]
        if [row["climate_date"] for row in records] != list(TARGET_DATES):
            raise ReplayError("method does not cover all seven dates")
        if any(row["selection"].get("quantity", 0) not in {0, 1} for row in records):
            raise ReplayError("one-contract-per-day invariant failed")

    prediction_rows = _prediction_csv_rows(methods)
    selection_rows = _selection_csv_rows(methods)
    prediction_fields = [
        "method_id", "method_class", "climate_date", "event_ticker", "contract_order",
        "market_ticker", "strike_type", "floor_strike", "cap_strike",
        "yes_probability", "no_probability",
    ]
    selection_fields = [
        "method_id", "method_class", "climate_date", "status", "reason", "market_ticker",
        "contract_side", "entry_price_cents", "bid_price_cents", "spread_cents",
        "execution_evidence_grade", "quote_at_utc", "quantity", "model_probability",
        "selection_probability", "fee_dollars", "entry_outlay_dollars",
        "expected_profit_dollars", "expected_net_return", "unavailable_market_sides",
    ]
    _write_immutable_csv(replay / PREDICTION_CSV, prediction_rows, prediction_fields)
    _write_immutable_csv(replay / SELECTION_CSV, selection_rows, selection_fields)

    root_inputs = [
        UNIVERSE, V5B_PROBABILITIES, V5B_SELECTION_FREEZE, V5B_STRATEGY_FREEZE,
        TRAINING_FEATURES, TRAINING_LABELS, FRIEND_CONFIG, V5F_CONFIG, V6_CANDIDATE,
    ]
    document = _seal({
        "schema_version": "klax-past7-prediction-selection-freeze-v1",
        "status": "FROZEN_BEFORE_REPLAY_OUTCOME_READ",
        "target_dates": list(TARGET_DATES), "decision_time_utc": "18:00",
        "training_state": {
            "date_start": TRAINING_START, "date_end": TRAINING_END, "date_count": 64,
            "target_label_updates_used": False,
            "policy": "original exposed Jun1-Aug3 state frozen once for all seven targets",
        },
        "candidate_mapping": {
            "v5b_frozen_no": v5b_strategy["candidate_id"],
            "v6_gefs_spread_equal_blend_no": "6c7dcb09ece3c69cee32e0bcba5de4c5ed34632bcc7274e0e1e7d76be199d896",
            "friend_exact_primary_gfs_nam_nbm": "kalshi_fixed_pst_f008_f031/one_contract_replay_cap",
            "v5f_cross_family_stack": "V5F/fixed_stacked_probability",
        },
        "methods": methods,
        "outcomes_read": False, "outcome_path_opened": False,
        "protected_holdouts_read": False, "network_used": False, "orders": 0,
        "root_input_bindings": {path.as_posix(): _file_hash(root / path) for path in root_inputs} | v6_bindings,
        "replay_input_bindings": {
            ORDERBOOK_RELATIVE.as_posix(): _file_hash(replay / ORDERBOOK_RELATIVE),
            **friend_target_bindings,
        },
        "output_bindings": {
            PREDICTION_CSV.as_posix(): _file_hash(replay / PREDICTION_CSV),
            SELECTION_CSV.as_posix(): _file_hash(replay / SELECTION_CSV),
        },
        "limitations": [
            "All seven dates were selection-exposed before this replay; this is not independent confirmation.",
            "The Weather Company numeric settlement temperature is unavailable, so MAE and CRPS are disabled.",
            "Friend primary is capped to one contract per day for the common replay protocol.",
            "GFS-, NAM-, and NBM-only rows are exploratory diagnostics and are not registered strategy families.",
        ],
    })
    _write_immutable_json(replay / PREDICTION_JSON, document)
    return document


def _load_outcomes(path: Path, expected_tickers: set[str]) -> dict[str, dict[str, str]]:
    expected_fields = {"market_ticker", "title", "closes_at", "resolved_at", "winning_side"}
    by_date = {day: {} for day in TARGET_DATES}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if set(reader.fieldnames or []) != expected_fields:
            raise ReplayError("resolved outcome TSV schema differs")
        for row in reader:
            ticker = row["market_ticker"]
            if ticker not in expected_tickers:
                raise ReplayError("resolved outcome ticker is outside replay universe")
            day = next(day for day in TARGET_DATES if f"26SEP{day[-2:]}" in ticker)
            if ticker in by_date[day] or row["winning_side"] not in {"yes", "no"}:
                raise ReplayError("duplicate or invalid resolved outcome")
            if _timestamp(row["resolved_at"]) <= _timestamp(row["closes_at"]):
                raise ReplayError("outcome resolved before close")
            by_date[day][ticker] = row["winning_side"].upper()
    for day, rows in by_date.items():
        if len(rows) != 6 or sum(side == "YES" for side in rows.values()) != 1:
            raise ReplayError(f"resolved outcomes are incomplete: {day}")
    return by_date


def score_prediction_freeze(project_root: str | Path, replay_root: str | Path | None = None) -> dict:
    """Score a pre-existing freeze; this is the only function that opens outcomes."""
    root = Path(project_root).resolve()
    replay = (Path(replay_root).resolve() if replay_root else root / REPLAY_RELATIVE)
    freeze_path = replay / PREDICTION_JSON
    freeze = _read_json(freeze_path, sealed=True)
    if freeze.get("status") != "FROZEN_BEFORE_REPLAY_OUTCOME_READ" or freeze.get("outcomes_read") is not False:
        raise ReplayError("valid pre-outcome prediction freeze required")
    for relative, expected in freeze["root_input_bindings"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or _file_hash(path) != expected:
            raise ReplayError(f"frozen root input binding differs: {relative}")
    for relative, expected in freeze["replay_input_bindings"].items():
        path = (replay / relative).resolve()
        if not path.is_relative_to(replay) or not path.is_file() or _file_hash(path) != expected:
            raise ReplayError(f"frozen replay input binding differs: {relative}")
    for relative, expected in freeze["output_bindings"].items():
        if _file_hash(replay / relative) != expected:
            raise ReplayError("frozen prediction CSV binding differs")
    expected_tickers = {
        probability["market_ticker"]
        for record in freeze["methods"][NAMED_METHODS[0]]["records"]
        for probability in record["probabilities"]
    }
    outcome_path = replay / OUTCOMES_RELATIVE
    outcomes = _load_outcomes(outcome_path, expected_tickers)
    rows, summaries = [], {}
    for method_id in ALL_METHODS:
        method = freeze["methods"][method_id]
        briers, logs, selected_rows = [], [], []
        for record in method["records"]:
            day = record["climate_date"]
            winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
            probs = {row["market_ticker"]: row["yes_probability"] for row in record["probabilities"]}
            brier = sum((value - float(ticker == winner)) ** 2 for ticker, value in probs.items())
            log_loss = -math.log(max(probs[winner], 1e-12))
            briers.append(brier); logs.append(log_loss)
            selection = record["selection"]
            scored = {
                "method_id": method_id, "method_class": method["method_class"],
                "climate_date": day, "winning_market_ticker": winner,
                "multiclass_brier": brier, "clipped_log_loss": log_loss,
                "selection_status": selection["status"], "abstention_reason": selection.get("reason"),
                "selected_market_ticker": selection.get("market_ticker"),
                "contract_side": selection.get("contract_side"), "won": None,
                "entry_outlay_dollars": None, "net_profit_dollars": None,
            }
            if selection["status"] == "SELECTED":
                selected_ticker = selection["market_ticker"]
                won = outcomes[day][selected_ticker] == selection["contract_side"]
                outlay = Decimal(selection["entry_outlay_dollars"])
                profit = Decimal(int(won)) - outlay
                scored.update({
                    "won": won, "entry_outlay_dollars": _decimal_text(outlay),
                    "net_profit_dollars": _decimal_text(profit),
                })
                selected_rows.append(scored)
            rows.append(scored)
        total_outlay = sum((Decimal(row["entry_outlay_dollars"]) for row in selected_rows), Decimal(0))
        total_profit = sum((Decimal(row["net_profit_dollars"]) for row in selected_rows), Decimal(0))
        summaries[method_id] = {
            "method_class": method["method_class"], "forecast_date_count": 7,
            "selected_date_count": len(selected_rows),
            "mean_multiclass_brier": mean(briers), "mean_clipped_log_loss": mean(logs),
            "total_entry_outlay_dollars": _decimal_text(total_outlay),
            "total_net_profit_dollars": _decimal_text(total_profit),
            "aggregate_realized_net_return": (
                _decimal_text(total_profit / total_outlay) if total_outlay else None
            ),
        }
    score_fields = [
        "method_id", "method_class", "climate_date", "winning_market_ticker",
        "multiclass_brier", "clipped_log_loss", "selection_status", "abstention_reason",
        "selected_market_ticker", "contract_side", "won", "entry_outlay_dollars",
        "net_profit_dollars",
    ]
    summary_fields = [
        "method_id", "method_class", "forecast_date_count", "selected_date_count",
        "mean_multiclass_brier", "mean_clipped_log_loss", "total_entry_outlay_dollars",
        "total_net_profit_dollars", "aggregate_realized_net_return",
    ]
    _write_immutable_csv(replay / SCORED_CSV, rows, score_fields)
    _write_immutable_csv(
        replay / SUMMARY_CSV,
        [{"method_id": method, **summaries[method]} for method in ALL_METHODS],
        summary_fields,
    )
    document = _seal({
        "schema_version": "klax-past7-scored-replay-v1",
        "status": "RETROSPECTIVE_REPLAY_SCORED",
        "prediction_freeze_sha256": _file_hash(freeze_path),
        "prediction_freeze_self_sha256": freeze["self_sha256"],
        "outcome_binding": {OUTCOMES_RELATIVE.as_posix(): _file_hash(outcome_path)},
        "target_dates": list(TARGET_DATES), "methods": summaries,
        "rows": rows, "outcomes_read_after_prediction_freeze": True,
        "protected_holdouts_read": False, "network_used": False, "orders": 0,
        "numeric_settlement_temperature_available": False,
        "mae_and_crps_computed": False,
        "independent_confirmation": False,
        "output_bindings": {
            SCORED_CSV.as_posix(): _file_hash(replay / SCORED_CSV),
            SUMMARY_CSV.as_posix(): _file_hash(replay / SUMMARY_CSV),
        },
    })
    _write_immutable_json(replay / SCORED_JSON, document)
    return document
