"""Deterministic development-only baseline replay on frozen local inputs.

Final holdout reads are intentionally absent. This is an assumption-dependent
historical scenario, never an order router or evidence of actual account gains.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
import json
from math import fsum
from pathlib import Path
from statistics import fmean

import numpy as np
import pyarrow.parquet as pq

from .domain import ContractBounds
from .evaluation import (ExecutionEvidence, FeeScenario, ReplayPolicy, brier_score,
                         evaluate_purchase, gaussian_crps, mean_absolute_error,
                         mean_bias, settle_purchase, summarize_settlements)
from .models import BASELINE_DEFINITIONS, fit_baselines, predict
from .provenance import canonical_hash, freeze_manifest, inventory, verify_manifest, write_json


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def calibration_summary(probabilities: list[float], outcomes: list[int]) -> list[dict]:
    bins = []
    for index in range(10):
        pairs = [(p, y) for p, y in zip(probabilities, outcomes) if min(9, int(p * 10)) == index]
        if pairs:
            bins.append({"lower": index / 10, "upper": (index + 1) / 10,
                         "count": len(pairs), "mean_probability": fmean(p for p, y in pairs),
                         "observed_frequency": fmean(y for p, y in pairs)})
    return bins


def probability_scores(predictions: dict, contracts: list[dict], outcomes: list[dict], labels: dict, eligible: set[str]) -> dict:
    actual = {r["ticker"]: r["yes_outcome"] for r in outcomes}
    pairs = [(predictions[d], labels[d]["tmax_f"]) for d in sorted(eligible) if d in predictions and d in labels]
    probabilities, results = [], []
    for contract in contracts:
        day = contract["climate_date"]
        if day not in eligible or day not in predictions or contract["ticker"] not in actual:
            continue
        mean, sd = predictions[day]
        probabilities.append(ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).probability(mean, sd))
        results.append(actual[contract["ticker"]])
    return {"weather_days": len(pairs), "contract_count": len(results),
            "mae_f": mean_absolute_error([m for (m, s), y in pairs], [y for ms, y in pairs]) if pairs else None,
            "bias_f": mean_bias([m for (m, s), y in pairs], [y for ms, y in pairs]) if pairs else None,
            "gaussian_crps_f": fmean(gaussian_crps(m, s, y) for (m, s), y in pairs) if pairs else None,
            "brier": brier_score(probabilities, results) if results else None,
            "calibration_bins": calibration_summary(probabilities, results),
            "scoring_label": "NWS daily high for weather scores; actual Kalshi outcome for Brier; reconciled days only"}


def forecast_artifacts(predictions: dict, features: list[dict], contracts: list[dict], outcomes: list[dict],
                       labels: dict, eligible: set[str], model_version: str, dataset_version: str) -> dict:
    """Persistable baseline forecasts and the complete eligible scoring universe."""
    lineage = {"model_sha256": model_version, "dataset_version": dataset_version}
    daily_predictions = [{"climate_date": row["climate_date"], "mean_f": predictions[row["climate_date"]][0],
        "sd_f": predictions[row["climate_date"]][1], "feature_available_at": row["available_at"],
        "eligible": row["climate_date"] in eligible, **lineage} for row in sorted(features, key=lambda row: row["climate_date"])]
    actual = {row["ticker"]: row["yes_outcome"] for row in outcomes}
    probability_rows, errors = [], defaultdict(list)
    for contract in sorted(contracts, key=lambda row: row["ticker"]):
        day, ticker = contract["climate_date"], contract["ticker"]
        if day not in eligible or day not in predictions or ticker not in actual: continue
        probability = ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).probability(*predictions[day])
        error = (probability - actual[ticker]) ** 2
        errors[day].append(error)
        probability_rows.append({"climate_date": day, "ticker": ticker, "yes_probability": probability,
                                 "yes_outcome": actual[ticker], "brier_contribution": error, **lineage})
    daywise = []
    for day in sorted(eligible):
        mean, sd = predictions[day]
        difference = mean - labels[day]["tmax_f"]
        daywise.append({"climate_date": day, "crps_f": gaussian_crps(mean, sd, labels[day]["tmax_f"]),
                        "brier": fmean(errors[day]) if errors[day] else None, "brier_sum": fsum(errors[day]),
                        "contract_count": len(errors[day]), "mean_error_f": difference,
                        "absolute_error_f": abs(difference), **lineage})
    return {"predictions": daily_predictions, "contract_probabilities": probability_rows, "daywise_scores": daywise}


def day_bootstrap(ledger: list[dict], weather_days: list[str], samples: int, seed: int) -> dict:
    """Resample whole weather days, including no-trade days; return ratio of sums."""
    by_day = {r["climate_date"]: r for r in ledger}
    if len(by_day) != len(ledger):
        raise ValueError("More than one entry per weather day")
    if not weather_days or not ledger:
        return {"lower_95": None, "upper_95": None, "valid_resamples": 0}
    profit = np.array([float(by_day[d]["net_profit"]) if d in by_day else 0 for d in weather_days])
    outlay = np.array([float(by_day[d]["entry_outlay"]) if d in by_day else 0 for d in weather_days])
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(weather_days), size=(samples, len(weather_days)))
    denominators = outlay[indices].sum(axis=1)
    valid = denominators > 0
    ratios = profit[indices].sum(axis=1)[valid] / denominators[valid]
    lo, hi = np.quantile(ratios, [0.025, 0.975]) if len(ratios) else (None, None)
    return {"lower_95": lo, "upper_95": hi, "valid_resamples": len(ratios),
            "limitation": "Day bootstrap does not capture multi-day weather dependence or strategy-selection bias"}


def replay_model(*, predictions: dict, contracts: list[dict], candles: list[dict], outcomes: list[dict], eligible: set[str],
                 policy: dict, model_version: str, dataset_version: str, slippage: str | None = None,
                 fee_rate: str | None = None, quantity: int | None = None) -> dict:
    quantity = policy["reference_quantity"] if quantity is None else quantity
    slip = Decimal(policy["slippage_per_contract"] if slippage is None else slippage)
    if not slip.is_finite() or slip < 0:
        raise ValueError("Slippage must be finite and nonnegative")
    fee_config = policy["fee_scenario"]
    fees = FeeScenario(fee_config["name"], fee_config["rate"] if fee_rate is None else fee_rate,
                       fee_config["source"], fee_config["rounding"], fee_config["historical_verified"])
    target_return = policy.get("research_target_expected_net_return", policy["target_expected_net_return"])
    allowed_sides = policy.get("research_allowed_sides", ["YES", "NO"])
    if (not isinstance(allowed_sides, list) or not allowed_sides or len(set(allowed_sides)) != len(allowed_sides)
            or any(side not in ("YES", "NO") for side in allowed_sides)):
        raise ValueError("Research side policy must contain unique YES/NO values")
    rules = ReplayPolicy(target_return=target_return, max_quote_age_seconds=policy["max_quote_age_seconds"],
                          execution_delay_seconds=policy["execution_delay_seconds"], max_quantity=quantity)
    by_day, quotes = defaultdict(list), defaultdict(list)
    actual = {r["ticker"]: r for r in outcomes}
    for row in contracts:
        by_day[row["climate_date"]].append(row)
    for row in candles:
        quotes[row["ticker"]].append(row)
    for rows in quotes.values():
        rows.sort(key=lambda r: r["end_period_ts"])
    ledger, decisions, settlements, skipped = [], [], [], Counter()
    opportunity_days = sorted(set(predictions) & eligible)
    for day in opportunity_days:
        cutoff = utc(day + f"T{policy['decision_hour_utc']:02d}:00:00+00:00")
        entry = cutoff + timedelta(seconds=policy["execution_delay_seconds"])
        candidates = []
        for contract in sorted(by_day[day], key=lambda r: r["ticker"]):
            ticker = contract["ticker"]
            outcome = actual.get(ticker)
            if not outcome or outcome.get("mapping_consistent") is not True:
                skipped["UNVERIFIED_SETTLEMENT_MAPPING"] += 1
                continue
            if not utc(contract["open_time"]) <= cutoff <= entry < utc(contract["close_time"]):
                skipped["MARKET_NOT_OPEN_AT_ENTRY"] += 1
                continue
            available = [r for r in quotes[ticker] if r["end_period_ts"] <= cutoff.timestamp()]
            if not available:
                skipped["NO_COMPLETED_CANDLE"] += 1
                continue
            quote = available[-1]
            observed = datetime.fromtimestamp(quote["end_period_ts"], timezone.utc)
            evidence = ExecutionEvidence("B", quote["source_sha256"], observed, observed + timedelta(seconds=60),
                                         policy["price_convention"] + "; assumed summary availability60s after hour")
            mean, sd = predictions[day]
            probability = ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).probability(mean, sd)
            for side, field in (("YES", "yes_ask_close"), ("NO", "yes_bid_close")):
                if side not in allowed_sides:
                    skipped["SIDE_POLICY"] += 1
                    continue
                value = quote[field]
                if value is None:
                    skipped["MISSING_QUOTE_SIDE"] += 1
                    continue
                base_price = Decimal(value) if side == "YES" else Decimal(1) - Decimal(value)
                price = base_price + slip
                if not Decimal(0) < base_price < Decimal(1) or not Decimal(0) < price < Decimal(1):
                    skipped["NONTRADABLE_PRICE"] += 1
                    continue
                decision = evaluate_purchase(decision_id=f"{model_version}:{day}:{ticker}:{side}", information_cutoff=cutoff,
                                             decision_at=entry, yes_probability=probability, side=side, quantity=quantity,
                                             entry_price=price, fees=fees, evidence=evidence, dataset_version=dataset_version,
                                             model_version=model_version, policy=rules)
                decisions.append({**asdict(decision), "ticker": ticker, "climate_date": day, "selected": False})
                if decision.status == "ACCEPTED":
                    candidates.append((decision, ticker, len(decisions) - 1))
                else:
                    skipped[decision.reason] += 1
        if not candidates:
            continue
        # Choice uses only forecast probabilities and pre-entry prices.
        decision, ticker, row_index = min(candidates, key=lambda item: (-item[0].expected_net_return, item[1], item[0].side))
        decisions[row_index]["selected"] = True
        skipped["DAILY_ENTRY_CAP"] += len(candidates) - 1
        settlement = settle_purchase(decision, actual[ticker]["yes_outcome"])
        settlements.append(settlement)
        ledger.append({**asdict(settlement), "climate_date": day, "ticker": ticker,
                       "entry_price": decision.entry_price, "entry_fee": decision.entry_fee,
                       "expected_net_return": decision.expected_net_return,
                       "settled_at": actual[ticker]["settlement_time"]})
    summary = summarize_settlements(settlements)
    running = peak = drawdown = Decimal(0)
    # Settlement chronology, not contract target-date chronology, for cash PnL.
    for row in sorted(ledger, key=lambda r: (utc(r["settled_at"]), r["decision_id"])):
        running += row["net_profit"]
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    summary.update(opportunity_days=len(opportunity_days), max_settled_net_pnl_drawdown_dollars=drawdown,
                   drawdown_scope="Cumulative settled net PnL only; no mark-to-market, entry cashflow, bankroll or capital-constraint simulation",
                   bootstrap=day_bootstrap(ledger, opportunity_days, policy["bootstrap_samples"], policy["bootstrap_seed"]),
                   inference_sample_sufficient=len(ledger) >= policy["minimum_selected_weather_days_for_inference"],
                   skipped_reasons=dict(skipped), historical_fee_verified=False, executable_depth_verified=False,
                   selection_return_is_not_out_of_sample_final_evidence=True)
    return {"summary": summary, "ledger": ledger, "decisions": decisions}


def development_paths(root: Path) -> list[Path]:
    base = root / "data/normalized"
    paths = []
    for split in ("weather_training", "selection"):
        for kind, names in (("features", ("forecasts", "contracts", "candles")), ("labels", ("climate", "climate_versions", "outcomes", "reconciliation"))):
            for name in names:
                path = base / split / kind / (name + ".parquet")
                paths.append(path)
    return paths


def development_inputs(root: Path, policy: dict) -> dict:
    tables = {}
    for path in development_paths(root):
        split = path.parent.parent.name
        tables[(split, path.stem)] = pq.read_table(path).to_pylist()
    from .readiness import require_development_coverage
    require_development_coverage(root, policy, tables)
    return tables


def run_development_baselines(root: Path, policy: dict) -> dict:
    from .policy import validate_evaluation_policy
    validate_evaluation_policy(policy)
    paths = development_paths(root)
    from .readiness import terminal_weather_run
    terminal = terminal_weather_run(root)
    if terminal is None:
        raise ValueError("Historical weather acquisition is incomplete; no baseline run permitted")
    paths += [root / terminal["source_manifest"], root / "data/manifests/forecast_normalization.json", root / "data/manifests/market_quality.json"]
    paths += [root / "configs/evaluation.json", *sorted((root / "src/klax_lab").glob("*.py"))]
    # Immutable data/code snapshot; creates a new manifest only if a new version.
    manifest_policy = {"evaluation": policy, "baseline_definitions": BASELINE_DEFINITIONS}
    fingerprint = canonical_hash({"files": inventory(root, paths), "policy": manifest_policy})
    manifest = freeze_manifest(root, paths, manifest_policy,
                               root / "data/manifests" / f"development-baselines-{fingerprint}.json")
    verify_manifest(root, manifest)
    tables = development_inputs(root, policy)
    verify_manifest(root, manifest)
    train_labels = {r["climate_date"]: r for r in tables[("weather_training", "climate")]}
    training = []
    fit_cutoff = utc(policy["model_fit_cutoff_utc"])
    for row in tables[("weather_training", "forecasts")]:
        label = train_labels.get(row["climate_date"])
        if label and utc(label["available_at"]) <= fit_cutoff:
            training.append({**row, "tmax_f": label["tmax_f"]})
    models = fit_baselines(training, policy["minimum_training_days"])
    features = tables[("selection", "forecasts")]
    for row in features:
        if utc(row["available_at"]) > utc(row["climate_date"] + f"T{policy['decision_hour_utc']:02d}:00:00Z"):
            raise ValueError("Selection forecast is unavailable at its information cutoff")
    contracts, outcomes = tables[("selection", "contracts")], tables[("selection", "outcomes")]
    reconciled = tables[("selection", "reconciliation")]
    labels = {r["climate_date"]: r for r in tables[("selection", "climate")]}
    eligible = set(labels) & {r["climate_date"] for r in features}
    excluded_reasons = defaultdict(set)
    for row in outcomes:
        if row.get("mapping_consistent") is not True:
            excluded_reasons[row["climate_date"]].add("unverified_settlement_mapping")
    for row in reconciled:
        if row.get("settlement_label_reconciled") is not True:
            excluded_reasons[row["climate_date"]].add("NWS_label_not_reconciled_with_settlement")
    bad_days = {r["climate_date"] for r in outcomes if r.get("mapping_consistent") is not True}
    bad_days |= {r["climate_date"] for r in reconciled if r.get("settlement_label_reconciled") is not True}
    # Require all expected contracts to have reconciled payout labels.
    good_tickers = {r["ticker"] for r in reconciled if r.get("settlement_label_reconciled") is True}
    bad_days |= {r["climate_date"] for r in contracts if r["ticker"] not in good_tickers}
    for row in contracts:
        if row["ticker"] not in good_tickers:
            excluded_reasons[row["climate_date"]].add("missing_reconciled_contract")
        if row["climate_date"] not in labels:
            excluded_reasons[row["climate_date"]].add("missing_NWS_label")
        if row["climate_date"] not in {r["climate_date"] for r in features}:
            excluded_reasons[row["climate_date"]].add("missing_or_quarantined_forecast")
    eligible -= bad_days
    output = root / "runs" / f"baselines-{manifest['version'][:16]}"
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "fitted_models.json", models)
    write_json(output / "selection_eligibility.json", {"eligible_days": sorted(eligible),
               "excluded": [{"climate_date": day, "reasons": sorted(reasons)} for day, reasons in sorted(excluded_reasons.items())],
               "limitation": "Retrospective settlement-reconciliation filter; results conditional on this auditable subset, not all listed markets"})
    reports = {}
    for name, model in models.items():
        predictions = {r["climate_date"]: predict(model, r) for r in features}
        model_hash = canonical_hash(model)
        saved_forecasts = forecast_artifacts(predictions, features, contracts, outcomes, labels, eligible, model_hash, manifest["version"])
        forecast_paths = []
        for kind, rows in saved_forecasts.items():
            artifact_path = output / f"{name}_{kind}.json"
            write_json(artifact_path, rows)
            forecast_paths.append(artifact_path)
        replay = replay_model(predictions=predictions, contracts=contracts, candles=tables[("selection", "candles")],
                              outcomes=outcomes, eligible=eligible, policy=policy,
                              model_version=model_hash, dataset_version=manifest["version"])
        write_json(output / f"{name}_ledger.json", replay["ledger"])
        write_json(output / f"{name}_decisions.json", replay["decisions"])
        reports[name] = {"forecast_scores": probability_scores(predictions, contracts, outcomes, labels, eligible),
                         "historical_assumed_fill": replay["summary"], "model_sha256": model_hash,
                         "forecast_artifacts": inventory(output, forecast_paths)}
        sensitivity = []
        for slip, rate, quantity in product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"]):
            assumptions = {"slippage": slip, "fee_rate": rate, "quantity": quantity}
            scenario_id = canonical_hash(assumptions)[:12]
            scenario = replay_model(predictions=predictions, contracts=contracts, candles=tables[("selection", "candles")],
                                    outcomes=outcomes, eligible=eligible, policy=policy, slippage=slip, fee_rate=rate, quantity=quantity,
                                    model_version=model_hash + ":" + scenario_id, dataset_version=manifest["version"])
            write_json(output / "sensitivity" / f"{name}_{scenario_id}_ledger.json", scenario["ledger"])
            sensitivity.append({"scenario_id": scenario_id, **assumptions, "summary": scenario["summary"]})
        reports[name]["predeclared_cost_sensitivity"] = sensitivity
    report = {"status": "DEVELOPMENT_BASELINES_COMPLETE", "dataset_version": manifest["version"],
              "training_days": len(training), "eligible_selection_days": len(eligible),
              "excluded_selection_days": (utc(policy["selection"][1] + "T00:00:00Z") - utc(policy["selection"][0] + "T00:00:00Z")).days + 1 - len(eligible),
              "eligibility_artifact": inventory(output, [output / "selection_eligibility.json"])[0],
              "saved_forecast_artifact_version": 1,
              "models": reports, "protected_final_evaluated": False, "goal1_complete": False, "goal2_complete": False,
              "limitations": ["All economic results are hourly-summary assumed-fill simulations", "2025 fees and publication availability are unverified assumptions", "These development results support further research, not performance claims", "Holdout isolation and actual multiagent campaign remain gated"]}
    verify_manifest(root, manifest)
    write_json(output / "summary.json", report)
    return {"path": str(output), **report}
