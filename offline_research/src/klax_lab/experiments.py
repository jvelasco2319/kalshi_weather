"""Closed-recipe experiments on an existing frozen DEVELOPMENT dataset only.

No model-supplied paths, code, expressions, imports, acquisition or holdout reads
are supported. Production calls require a completed baseline run. The separate
verifier independently checks fit, predictions and primary opportunity selection.
Sensitivity selection completeness and uncertainty estimates remain unaudited.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from itertools import product
import json
from math import cos, erf, exp, fsum, isclose, isfinite, pi, sin, sqrt
from pathlib import Path
import platform
import re
from statistics import fmean
from uuid import uuid4

import numpy as np
import pyarrow
import pyarrow.parquet as pq

from .baseline import development_paths, probability_scores, replay_model, utc
from .candidates import CandidateSpec, fit_candidate, predict_candidate
from .domain import ContractBounds
from .evaluation import gaussian_crps
from .models import BASELINE_DEFINITIONS
from .policy import validate_evaluation_policy
from .provenance import canonical_hash, sha256_file, verify_manifest, write_json
from .research_plan import PLAN_VERSION, ResearchPlan, compile_plan

CODE_FILES = ("experiments.py", "candidates.py", "baseline.py", "models.py", "domain.py",
              "evaluation.py", "policy.py", "provenance.py", "cli.py", "offline.py",
              "research_plan.py")


def _read_json(path: Path):
    def invalid(_value):
        raise ValueError("Non-finite JSON number")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)


def _spec(value: CandidateSpec | dict) -> CandidateSpec:
    fields = asdict(value) if isinstance(value, CandidateSpec) else value
    checked = CandidateSpec.from_dict(fields)
    fields = asdict(checked)
    for key in ("gfs_weight", "spread_scale", "disagreement_coefficient"):
        fields[key] = float(fields[key])
    return CandidateSpec.from_dict(fields)


def _resolve_plan(value: CandidateSpec | dict) -> tuple[CandidateSpec, ResearchPlan | None, dict]:
    if isinstance(value, dict) and value.get("plan_version") == PLAN_VERSION:
        compiled = compile_plan(value)
        return compiled.candidate, compiled.plan, compiled.policy_overrides
    return _spec(value), None, {}


def _guard_manifest(root: Path, manifest_path: Path) -> tuple[dict, dict, dict]:
    manifest_path = manifest_path.resolve()
    if manifest_path.parent != (root / "data/manifests").resolve() or not re.fullmatch(r"development-baselines-[0-9a-f]{64}\.json", manifest_path.name):
        raise ValueError("An existing frozen development-baselines manifest is required")
    manifest = _read_json(manifest_path)
    if manifest_path.name != f"development-baselines-{manifest['version']}.json":
        raise ValueError("Development manifest filename/version mismatch")
    if manifest["policy"].get("baseline_definitions") != BASELINE_DEFINITIONS:
        raise ValueError("Manifest is not the registered development baseline snapshot")
    required = {p.relative_to(root).as_posix() for p in development_paths(root)}
    indexed = {}
    # Check paths BEFORE verify_manifest opens any snapshot member.
    for record in manifest["files"]:
        relative = Path(record["path"])
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or "protected_final" in {p.lower() for p in path.parts}:
            raise ValueError("Development manifest attempts protected or external access")
        key = relative.as_posix()
        if key in indexed:
            raise ValueError("Duplicate frozen input path")
        if key.startswith("data/normalized/") and key not in required:
            raise ValueError("Unapproved development table in manifest")
        allowed = (key in required or key == "configs/evaluation.json" or
                   re.fullmatch(r"src/klax_lab/[A-Za-z0-9_]+\.py", key) or
                   key in ("data/manifests/forecast_normalization.json", "data/manifests/market_quality.json") or
                   re.fullmatch(r"data/manifests/weather/range_[A-Za-z0-9]+\.json", key))
        if not allowed:
            raise ValueError("Unapproved frozen development input")
        indexed[key] = record
    if not required <= indexed.keys():
        raise ValueError("Frozen development manifest lacks required tables")
    code = {}
    for name in CODE_FILES:
        key = "src/klax_lab/" + name
        expected = indexed.get(key)
        if not expected or expected["sha256"] != sha256_file(Path(__file__).with_name(name)):
            raise ValueError("Running evaluator code is not in the frozen baseline snapshot")
        code[key] = expected["sha256"]
    verify_manifest(root, manifest)
    policy = manifest["policy"]["evaluation"]
    validate_evaluation_policy(policy)
    if "configs/evaluation.json" not in indexed or _read_json(root / "configs/evaluation.json") != policy:
        raise ValueError("Frozen evaluation policy differs from the registered policy file")
    return manifest, policy, code


def _read_development(root: Path, manifest: dict, policy: dict) -> dict:
    tables = {}
    for path in development_paths(root):
        split = path.parent.parent.name
        rows = pq.read_table(path).to_pylist()
        lower, upper = policy[split]
        if any(not lower <= row["climate_date"] <= upper for row in rows):
            raise ValueError("A frozen table contains data outside its assigned development partition")
        key = (split, path.stem)
        identity = "climate_date" if path.stem in ("forecasts", "climate") else "ticker"
        if path.stem != "climate_versions":
            identities = [(r["ticker"], r["end_period_ts"]) if path.stem == "candles" else r[identity] for r in rows]
            if len(identities) != len(set(identities)):
                raise ValueError("Duplicate development table identity")
        tables[key] = rows
    verify_manifest(root, manifest)
    return tables


def _training_and_eligibility(tables: dict, policy: dict) -> tuple[list, set, dict, list]:
    labels = {r["climate_date"]: r for r in tables[("weather_training", "climate")]}
    cutoff = utc(policy["model_fit_cutoff_utc"])
    training, training_exclusions = [], []
    for row in tables[("weather_training", "forecasts")]:
        day = row["climate_date"]
        decision = utc(day + f"T{policy['decision_hour_utc']:02d}:00:00Z")
        if utc(row["available_at"]) > decision:
            raise ValueError("Training forecast was unavailable at its historical decision cutoff")
        label = labels.get(day)
        if not label:
            training_exclusions.append({"climate_date": day, "reason": "missing_training_NWS_label"})
        elif utc(label["available_at"]) > cutoff:
            training_exclusions.append({"climate_date": day, "reason": "training_label_after_fit_cutoff"})
        else:
            training.append({**row, "tmax_f": label["tmax_f"], "label_available_at": label["available_at"],
                             "label_source_sha256": label.get("source_sha256")})
    features = tables[("selection", "forecasts")]
    for row in features:
        if utc(row["available_at"]) > utc(row["climate_date"] + f"T{policy['decision_hour_utc']:02d}:00:00Z"):
            raise ValueError("Selection forecast is unavailable at its information cutoff")
    contracts = tables[("selection", "contracts")]
    outcomes = tables[("selection", "outcomes")]
    reconciled = tables[("selection", "reconciliation")]
    labels = {r["climate_date"]: r for r in tables[("selection", "climate")]}
    feature_days = {r["climate_date"] for r in features}
    eligible = set(labels) & feature_days
    excluded = defaultdict(set)
    for row in outcomes:
        if row.get("mapping_consistent") is not True:
            excluded[row["climate_date"]].add("unverified_settlement_mapping")
    for row in reconciled:
        if row.get("settlement_label_reconciled") is not True:
            excluded[row["climate_date"]].add("NWS_label_not_reconciled_with_settlement")
    bad = {r["climate_date"] for r in outcomes if r.get("mapping_consistent") is not True}
    bad |= {r["climate_date"] for r in reconciled if r.get("settlement_label_reconciled") is not True}
    good = {r["ticker"] for r in reconciled if r.get("settlement_label_reconciled") is True}
    for row in contracts:
        day = row["climate_date"]
        if row["ticker"] not in good:
            bad.add(day)
            excluded[day].add("missing_reconciled_contract")
        if day not in labels:
            excluded[day].add("missing_NWS_label")
        if day not in feature_days:
            excluded[day].add("missing_or_quarantined_forecast")
    eligible -= bad
    lineage = {"eligible_days": sorted(eligible),
               "excluded": [{"climate_date": d, "reasons": sorted(reasons)} for d, reasons in sorted(excluded.items())],
               "training_exclusions": training_exclusions,
               "training_rows_sha256": canonical_hash(training),
               "limitation": "Same retrospective settlement-reconciliation subset as the frozen development baseline"}
    return training, eligible, labels, lineage


def _reference_basis(day: str) -> list[float]:
    value = date.fromisoformat(day)
    position = date(2000, value.month, value.day).timetuple().tm_yday
    theta = 2 * pi * (position - 1) / 366
    return [1., sin(theta), cos(theta)]


def _solve_three(matrix, target):
    augmented = [list(row) + [value] for row, value in zip(matrix, target)]
    for column in range(3):
        pivot = max(range(column, 3), key=lambda r: abs(augmented[r][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        if abs(scale) < 1e-10:
            raise ValueError("Independent harmonic fit is singular")
        augmented[column] = [v / scale for v in augmented[column]]
        for row in range(3):
            if row != column:
                scale = augmented[row][column]
                augmented[row] = [a - scale * b for a, b in zip(augmented[row], augmented[column])]
    return [row[3] for row in augmented]


def _reference_fit(spec: CandidateSpec, rows: list[dict]) -> dict:
    residuals = [r["tmax_f"] - (spec.gfs_weight * r["gfs"] + (1 - spec.gfs_weight) * r["nbm"]) for r in rows]
    global_bias = fsum(residuals) / len(rows)
    design = [_reference_basis(row["climate_date"]) for row in rows]
    gram = [[fsum(r[i] * r[j] for r in design) for j in range(3)] for i in range(3)]
    target = [fsum(r[i] * y for r, y in zip(design, residuals)) for i in range(3)]
    harmonic = _solve_three(gram, target)
    months = {str(m): [i for i, r in enumerate(rows) if date.fromisoformat(r["climate_date"]).month == m] for m in range(1, 13)}
    biases = {m: (fsum(residuals[i] for i in indices) + 30 * global_bias) / (len(indices) + 30) for m, indices in months.items()}
    errors = []
    for row, residual, basis in zip(rows, residuals, design):
        bias = global_bias if spec.bias_mode == "global" else biases[str(date.fromisoformat(row["climate_date"]).month)] if spec.bias_mode == "monthly_shrinkage" else fsum(a * b for a, b in zip(harmonic, basis))
        errors.append(residual - bias)
    variance = max(1., fsum(e * e for e in errors) / len(errors))
    return {"global_bias": global_bias, "monthly_bias": biases, "harmonic_coefficients": harmonic,
            "global_variance": variance,
            "monthly_variance": {m: max(1., (fsum(errors[i] ** 2 for i in indices) + 30 * variance) / (len(indices) + 30)) for m, indices in months.items()}}


def _reference_prediction(spec, fit, row):
    month = str(date.fromisoformat(row["climate_date"]).month)
    bias = fit["global_bias"] if spec.bias_mode == "global" else fit["monthly_bias"][month] if spec.bias_mode == "monthly_shrinkage" else fsum(a * b for a, b in zip(fit["harmonic_coefficients"], _reference_basis(row["climate_date"])))
    mean = spec.gfs_weight * row["gfs"] + (1 - spec.gfs_weight) * row["nbm"] + bias
    variance = fit["monthly_variance"][month] if spec.spread_mode == "monthly_shrinkage" else fit["global_variance"]
    if spec.spread_mode == "disagreement":
        variance += (spec.disagreement_coefficient * (row["gfs"] - row["nbm"])) ** 2
    return mean, max(1., sqrt(variance) * spec.spread_scale)


def _reference_probability(mean, sd, contract):
    lower, upper = contract["lower_integer_f"], contract["upper_integer_f"]
    lo = 0. if lower is None else (1 + erf((lower - .5 - mean) / (sd * sqrt(2)))) / 2
    hi = 1. if upper is None else (1 + erf((upper + .5 - mean) / (sd * sqrt(2)))) / 2
    return max(0., min(1., hi - lo))


def _close(actual, expected, label):
    if not isfinite(actual) or not isfinite(expected) or not isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8):
        raise ValueError("Independent verification failed: " + label)


def verify_candidate_fit(spec, training, model, features, predictions, contracts, eligible):
    reference = _reference_fit(spec, training)
    for key in ("global_bias", "global_variance"):
        _close(model[key], reference[key], key)
    for key in ("monthly_bias", "monthly_variance"):
        for month in reference[key]: _close(model[key][month], reference[key][month], key)
    for actual, expected in zip(model["harmonic_coefficients"], reference["harmonic_coefficients"]):
        _close(actual, expected, "harmonic fit")
    refs = {}
    for row in features:
        day = row["climate_date"]
        refs[day] = _reference_prediction(spec, reference, row)
        for actual, expected in zip(predictions[day], refs[day]): _close(actual, expected, "prediction")
    probability_count = 0
    for contract in contracts:
        day = contract["climate_date"]
        if day in eligible:
            actual = ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).probability(*predictions[day])
            expected = _reference_probability(*refs[day], contract)
            _close(actual, expected, "contract probability")
            probability_count += 1
    return {"fit_verified": True, "predictions_verified": len(features), "contract_probabilities_verified": probability_count,
            "method": "Separate Python sums/pivoted3x3 normal-equation fit and erf-CDF probabilities; no calls to candidate fit/predict in reference implementation"}


def verify_ledger_arithmetic(replay: dict, outcomes: list[dict], policy: dict, *, fee_rate=None) -> dict:
    actual = {row["ticker"]: row for row in outcomes}
    selected = {r["decision_id"]: r for r in replay["decisions"] if r["selected"]}
    ledger_ids = [r["decision_id"] for r in replay["ledger"]]
    if len(ledger_ids) != len(set(ledger_ids)) or set(ledger_ids) != set(selected):
        raise ValueError("Selected decisions and ledger counts differ")
    rate = Decimal(policy["fee_scenario"]["rate"] if fee_rate is None else fee_rate)
    quantum = Decimal(policy["fee_scenario"]["rounding"])
    total_profit, total_outlay = Decimal(0), Decimal(0)
    for row in replay["ledger"]:
        decision = selected[row["decision_id"]]
        n, price = decision["quantity"], Decimal(decision["entry_price"])
        fee = (rate * n * price * (1 - price) / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
        outlay = n * price + fee
        label = actual[row["ticker"]]["yes_outcome"]
        if type(label) is not int or label not in (0, 1):
            raise ValueError("Nonbinary payout label")
        if utc(row["settled_at"]) != utc(actual[row["ticker"]]["settlement_time"]) or utc(row["settled_at"]) < utc(str(decision["decision_at"])):
            raise ValueError("Ledger settlement timing differs from the actual outcome")
        payout = Decimal(n * (label if row["side"] == "YES" else 1 - label))
        profit = payout - outlay - Decimal(decision["settlement_cost"])
        expected = {"entry_fee": fee, "entry_outlay": outlay, "payout": payout, "net_profit": profit, "net_return": profit / outlay}
        if any(Decimal(row[key]) != value for key, value in expected.items()):
            raise ValueError("Independent ledger fee/outlay/payout/PnL check failed")
        if Decimal(decision["entry_fee"]) != fee or Decimal(decision["entry_outlay"]) != outlay:
            raise ValueError("Independent decision arithmetic check failed")
        total_profit += profit
        total_outlay += outlay
    summary = replay["summary"]
    if Decimal(summary["total_net_profit"]) != total_profit or Decimal(summary["total_entry_outlay"]) != total_outlay:
        raise ValueError("Independent aggregate PnL check failed")
    expected_roi = total_profit / total_outlay if total_outlay else None
    reported_roi = summary["capital_weighted_return"]
    if (None if reported_roi is None else Decimal(reported_roi)) != expected_roi:
        raise ValueError("Independent aggregate ROI check failed")
    expected_mean = sum((Decimal(r["net_return"]) for r in replay["ledger"]), Decimal(0)) / len(ledger_ids) if ledger_ids else None
    reported_mean = summary["mean_trade_return"]
    if summary["trade_count"] != len(ledger_ids) or (None if reported_mean is None else Decimal(reported_mean)) != expected_mean:
        raise ValueError("Independent trade count or mean ROI check failed")
    return {"ledger_rows_verified": len(replay["ledger"]), "selected_ledger_arithmetic_verified": True}


def _daywise(predictions, contracts, outcomes, labels, eligible):
    actual = {row["ticker"]: row["yes_outcome"] for row in outcomes}
    contract_predictions, errors = [], defaultdict(list)
    for contract in sorted(contracts, key=lambda r: r["ticker"]):
        day, ticker = contract["climate_date"], contract["ticker"]
        if day not in eligible or ticker not in actual: continue
        probability = ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).probability(*predictions[day])
        squared_error = (probability - actual[ticker]) ** 2
        errors[day].append(squared_error)
        contract_predictions.append({"ticker": ticker, "climate_date": day, "yes_probability": probability,
                                     "yes_outcome": actual[ticker], "brier_contribution": squared_error})
    scores = [{"climate_date": day, "crps_f": gaussian_crps(*predictions[day], labels[day]["tmax_f"]),
               "brier": fmean(errors[day]) if errors[day] else None, "brier_sum": fsum(errors[day]),
               "contract_count": len(errors[day])} for day in sorted(eligible)]
    return scores, contract_predictions


def _artifact_inventory(folder: Path) -> list[dict]:
    paths = [path for path in sorted(folder.rglob("*.json")) if path.name != "artifact_manifest.json"]
    if any(not path.resolve().is_relative_to(folder.resolve()) for path in paths):
        raise ValueError("Artifact inventory refers outside the experiment")
    return [{"path": path.relative_to(folder).as_posix(), "bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in paths]


def _read_completed(folder: Path, experiment_id: str) -> dict:
    manifest = _read_json(folder / "artifact_manifest.json")
    if manifest["experiment_id"] != experiment_id:
        raise ValueError("Existing experiment identity differs")
    required = {"candidate_spec.json", "fitted_model.json", "predictions.json", "contract_probabilities.json",
                "daywise_scores.json", "primary_ledger.json", "primary_decisions.json", "selection_eligibility.json",
                "provenance.json", "verification.json", "summary.json", "offline_checks.json"}
    names = [record["path"] for record in manifest["files"]]
    if len(names) != len(set(names)) or not required <= set(names):
        raise ValueError("Existing experiment inventory is incomplete or modified")
    for record in manifest["files"]:
        path = (folder / record["path"]).resolve()
        if not path.is_relative_to(folder.resolve()) or path.stat().st_size != record["bytes"] or sha256_file(path) != record["sha256"]:
            raise ValueError("Existing experiment artifact was modified")
    if manifest["files"] != _artifact_inventory(folder):
        raise ValueError("Existing experiment inventory is incomplete or modified")
    report = _read_json(folder / "summary.json")
    if report["status"] != "DEVELOPMENT_EXPERIMENT_COMPLETE":
        raise ValueError("Existing experiment is incomplete")
    return {"path": str(folder), **report}


def run_candidate_experiment(root: Path, manifest_path: Path, spec: CandidateSpec | dict,
                             *, output_root: Path | None = None, offline_checks: dict | None = None) -> dict:
    """Run one compiled V1 recipe or typed V2 plan on development data."""
    root = Path(root).resolve()
    spec, research_plan, policy_overrides = _resolve_plan(spec)
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute(): manifest_path = root / manifest_path
    manifest, base_policy, code = _guard_manifest(root, manifest_path)
    policy = json.loads(json.dumps(base_policy))
    policy.update(policy_overrides)
    baseline_folder = root / "runs" / f"baselines-{manifest['version'][:16]}"
    baseline_summary = _read_json(baseline_folder / "summary.json")
    if (baseline_summary.get("status") != "DEVELOPMENT_BASELINES_COMPLETE" or
            baseline_summary.get("dataset_version") != manifest["version"] or
            baseline_summary.get("protected_final_evaluated") is not False):
        raise ValueError("Matching completed development baselines are required")
    baseline_eligibility_path = baseline_folder / "selection_eligibility.json"
    baseline_eligibility = _read_json(baseline_eligibility_path)
    baseline_hashes = {p.name: sha256_file(p) for p in (baseline_folder / "summary.json", baseline_eligibility_path)}
    code_hash = canonical_hash(code)
    candidate_id = research_plan.identity if research_plan else spec.identity
    experiment_id = canonical_hash({"dataset_version": manifest["version"], "candidate_id": candidate_id, "code_hash": code_hash})
    output_root = (root / "runs/experiments" if output_root is None else Path(output_root)).resolve()
    if not output_root.is_relative_to((root / "runs").resolve()):
        raise ValueError("Experiment artifacts must stay within project runs")
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / experiment_id
    if output.exists():
        result = _read_completed(output, experiment_id)
        if result["baseline_artifacts"] != baseline_hashes:
            raise ValueError("Baseline evidence changed since experiment")
        verify_manifest(root, manifest)
        return result
    lock = output_root / (experiment_id + ".lock")
    with lock.open("x", encoding="utf-8") as handle: handle.write(experiment_id)
    # Keep temporary paths short enough for Windows' legacy path limit.
    staging = output_root / (".stg-" + experiment_id[:8] + "-" + uuid4().hex[:8])
    staging.mkdir()
    try:
        tables = _read_development(root, manifest, policy)
        training, eligible, labels, exclusions = _training_and_eligibility(tables, policy)
        if baseline_eligibility["eligible_days"] != exclusions["eligible_days"] or baseline_eligibility["excluded"] != exclusions["excluded"]:
            raise ValueError("Experiment eligibility differs from the completed baseline; no paired comparison allowed")
        model = fit_candidate(spec, training, policy["minimum_training_days"])
        model_hash = canonical_hash(model)
        execution_version = model_hash + (":" + candidate_id[:12] if research_plan else "")
        features = tables[("selection", "forecasts")]
        predictions = {r["climate_date"]: predict_candidate(model, r) for r in features}
        contracts, outcomes = tables[("selection", "contracts")], tables[("selection", "outcomes")]
        verification = verify_candidate_fit(spec, training, model, features, predictions, contracts, eligible)
        inputs = {"predictions": predictions, "contracts": contracts, "candles": tables[("selection", "candles")],
                  "outcomes": outcomes, "eligible": eligible, "policy": policy, "model_version": execution_version,
                  "dataset_version": manifest["version"]}
        primary = replay_model(**inputs)
        verification.update(verify_ledger_arithmetic(primary, outcomes, policy))
        verification.update(status="PARTIAL_INDEPENDENT_VERIFICATION", independent_verification_complete=False,
                            unverified=["Full opportunity generation and selection engine", "Bootstrap uncertainty computation", "Real historical fills and source availability"])
        scores, contract_predictions = _daywise(predictions, contracts, outcomes, labels, eligible)
        write_json(staging / "candidate_spec.json", asdict(spec))
        if research_plan:
            write_json(staging / "research_plan.json", research_plan.to_dict())
        write_json(staging / "fitted_model.json", model)
        write_json(staging / "predictions.json", [{"climate_date": r["climate_date"], "mean_f": predictions[r["climate_date"]][0],
                   "sd_f": predictions[r["climate_date"]][1], "feature_available_at": r["available_at"],
                   "eligible": r["climate_date"] in eligible, "model_sha256": model_hash} for r in features])
        write_json(staging / "contract_probabilities.json", contract_predictions)
        write_json(staging / "daywise_scores.json", scores)
        write_json(staging / "primary_ledger.json", primary["ledger"])
        write_json(staging / "primary_decisions.json", primary["decisions"])
        write_json(staging / "selection_eligibility.json", exclusions)
        assumptions = list(product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"]))
        if len(assumptions) != 12 or len(set(assumptions)) != 12:
            raise ValueError("The registered twelve unique cost sensitivities are required")
        sensitivities = []
        for slip, rate, quantity in assumptions:
            scenario = {"slippage": slip, "fee_rate": rate, "quantity": quantity}
            scenario_id = canonical_hash(scenario)[:12]
            replay = replay_model(**{**inputs, "model_version": execution_version + ":" + scenario_id}, **scenario)
            checked = verify_ledger_arithmetic(replay, outcomes, policy, fee_rate=rate)
            write_json(staging / "sensitivity" / f"{scenario_id}_ledger.json", replay["ledger"])
            write_json(staging / "sensitivity" / f"{scenario_id}_decisions.json", replay["decisions"])
            sensitivities.append({"scenario_id": scenario_id, **scenario, "summary": replay["summary"], "verification": checked})
        verify_manifest(root, manifest)
        if any(sha256_file(baseline_folder / name) != value for name, value in baseline_hashes.items()):
            raise ValueError("Baseline evidence changed during experiment")
        provenance = {"manifest_path": manifest_path.resolve().relative_to(root).as_posix(), "dataset_version": manifest["version"],
                      "candidate_id": candidate_id, "compiled_candidate_id": spec.identity,
                      "research_plan_sha256": research_plan.identity if research_plan else None,
                      "model_sha256": model_hash, "execution_model_version": execution_version, "code_sha256": code_hash,
                      "code_files": code, "policy_sha256": canonical_hash(policy), "baseline_artifacts": baseline_hashes,
                      "base_policy_sha256": canonical_hash(base_policy),
                      "training_rows_sha256": exclusions["training_rows_sha256"],
                      "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pyarrow": pyarrow.__version__}}
        write_json(staging / "provenance.json", provenance)
        write_json(staging / "verification.json", verification)
        write_json(staging / "offline_checks.json", offline_checks or {"scope": "Direct API invocation; offline guard not asserted"})
        report = {"status": "DEVELOPMENT_EXPERIMENT_COMPLETE", "experiment_id": experiment_id, **provenance,
                  "training_days": len(training), "eligible_selection_days": len(eligible),
                  "forecast_scores": probability_scores(predictions, contracts, outcomes, labels, eligible),
                  "historical_assumed_fill": primary["summary"], "predeclared_cost_sensitivity": sensitivities,
                  "verification": verification, "protected_final_evaluated": False,
                  "paired_comparison_artifact": "daywise_scores.json",
                  "limitations": ["Selection-period research results are not untouched final evidence", "All PnL is simulated with hourly-summary fill assumptions", "Eligibility uses retrospective settlement reconciliation", "Independent verification scope is partial and explicit"]}
        write_json(staging / "summary.json", report)
        write_json(staging / "artifact_manifest.json", {"experiment_id": experiment_id, "files": _artifact_inventory(staging)})
        verify_manifest(root, manifest)
        staging.rename(output)
        return {"path": str(output), **report}
    except BaseException as error:
        write_json(staging / "failure.json", {"status": "FAILED", "error_type": type(error).__name__, "reason": str(error),
                                               "dataset_version": manifest["version"], "candidate_id": candidate_id})
        raise
    finally:
        lock.unlink(missing_ok=True)


def _research_replay_policy(policy: dict) -> tuple[Decimal, tuple[str, ...]]:
    target = Decimal(policy.get("research_target_expected_net_return",
                                policy["target_expected_net_return"]))
    allowed = policy.get("research_allowed_sides", ["YES", "NO"])
    if (not target.is_finite() or not isinstance(allowed, list) or not allowed
            or len(set(allowed)) != len(allowed)
            or any(side not in ("YES", "NO") for side in allowed)):
        raise ValueError("Invalid effective research replay policy")
    return target, tuple(allowed)


def _verify_decisions(decisions, ledger, contracts, candles, predictions, eligible, policy,
                      model_hash, dataset_version, *, slippage=None, fee_rate=None, quantity=None):
    """Check saved decisions against sources; does not prove none were omitted."""
    indexed = {r["ticker"]: r for r in contracts}
    quotes = defaultdict(list)
    for row in candles: quotes[row["ticker"]].append(row)
    rate = Decimal(policy["fee_scenario"]["rate"] if fee_rate is None else fee_rate)
    quantum = Decimal(policy["fee_scenario"]["rounding"])
    slip = Decimal(policy["slippage_per_contract"] if slippage is None else slippage)
    n = policy["reference_quantity"] if quantity is None else quantity
    target_return, allowed_sides = _research_replay_policy(policy)
    ids = set()
    for row in decisions:
        day, ticker, side = row["climate_date"], row["ticker"], row["side"]
        if side not in allowed_sides or day not in eligible or ticker not in indexed:
            raise ValueError("Decision is outside eligible contracts")
        expected_id = f"{model_hash}:{day}:{ticker}:{side}"
        if row["decision_id"] != expected_id or expected_id in ids:
            raise ValueError("Duplicate or unbound decision identity")
        ids.add(expected_id)
        if row["model_version"] != model_hash or row["dataset_version"] != dataset_version or row["quantity"] != n:
            raise ValueError("Decision lineage or scenario differs")
        contract = indexed[ticker]
        cutoff = utc(day + f"T{policy['decision_hour_utc']:02d}:00:00Z")
        entry = cutoff + timedelta(seconds=policy["execution_delay_seconds"])
        if (contract["climate_date"] != day or utc(row["information_cutoff"]) != cutoff or
                utc(row["decision_at"]) != entry or not utc(contract["open_time"]) <= cutoff <= entry < utc(contract["close_time"])):
            raise ValueError("Decision violates information cutoff or market hours")
        available = [r for r in quotes[ticker] if r["end_period_ts"] <= cutoff.timestamp()]
        if not available: raise ValueError("Decision has no pre-cutoff candle")
        quote = max(available, key=lambda r: r["end_period_ts"])
        base_price = Decimal(quote["yes_ask_close"]) if side == "YES" else 1 - Decimal(quote["yes_bid_close"])
        price = base_price + slip
        if row["price_source"] != quote["source_sha256"] or Decimal(row["entry_price"]) != price:
            raise ValueError("Decision price/source does not match the frozen candle")
        probability = _reference_probability(*predictions[day], contract)
        purchased = probability if side == "YES" else 1 - probability
        _close(float(row["purchased_probability"]), purchased, "saved decision probability")
        fee = (rate * n * price * (1 - price) / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
        outlay = n * price + fee
        # Recompute from the persisted probability after separately bounding its
        # floating-point difference from the independent CDF.
        expected_profit = Decimal(n) * Decimal(row["purchased_probability"]) - outlay - Decimal(row["settlement_cost"])
        if (Decimal(row["entry_fee"]) != fee or Decimal(row["entry_outlay"]) != outlay or
                Decimal(row["expected_net_profit"]) != expected_profit or Decimal(row["expected_net_return"]) != expected_profit / outlay):
            raise ValueError("Saved decision expected-return arithmetic failed")
        if row["selected"] and (row["status"] != "ACCEPTED" or expected_profit / outlay < target_return):
            raise ValueError("Selected decision failed the registered edge screen")
    days = [r["climate_date"] for r in ledger]
    if len(days) != len(set(days)):
        raise ValueError("Saved ledger violates the daily entry cap")
    selected = {r["decision_id"]: r for r in decisions if r["selected"]}
    for row in ledger:
        decision = selected[row["decision_id"]]
        for key in ("side", "quantity", "ticker", "climate_date"):
            if row[key] != decision[key]: raise ValueError("Ledger and decision identity differ")
    return len(decisions)


def _verify_primary_selection(tables, policy, predictions, decisions, ledger, summary, model_hash):
    # This enumeration is separate from replay_model/evaluate_purchase. It uses
    # saved probabilities only after independent CDF verification, so tiny
    # float-to-decimal differences cannot change exact ranking at a tie.
    labels = {r["climate_date"] for r in tables[("selection", "climate")]}
    features = {r["climate_date"] for r in tables[("selection", "forecasts")]}
    actual = {r["ticker"]: r for r in tables[("selection", "outcomes")]}
    reconciled = tables[("selection", "reconciliation")]
    good = {r["ticker"] for r in reconciled if r.get("settlement_label_reconciled") is True}
    bad = {r["climate_date"] for r in actual.values() if r.get("mapping_consistent") is not True}
    bad |= {r["climate_date"] for r in reconciled if r.get("settlement_label_reconciled") is not True}
    contracts = tables[("selection", "contracts")]
    bad |= {r["climate_date"] for r in contracts if r["ticker"] not in good}
    eligible = (labels & features) - bad
    recorded = {r["decision_id"]: r for r in decisions}
    quotes = defaultdict(list)
    for row in tables[("selection", "candles")]: quotes[row["ticker"]].append(row)
    rate, quantum = Decimal(policy["fee_scenario"]["rate"]), Decimal(policy["fee_scenario"]["rounding"])
    slip, n = Decimal(policy["slippage_per_contract"]), policy["reference_quantity"]
    target_return, allowed_sides = _research_replay_policy(policy)
    expected, accepted = set(), defaultdict(list)
    side_policy_skips = 0
    for contract in contracts:
        day, ticker = contract["climate_date"], contract["ticker"]
        if day not in eligible or ticker not in actual or actual[ticker].get("mapping_consistent") is not True: continue
        cutoff = utc(day + f"T{policy['decision_hour_utc']:02d}:00:00Z")
        entry = cutoff + timedelta(seconds=policy["execution_delay_seconds"])
        if not utc(contract["open_time"]) <= cutoff <= entry < utc(contract["close_time"]): continue
        completed = [r for r in quotes[ticker] if r["end_period_ts"] <= cutoff.timestamp()]
        if not completed: continue
        quote = max(completed, key=lambda r: r["end_period_ts"])
        if quote["climate_date"] != day: raise ValueError("Primary candle belongs to another weather day")
        yes_probability = _reference_probability(*predictions[day], contract)
        for side, field in (("YES", "yes_ask_close"), ("NO", "yes_bid_close")):
            if side not in allowed_sides:
                side_policy_skips += 1
                continue
            if quote[field] is None: continue
            base = Decimal(quote[field]) if side == "YES" else 1 - Decimal(quote[field])
            price = base + slip
            if not 0 < base < 1 or not 0 < price < 1: continue
            key = f"{model_hash}:{day}:{ticker}:{side}"
            expected.add(key)
            row = recorded.get(key)
            if row is None: raise ValueError("Primary opportunity was omitted from decisions")
            independent_p = yes_probability if side == "YES" else 1 - yes_probability
            if abs(float(row["purchased_probability"]) - independent_p) > 1e-10:
                raise ValueError("Primary selection probability differs from independent CDF")
            fee = (rate * n * price * (1 - price) / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum
            outlay = n * price + fee
            roi = (n * Decimal(row["purchased_probability"]) - outlay) / outlay
            status, reason = "SKIPPED", "EXPECTED_RETURN_BELOW_TARGET"
            if quote["end_period_ts"] + 60 > entry.timestamp(): reason = "PRICE_NOT_AVAILABLE_AS_OF_DECISION"
            elif entry.timestamp() - quote["end_period_ts"] > policy["max_quote_age_seconds"]: reason = "STALE_PRICE"
            elif roi >= target_return: status, reason = "ACCEPTED", "ASSUMED_FILL_SCENARIO"
            if row["status"] != status or row["reason"] != reason or Decimal(row["settlement_cost"]) != 0:
                raise ValueError("Primary opportunity status or reason differs")
            if status == "ACCEPTED": accepted[day].append((roi, ticker, side, key))
    if set(recorded) != expected: raise ValueError("Primary decision universe differs")
    selected = {min(rows, key=lambda r: (-r[0], r[1], r[2]))[3] for rows in accepted.values()}
    if {r["decision_id"] for r in decisions if r["selected"]} != selected or {r["decision_id"] for r in ledger} != selected:
        raise ValueError("Primary daily highest-ROI selection differs")
    if summary.get("skipped_reasons", {}).get("SIDE_POLICY", 0) != side_policy_skips:
        raise ValueError("Primary side-policy skip count differs")
    if summary["opportunity_days"] != len(eligible): raise ValueError("Primary opportunity-day count differs")
    return {"primary_candidate_selection_verified": True, "primary_opportunities_verified": len(expected),
            "primary_selected_days_verified": len(selected), "probability_tolerance": 1e-10}


def verify_candidate_experiment(root: Path, manifest_path: Path, run_path: Path) -> dict:
    """Independently refit and verify persisted artifacts; read-only, no replay calls.

    A PASS includes independent primary opportunity enumeration and selection.
    Sensitivity selection completeness and bootstrap are outside its scope.
    """
    root = Path(root).resolve()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute(): manifest_path = root / manifest_path
    manifest, base_policy, code = _guard_manifest(root, manifest_path)
    folder = Path(run_path).resolve()
    if not folder.is_relative_to(root / "runs") or not re.fullmatch(r"[0-9a-f]{64}", folder.name):
        raise ValueError("Verification requires a completed experiment within project runs")
    report = _read_completed(folder, folder.name)
    initial_inventory_hash = sha256_file(folder / "artifact_manifest.json")
    provenance = _read_json(folder / "provenance.json")
    spec = _spec(_read_json(folder / "candidate_spec.json"))
    research_plan = ResearchPlan.from_dict(_read_json(folder / "research_plan.json")) if (folder / "research_plan.json").is_file() else None
    if research_plan:
        compiled = compile_plan(research_plan)
        if compiled.candidate != spec:
            raise ValueError("Research plan compiles to another candidate")
        policy = json.loads(json.dumps(base_policy))
        policy.update(compiled.policy_overrides)
        candidate_id = research_plan.identity
    else:
        policy = base_policy
        candidate_id = spec.identity
    expected_id = canonical_hash({"dataset_version": manifest["version"], "candidate_id": candidate_id, "code_hash": canonical_hash(code)})
    if report["experiment_id"] != expected_id or folder.name != expected_id or report["protected_final_evaluated"] is not False:
        raise ValueError("Experiment identity or partition does not match the frozen snapshot")
    expected_provenance = {"dataset_version": manifest["version"], "candidate_id": candidate_id,
                            "compiled_candidate_id": spec.identity,
                            "research_plan_sha256": research_plan.identity if research_plan else None,
                            "code_sha256": canonical_hash(code), "code_files": code, "policy_sha256": canonical_hash(policy),
                            "base_policy_sha256": canonical_hash(base_policy),
                            "manifest_path": manifest_path.resolve().relative_to(root).as_posix()}
    for key, value in expected_provenance.items():
        if provenance[key] != value or report[key] != value:
            raise ValueError("Persisted experiment provenance mismatch: " + key)
    baseline = root / "runs" / f"baselines-{manifest['version'][:16]}"
    baseline_hashes = {name: sha256_file(baseline / name) for name in ("summary.json", "selection_eligibility.json")}
    if baseline_hashes != provenance["baseline_artifacts"] or report["baseline_artifacts"] != baseline_hashes:
        raise ValueError("Baseline evidence changed since experiment")
    tables = _read_development(root, manifest, policy)
    training, eligible, labels, exclusions = _training_and_eligibility(tables, policy)
    if len(training) < policy["minimum_training_days"]:
        raise ValueError("Insufficient training days")
    if report["training_days"] != len(training) or report["eligible_selection_days"] != len(eligible):
        raise ValueError("Experiment training or selection count differs")
    if _read_json(folder / "selection_eligibility.json") != exclusions:
        raise ValueError("Persisted exclusion lineage differs")
    prior = _read_json(baseline / "selection_eligibility.json")
    if prior["eligible_days"] != exclusions["eligible_days"] or prior["excluded"] != exclusions["excluded"]:
        raise ValueError("Persisted eligibility differs from baseline")
    if provenance["training_rows_sha256"] != canonical_hash(training) or report["training_rows_sha256"] != canonical_hash(training):
        raise ValueError("Training row lineage differs")
    model = _read_json(folder / "fitted_model.json")
    model_hash = canonical_hash(model)
    execution_version = model_hash + (":" + candidate_id[:12] if research_plan else "")
    if provenance.get("execution_model_version") != execution_version or report.get("execution_model_version") != execution_version:
        raise ValueError("Execution model version differs")
    if (model_hash != provenance["model_sha256"] or model_hash != report["model_sha256"] or model["candidate_id"] != spec.identity or
            model["spec"] != asdict(spec) or model["training_days"] != len(training) or model["monthly_prior_count"] != 30 or
            model["recipe_version"] != "gaussian-calibration-v1" or len(model["harmonic_coefficients"]) != 3):
        raise ValueError("Fitted model identity differs")
    features = tables[("selection", "forecasts")]
    reference = _reference_fit(spec, training)
    predictions = {r["climate_date"]: _reference_prediction(spec, reference, r) for r in features}
    contracts, outcomes = tables[("selection", "contracts")], tables[("selection", "outcomes")]
    fit_check = verify_candidate_fit(spec, training, model, features, predictions, contracts, eligible)
    saved = _read_json(folder / "predictions.json")
    if len(saved) != len(features) or {r["climate_date"] for r in saved} != set(predictions):
        raise ValueError("Persisted prediction dates differ")
    feature_index = {r["climate_date"]: r for r in features}
    for row in saved:
        day = row["climate_date"]
        _close(row["mean_f"], predictions[day][0], "persisted mean")
        _close(row["sd_f"], predictions[day][1], "persisted spread")
        if row["model_sha256"] != model_hash or row["eligible"] != (day in eligible) or row["feature_available_at"] != feature_index[day]["available_at"]:
            raise ValueError("Persisted prediction lineage differs")
    actual = {r["ticker"]: r["yes_outcome"] for r in outcomes}
    expected_contracts = {r["ticker"]: r for r in contracts if r["climate_date"] in eligible and r["ticker"] in actual}
    saved_contracts = _read_json(folder / "contract_probabilities.json")
    if len(saved_contracts) != len(expected_contracts) or {r["ticker"] for r in saved_contracts} != set(expected_contracts):
        raise ValueError("Persisted contract coverage differs")
    errors = defaultdict(list)
    probabilities, results = [], []
    for row in saved_contracts:
        contract = expected_contracts[row["ticker"]]
        day, outcome = contract["climate_date"], actual[row["ticker"]]
        probability = _reference_probability(*predictions[day], contract)
        error = (probability - outcome) ** 2
        if row["climate_date"] != day or row["yes_outcome"] != outcome: raise ValueError("Persisted scoring label differs")
        _close(row["yes_probability"], probability, "persisted contract probability")
        _close(row["brier_contribution"], error, "persisted Brier contribution")
        errors[day].append(error)
        probabilities.append(probability)
        results.append(outcome)
    daywise = _read_json(folder / "daywise_scores.json")
    if len(daywise) != len(eligible) or {r["climate_date"] for r in daywise} != eligible:
        raise ValueError("Persisted daywise score coverage differs")
    crps_values, differences = [], []
    for row in daywise:
        day = row["climate_date"]
        mean, sd = predictions[day]
        z = (labels[day]["tmax_f"] - mean) / sd
        crps = sd * (z * erf(z / sqrt(2)) + 2 * exp(-z * z / 2) / sqrt(2 * pi) - 1 / sqrt(pi))
        _close(row["crps_f"], crps, "independent daywise CRPS")
        _close(row["brier_sum"], fsum(errors[day]), "independent daywise Brier sum")
        if row["contract_count"] != len(errors[day]): raise ValueError("Daywise contract count differs")
        if errors[day]: _close(row["brier"], fsum(errors[day]) / len(errors[day]), "independent daywise Brier")
        elif row["brier"] is not None: raise ValueError("Brier score exists without contracts")
        crps_values.append(crps)
        differences.append(mean - labels[day]["tmax_f"])
    scores = report["forecast_scores"]
    if scores["weather_days"] != len(eligible) or scores["contract_count"] != len(expected_contracts):
        raise ValueError("Forecast aggregate coverage differs")
    for key, values in (("gaussian_crps_f", crps_values), ("mae_f", [abs(d) for d in differences]),
                        ("bias_f", differences), ("brier", [e for rows in errors.values() for e in rows])):
        if values: _close(scores[key], fsum(values) / len(values), "aggregate " + key)
        elif scores[key] is not None: raise ValueError("Aggregate score exists without observations")
    scenarios = [("primary", {}, report["historical_assumed_fill"], execution_version)]
    expected_sensitivities = {canonical_hash({"slippage": s, "fee_rate": r, "quantity": q})[:12]: {"slippage": s, "fee_rate": r, "quantity": q}
                              for s, r, q in product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"])}
    stored = report["predeclared_cost_sensitivity"]
    if len(expected_sensitivities) != 12 or len(stored) != 12 or {r["scenario_id"] for r in stored} != set(expected_sensitivities):
        raise ValueError("Persisted sensitivity scenarios differ")
    for scenario in stored:
        key = scenario["scenario_id"]
        params = expected_sensitivities[key]
        if any(scenario[name] != value for name, value in params.items()): raise ValueError("Sensitivity assumptions differ")
        scenarios.append(("sensitivity/" + key, params, scenario["summary"], execution_version + ":" + key))
    checks = []
    selection_check = {}
    for prefix, params, summary, version in scenarios:
        ledger = _read_json(folder / (prefix + "_ledger.json"))
        decisions = _read_json(folder / (prefix + "_decisions.json"))
        checked = verify_ledger_arithmetic({"ledger": ledger, "decisions": decisions, "summary": summary}, outcomes, policy, fee_rate=params.get("fee_rate"))
        checked["decisions_verified"] = _verify_decisions(decisions, ledger, contracts, tables[("selection", "candles")], predictions,
                                                         eligible, policy, version, manifest["version"], **params)
        if prefix == "primary":
            selection_check = _verify_primary_selection(tables, policy, predictions, decisions, ledger, summary, execution_version)
        checks.append({"scenario": prefix, **checked})
    verify_manifest(root, manifest)
    _read_completed(folder, expected_id)
    if sha256_file(folder / "artifact_manifest.json") != initial_inventory_hash:
        raise ValueError("Experiment artifacts changed during verification")
    if any(sha256_file(baseline / name) != digest for name, digest in baseline_hashes.items()):
        raise ValueError("Baseline evidence changed during verification")
    return {"status": "PASS", "experiment_id": expected_id, "path": str(folder), "dataset_version": manifest["version"],
            "artifact_manifest_sha256": initial_inventory_hash,
            "model_sha256": model_hash, "code_sha256": canonical_hash(code), "fit": fit_check, "scenarios": checks,
            **selection_check,
            "daywise_scores_verified": len(daywise), "protected_final_evaluated": False,
            "scope": "Independent reference fit, persisted predictions, CRPS/Brier, primary opportunity enumeration/daily selection, and all scenario decision/ledger arithmetic",
            "independent_verification_complete": False,
            "unverified": ["Sensitivity opportunity generation and selection completeness", "Bootstrap uncertainty computation", "Real historical fills and public availability"]}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from .cli import run_guard_checks

    parser = argparse.ArgumentParser(description="Frozen development candidate experiments; no acquisition or final-test access")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--spec-file", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "run" and args.spec_file is None: parser.error("run requires --spec-file")
    if args.command == "verify" and (args.run_directory is None or args.output is None): parser.error("verify requires --run-directory and --output")
    root = args.root.resolve()
    try:
        # NumPy/PyArrow are loaded by this module before the irreversible hook.
        checks = run_guard_checks(root)
        if args.command == "run":
            report = run_candidate_experiment(root, args.manifest, _read_json(args.spec_file), output_root=args.output_root, offline_checks=checks)
        else:
            output = args.output.resolve()
            if not output.is_relative_to(root / "runs") or output.is_relative_to(args.run_directory.resolve()):
                raise ValueError("Verification output must be separate from the immutable experiment, within project runs")
            write_json(output.parent / "offline_checks.json", checks)
            report = verify_candidate_experiment(root, args.manifest, args.run_directory)
            write_json(output, report)
        print(json.dumps(report, default=str, allow_nan=False))
        return 0
    except Exception as error:
        report = {"status": "FAIL", "error_type": type(error).__name__, "reason": str(error), "protected_final_evaluated": False}
        print(json.dumps(report, allow_nan=False))
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
