"""One-use protected historical evaluation of already-frozen models.

No agents, candidate generation, calibration fitting or acquisition are called.
The project ticket is durably consumed before any protected input is hashed or
opened. Failed attempts require explicit review; automatic retries are refused.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from itertools import product
import json
from math import erf, exp, fsum, pi, sqrt
import os
from pathlib import Path

import pyarrow.parquet as pq

from .baseline import probability_scores, replay_model, utc
from .candidates import CandidateSpec, predict_candidate
from .campaign import assess_development_gate
from .experiments import (_close, _daywise, _guard_manifest, _read_completed, _read_json,
                          _reference_prediction, _reference_probability, _spec,
                          _verify_decisions, _verify_primary_selection, verify_ledger_arithmetic)
from .models import BASELINE_DEFINITIONS, predict
from .provenance import canonical_hash, freeze_manifest, inventory, sha256_file, verify_manifest, write_json
from .readiness import code_fingerprint
from .replication import _Checks, _audit_settlement_mapping, independent_prediction
from .research_plan import ResearchPlan, STAGES, compile_plan, stage_gate


TICKET_PATH = "data/manifests/protected_final_ticket.json"
FINAL_NAMES = (("features", ("forecasts", "contracts", "candles")),
               ("labels", ("climate", "climate_versions", "outcomes", "reconciliation")))


def _within(root: Path, path: Path, prefix: str) -> Path:
    path = Path(path)
    if not path.is_absolute(): path = root / path
    path = path.resolve()
    if not path.is_relative_to((root / prefix).resolve()):
        raise ValueError("Artifact path lies outside " + prefix)
    return path


def final_paths(root: Path) -> list[Path]:
    return [root / "data/normalized/protected_final" / kind / (name + ".parquet")
            for kind, names in FINAL_NAMES for name in names]


def _checked_artifact(root: Path, item: dict, prefix: str) -> Path:
    path = _within(root, Path(item["path"]), prefix)
    if sha256_file(path) != item["sha256"]:
        raise ValueError("Bound development artifact changed: " + path.name)
    return path


def _preflight(root: Path, campaign_path: Path) -> dict:
    """Only development evidence is read here; no protected-table operations."""
    campaign_path = _within(root, campaign_path, "runs/campaigns")
    if campaign_path.name != "summary.json": raise ValueError("Campaign summary.json is required")
    campaign = _read_json(campaign_path)
    if campaign.get("status") != "RESEARCH_CYCLE_COMPLETE" or campaign.get("protected_final_evaluated") is not False:
        raise ValueError("An unexposed completed research cycle is required")
    champion = campaign.get("champion")
    if champion is None:
        return {"qualified": False, "campaign": campaign, "campaign_path": campaign_path}
    if champion.get("gate", {}).get("passed") is not True:
        raise ValueError("Campaign champion did not pass its fixed gate")
    readiness_path = root / "data/manifests/readiness.json"
    ready = _read_json(readiness_path)
    if ready.get("status") != "READY_FOR_OFFLINE_CAMPAIGN" or sha256_file(readiness_path) != campaign["readiness_sha256"]:
        raise ValueError("Campaign readiness attestation changed or failed")
    if campaign.get("architecture_version") == 2 and ready.get("architecture_version") != 2:
        raise ValueError("V2 campaign requires a matching V2 readiness attestation")
    pilot_path, policy_path = root / "configs/pilot.json", root / "configs/evaluation.json"
    pilot = _read_json(pilot_path)
    if (sha256_file(pilot_path) != ready["pilot_policy_sha256"] or ready["pilot_policy_sha256"] != campaign["pilot_policy_sha256"] or
            sha256_file(policy_path) != ready["evaluation_policy_sha256"] or code_fingerprint(root) != ready["code_sha256"]):
        raise ValueError("Registered code or policies changed since readiness")
    manifest_path = _within(root, Path(ready["development_manifest_path"]), "data/manifests")
    if sha256_file(manifest_path) != ready["development_manifest_sha256"]:
        raise ValueError("Development snapshot attestation changed")
    manifest, policy, _ = _guard_manifest(root, manifest_path)
    indexed = {record["path"]: record for record in manifest["files"]}
    architecture_version = campaign.get("architecture_version", 1)
    frozen_sources = ["final_evaluation.py", "campaign.py", "replication.py", "readiness.py"]
    if architecture_version == 2:
        frozen_sources.extend(["campaign_v2.py", "research_plan.py", "research_protocol.py"])
    for name in frozen_sources:
        key = "src/klax_lab/" + name
        if key not in indexed or indexed[key]["sha256"] != sha256_file(Path(__file__).with_name(name)):
            raise ValueError("Final evaluator code was not frozen before discovery")
    experiment_path = _within(root, Path(champion["experiment_path"]), "runs/campaigns")
    experiment = _read_completed(experiment_path, champion["experiment_id"])
    spec = _spec(champion["spec"])
    research_plan = None
    champion_policy = json.loads(json.dumps(policy))
    expected_candidate_id = spec.identity
    if architecture_version == 2:
        research_plan = ResearchPlan.from_dict(champion["plan"])
        compiled = compile_plan(research_plan)
        if asdict(compiled.candidate) != asdict(spec):
            raise ValueError("V2 plan compiles to a different candidate recipe")
        expected_candidate_id = research_plan.identity
        champion_policy.update(compiled.policy_overrides)
        if (_read_json(experiment_path / "research_plan.json") != research_plan.to_dict()
                or experiment.get("research_plan_sha256") != expected_candidate_id
                or experiment.get("compiled_candidate_id") != spec.identity):
            raise ValueError("Frozen V2 research plan changed")
    if (champion["candidate_id"] != expected_candidate_id or experiment["candidate_id"] != expected_candidate_id or
            experiment["dataset_version"] != manifest["version"] or experiment["protected_final_evaluated"] is not False or
            _read_json(experiment_path / "candidate_spec.json") != asdict(spec)):
        raise ValueError("Champion recipe or dataset identity differs")
    model = _read_json(experiment_path / "fitted_model.json")
    if canonical_hash(model) != experiment["model_sha256"] or model["candidate_id"] != spec.identity:
        raise ValueError("Frozen champion model changed")
    replica_path = _within(root, Path(champion["replication_path"]), "runs/campaigns")
    replica = _read_json(replica_path)
    if (replica.get("status") != "PASS" or replica.get("primary_candidate_selection_verified") is not True or
            replica["experiment_id"] != experiment["experiment_id"] or replica["dataset_version"] != manifest["version"] or
            replica["model_sha256"] != canonical_hash(model) or replica["code_sha256"] != experiment["code_sha256"] or
            replica["artifact_manifest_sha256"] != sha256_file(experiment_path / "artifact_manifest.json")):
        raise ValueError("Independent champion verification is absent, stale or incomplete")
    baseline_folder = _within(root, Path(ready["baseline_run_path"]), "runs")
    if baseline_folder.name != "baselines-" + manifest["version"][:16]: raise ValueError("Baseline identity differs")
    for item in ready["baseline_artifacts"]: _checked_artifact(root, item, baseline_folder.relative_to(root).as_posix())
    baseline_summary = _read_json(baseline_folder / "summary.json")
    baseline_models = _read_json(baseline_folder / "fitted_models.json")
    if (baseline_summary["dataset_version"] != manifest["version"] or baseline_summary.get("protected_final_evaluated") is not False or
            set(baseline_models) != set(BASELINE_DEFINITIONS)):
        raise ValueError("Five frozen development baselines are required")
    # Bind fitted parameters to the independently verified development artifacts,
    # rather than trusting a file that could have changed after its original run.
    baseline_replica = _read_json(baseline_folder / "independent_replication.json")
    model_relative = (baseline_folder / "fitted_models.json").relative_to(root).as_posix()
    model_items = [item for item in baseline_replica["artifact_hashes"] if item["path"] == model_relative]
    if baseline_replica.get("status") != "PASS" or len(model_items) != 1:
        raise ValueError("Baseline fitted-model attestation is missing")
    _checked_artifact(root, model_items[0], baseline_folder.relative_to(root).as_posix())
    reference_name = min(baseline_summary["models"], key=lambda name: (baseline_summary["models"][name]["forecast_scores"]["gaussian_crps_f"], name))
    if reference_name != campaign["reference_baseline"]:
        raise ValueError("Campaign reference baseline differs from the fixed ranking")
    gate = assess_development_gate(experiment, baseline_summary["models"][reference_name], replica, champion["critic"], pilot["development_gate"])
    if not gate["passed"]: raise ValueError("Champion no longer passes the fixed development gate")
    if architecture_version == 2:
        critic_allowed = champion["critic"].get("action") == "propose"
        if critic_allowed:
            critic_allowed = ResearchPlan.from_dict(champion["critic"]["plan"]).identity == expected_candidate_id
        gates = {stage: stage_gate(stage, experiment, baseline_summary["models"][reference_name],
                                   champion["rolling_folds"], pilot["stage_gates"],
                                   independently_verified=True, critic_allowed=critic_allowed)
                 for stage in STAGES}
        if gates != champion.get("stage_gates") or not all(item["passed"] for item in gates.values()):
            raise ValueError("Champion no longer passes every V2 stage gate")
    bound_paths = [campaign_path, readiness_path, pilot_path, policy_path, manifest_path, replica_path,
                   experiment_path / "artifact_manifest.json", experiment_path / "fitted_model.json", experiment_path / "candidate_spec.json",
                   baseline_folder / "fitted_models.json", baseline_folder / "summary.json", baseline_folder / "independent_replication.json"]
    if research_plan is not None:
        bound_paths.append(experiment_path / "research_plan.json")
    binding = {"campaign_id": campaign["campaign_id"], "campaign_summary_path": campaign_path.relative_to(root).as_posix(),
               "experiment_id": experiment["experiment_id"], "candidate_id": expected_candidate_id,
               "champion_model_sha256": canonical_hash(model), "champion_spec_sha256": canonical_hash(asdict(spec)),
               "research_plan_sha256": research_plan.identity if research_plan else None,
               "development_dataset_version": manifest["version"], "code_sha256": ready["code_sha256"],
               "bound_artifacts": inventory(root, bound_paths)}
    return {"qualified": True, "campaign": campaign, "campaign_path": campaign_path, "binding": binding,
            "model": model, "baseline_models": baseline_models, "policy": policy,
            "champion_policy": champion_policy, "research_plan": research_plan,
            "execution_model_version": experiment.get("execution_model_version", canonical_hash(model)),
            "manifest": manifest, "reference_baseline": reference_name}


def _consume_ticket(root: Path, binding: dict) -> dict:
    path = root / TICKET_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = {"status": "CONSUMED", "ticket_id": canonical_hash(binding), "binding": binding,
              "consumed_at_utc": datetime.now(timezone.utc).isoformat(),
              "rule": "One protected evaluation per project; failed or interrupted tickets prohibit automatic retry"}
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(ticket, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return ticket


def _existing_result(root: Path, campaign_path: Path) -> dict | None:
    path = root / TICKET_PATH
    if not path.exists(): return None
    ticket = _read_json(path)
    if ticket.get("status") != "COMPLETE":
        raise ValueError("Protected evaluation ticket was consumed or failed; automatic retry is prohibited")
    relative = campaign_path.relative_to(root).as_posix()
    if ticket["binding"]["campaign_summary_path"] != relative:
        raise ValueError("The project final evaluation was already used by another campaign")
    expected = next(r["sha256"] for r in ticket["binding"]["bound_artifacts"] if r["path"] == relative)
    if sha256_file(campaign_path) != expected: raise ValueError("Previously evaluated campaign summary changed")
    folder = root / "runs/protected_evaluator" / ticket["ticket_id"]
    artifact_path = folder / "artifact_manifest.json"
    if sha256_file(artifact_path) != ticket["artifact_manifest_sha256"]:
        raise ValueError("Completed final artifact inventory changed")
    artifact_manifest = _read_json(artifact_path)
    for record in artifact_manifest["files"]: _checked_artifact(root, record, folder.relative_to(root).as_posix())
    report = _read_json(folder / "summary.json")
    if report.get("status") != "PROTECTED_FINAL_COMPLETE": raise ValueError("Final report is incomplete")
    return {"path": str(folder), "report_path": str(folder / "summary.json"), **report}


def _load_final(root: Path, policy: dict, snapshot: dict) -> tuple[dict, dict, set, dict]:
    verify_manifest(root, snapshot)
    lower, upper = policy["protected_final"]
    tables = {}
    for path in final_paths(root):
        rows = pq.read_table(path).to_pylist()
        if any(not lower <= row["climate_date"] <= upper for row in rows): raise ValueError("Final table leaks outside its registered partition")
        key = path.stem
        if key != "climate_versions":
            identities = [(r["ticker"], r["end_period_ts"]) if key == "candles" else r["climate_date"] if key in ("forecasts", "climate") else r["ticker"] for r in rows]
            if len(identities) != len(set(identities)): raise ValueError("Duplicate final table identity")
        tables[("selection", key)] = rows  # Reuse a reviewed replay interface, never a discovery process.
    verify_manifest(root, snapshot)
    features = tables[("selection", "forecasts")]
    expected_days = (date.fromisoformat(upper) - date.fromisoformat(lower)).days + 1
    fraction = Decimal(len(features)) / expected_days
    if fraction < Decimal(policy["minimum_forecast_coverage_fraction"]):
        raise ValueError("Protected weather coverage is below the preregistered minimum")
    for row in features:
        day = row["climate_date"]
        assumed = utc(day + "T00:00:00Z") + timedelta(hours=policy["availability_delay_hours"])
        if not assumed <= utc(row["available_at"]) <= utc(day + f"T{policy['decision_hour_utc']:02d}:00:00Z"):
            raise ValueError("Final forecast violates its information cutoff")
    labels = {r["climate_date"]: r for r in tables[("selection", "climate")]}
    feature_days = {r["climate_date"] for r in features}
    eligible = set(labels) & feature_days
    excluded = {}
    def exclude(day, reason): excluded.setdefault(day, set()).add(reason)
    outcomes, reconciled = tables[("selection", "outcomes")], tables[("selection", "reconciliation")]
    for row in outcomes:
        if row.get("mapping_consistent") is not True: exclude(row["climate_date"], "unverified_settlement_mapping")
    for row in reconciled:
        if row.get("settlement_label_reconciled") is not True: exclude(row["climate_date"], "NWS_label_not_reconciled_with_settlement")
    good = {r["ticker"] for r in reconciled if r.get("settlement_label_reconciled") is True}
    for row in tables[("selection", "contracts")]:
        day = row["climate_date"]
        if row["ticker"] not in good: exclude(day, "missing_reconciled_contract")
        if day not in labels: exclude(day, "missing_NWS_label")
        if day not in feature_days: exclude(day, "missing_or_quarantined_forecast")
    eligible -= set(excluded)
    settlement_checks = _Checks()
    _audit_settlement_mapping({name: rows for (_, name), rows in tables.items()}, settlement_checks)
    if settlement_checks.failed:
        raise ValueError("Protected settlement reconciliation failed independent source/timestamp validation")
    lineage = {"eligible_days": sorted(eligible), "excluded": [{"climate_date": d, "reasons": sorted(r)} for d, r in sorted(excluded.items())],
               "weather_coverage": {"available_days": len(features), "expected_days": expected_days, "fraction": str(fraction)},
               "independent_settlement_validation": {"status": "PASS", "checks": settlement_checks.count},
               "limitation": "Results conditional on retrospective settlement-reconciliation eligibility"}
    return tables, labels, eligible, lineage


def _verify_scores(predictions, contract_predictions, daywise, tables, labels, eligible, scores):
    contracts = {r["ticker"]: r for r in tables[("selection", "contracts")] if r["climate_date"] in eligible}
    outcomes = {r["ticker"]: r["yes_outcome"] for r in tables[("selection", "outcomes")]}
    errors, crps_values, differences = {}, [], []
    for row in contract_predictions:
        contract = contracts[row["ticker"]]
        day = contract["climate_date"]
        probability = _reference_probability(*predictions[day], contract)
        _close(row["yes_probability"], probability, "final independent contract probability")
        if row["yes_outcome"] != outcomes[row["ticker"]]: raise ValueError("Final scoring outcome differs")
        error = (probability - outcomes[row["ticker"]]) ** 2
        _close(row["brier_contribution"], error, "final independent Brier")
        errors.setdefault(day, []).append(error)
    if len(contract_predictions) != len([ticker for ticker in contracts if ticker in outcomes]): raise ValueError("Final probability coverage differs")
    if {r["climate_date"] for r in daywise} != eligible or len(daywise) != len(eligible): raise ValueError("Final score dates differ")
    for row in daywise:
        day = row["climate_date"]
        mean, sd = predictions[day]
        z = (labels[day]["tmax_f"] - mean) / sd
        crps = sd * (z * erf(z / sqrt(2)) + 2 * exp(-z * z / 2) / sqrt(2 * pi) - 1 / sqrt(pi))
        _close(row["crps_f"], crps, "final independent CRPS")
        crps_values.append(crps)
        differences.append(mean - labels[day]["tmax_f"])
    for key, values in (("gaussian_crps_f", crps_values), ("mae_f", [abs(d) for d in differences]),
                        ("bias_f", differences), ("brier", [e for group in errors.values() for e in group])):
        if values: _close(scores[key], fsum(values) / len(values), "final aggregate " + key)
        elif scores[key] is not None: raise ValueError("Final score exists without observations")
    return {"independent_frozen_predictions_and_scores_verified": True, "forecast_refit_performed": False}


def run_final_evaluation(root: Path, campaign_summary_path: Path, *, offline_checks: dict | None = None) -> dict:
    root = Path(root).resolve()
    campaign_path = _within(root, campaign_summary_path, "runs/campaigns")
    previous = _existing_result(root, campaign_path)
    if previous is not None: return previous
    context = _preflight(root, campaign_path)
    if not context["qualified"]:
        return {"status": "NOT_RUN_NO_QUALIFIED_CANDIDATE", "campaign_id": context["campaign"]["campaign_id"],
                "protected_final_evaluated": False, "ticket_consumed": False,
                "scientific_conclusion": "No candidate passed the fixed development screen"}
    ticket = _consume_ticket(root, context["binding"])
    folder = root / "runs/protected_evaluator" / ticket["ticket_id"]
    try:
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / "offline_checks.json", offline_checks or {"scope": "Direct API invocation; process guard not asserted"})
        policy = context["policy"]
        snapshot = freeze_manifest(root, final_paths(root), {"evaluation": policy, "one_use_ticket_id": ticket["ticket_id"]}, folder / "final_dataset_manifest.json")
        tables, labels, eligible, lineage = _load_final(root, policy, snapshot)
        write_json(folder / "eligibility.json", lineage)
        write_json(folder / "frozen_champion_model.json", context["model"])
        if context["research_plan"] is not None:
            write_json(folder / "frozen_research_plan.json", context["research_plan"].to_dict())
        write_json(folder / "frozen_baseline_models.json", context["baseline_models"])
        features = tables[("selection", "forecasts")]
        contracts, outcomes = tables[("selection", "contracts")], tables[("selection", "outcomes")]
        all_models = {"champion": context["model"], **context["baseline_models"]}
        reports = {}
        assumptions = list(product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"]))
        if len(assumptions) != 12 or len(set(assumptions)) != 12: raise ValueError("Exactly twelve registered sensitivities are required")
        for name, model in all_models.items():
            model_hash = canonical_hash(model)
            replay_policy = context["champion_policy"] if name == "champion" else policy
            execution_version = context["execution_model_version"] if name == "champion" else model_hash
            predictions, independent = {}, {}
            for row in features:
                day = row["climate_date"]
                if name == "champion":
                    predictions[day] = predict_candidate(model, row)
                    independent[day] = _reference_prediction(_spec(model["spec"]), model, row)
                else:
                    predictions[day] = predict(model, row)
                    independent[day] = independent_prediction(model, row)
                for actual, expected in zip(predictions[day], independent[day]): _close(actual, expected, "final frozen-model prediction")
            destination = folder / name
            write_json(destination / "predictions.json", [{"climate_date": d, "mean_f": p[0], "sd_f": p[1], "model_sha256": model_hash} for d, p in sorted(predictions.items())])
            inputs = {"predictions": predictions, "contracts": contracts, "candles": tables[("selection", "candles")],
                      "outcomes": outcomes, "eligible": eligible, "policy": replay_policy,
                      "model_version": execution_version, "dataset_version": snapshot["version"]}
            primary = replay_model(**inputs)
            primary["summary"].pop("selection_return_is_not_out_of_sample_final_evidence", None)
            primary["summary"]["evaluation_partition"] = "protected_final"
            verification = verify_ledger_arithmetic(primary, outcomes, replay_policy)
            verification.update(_verify_primary_selection(tables, replay_policy, independent, primary["decisions"], primary["ledger"], primary["summary"], execution_version))
            # The audit interfaces read persisted JSON values, while replay returns
            # Decimal/datetime objects. Serialize each primary before auditing it.
            write_json(destination / "primary_decisions.json", primary["decisions"])
            write_json(destination / "primary_ledger.json", primary["ledger"])
            verification["primary_decisions_verified"] = _verify_decisions(_read_json(destination / "primary_decisions.json"), _read_json(destination / "primary_ledger.json"),
                contracts, tables[("selection", "candles")], independent, eligible, replay_policy, execution_version, snapshot["version"])
            scores = probability_scores(predictions, contracts, outcomes, labels, eligible)
            daywise, contract_predictions = _daywise(predictions, contracts, outcomes, labels, eligible)
            verification.update(_verify_scores(independent, contract_predictions, daywise, tables, labels, eligible, scores))
            write_json(destination / "daywise_scores.json", daywise)
            write_json(destination / "contract_probabilities.json", contract_predictions)
            sensitivities = []
            for slip, rate, quantity in assumptions:
                scenario = {"slippage": slip, "fee_rate": rate, "quantity": quantity}
                scenario_id = canonical_hash(scenario)[:12]
                replay = replay_model(**{**inputs, "model_version": model_hash + ":" + scenario_id}, **scenario)
                replay["summary"].pop("selection_return_is_not_out_of_sample_final_evidence", None)
                replay["summary"]["evaluation_partition"] = "protected_final"
                checked = verify_ledger_arithmetic(replay, outcomes, replay_policy, fee_rate=rate)
                write_json(destination / "sensitivity" / (scenario_id + "_ledger.json"), replay["ledger"])
                write_json(destination / "sensitivity" / (scenario_id + "_decisions.json"), replay["decisions"])
                sensitivities.append({"scenario_id": scenario_id, **scenario, "summary": replay["summary"], "verification": checked})
            verification.update(status="PASS", independent_verification_complete=False,
                                unverified=["Sensitivity selection completeness", "Bootstrap numerical implementation", "Real fills and historical public availability"])
            reports[name] = {"model_sha256": model_hash, "execution_model_version": execution_version,
                             "policy_sha256": canonical_hash(replay_policy), "forecast_scores": scores,
                             "historical_assumed_fill": primary["summary"], "predeclared_cost_sensitivity": sensitivities,
                             "verification": verification, "weather_day_bootstrap": primary["summary"]["bootstrap"]}
            write_json(destination / "summary.json", reports[name])
        verify_manifest(root, snapshot)
        verify_manifest(root, context["manifest"])
        for item in ticket["binding"]["bound_artifacts"]: _checked_artifact(root, item, ".")
        comparisons = [{"model": name, **{key: value["historical_assumed_fill"][key] for key in
                        ("trade_count", "total_net_profit", "total_entry_outlay", "capital_weighted_return", "mean_trade_return")},
                        "crps_f": value["forecast_scores"]["gaussian_crps_f"], "brier": value["forecast_scores"]["brier"]} for name, value in reports.items()]
        champion = reports["champion"]["historical_assumed_fill"]
        enough = len(eligible) >= policy["minimum_final_opportunity_days_for_inference"] and champion["trade_count"] >= policy["minimum_selected_weather_days_for_inference"]
        report = {"status": "PROTECTED_FINAL_COMPLETE", "ticket_id": ticket["ticket_id"],
                  "campaign_id": context["campaign"]["campaign_id"], "binding": ticket["binding"],
                  "final_dataset_version": snapshot["version"], "protected_final_evaluated": True,
                  "forecast_refit_performed": False, "agents_dispatched": 0,
                  "independent_verification_passed": True,
                  "independent_verification_scope": "Frozen model predictions/scores, primary opportunity selection, and all scenario selected-ledger arithmetic; excludes sensitivity selection and bootstrap implementation",
                  "eligible_final_days": len(eligible), "inference_sample_sufficient": enough,
                  "scientific_conclusion": "Historical assumed-fill results available; assess uncertainty and assumptions" if enough else "INSUFFICIENT_FINAL_SAMPLE",
                  "reference_baseline": context["reference_baseline"], "models": reports, "comparisons": comparisons,
                  "limitations": ["All returns are simulated historical outcomes, never actual account gains",
                      "Hourly bid/ask summaries cannot establish executable depth or real fills",
                      "Historical fees and public forecast availability remain assumptions",
                      "Weather-day bootstrap includes no-trade days but does not capture multi-day weather dependence",
                      "One year of Kalshi data and retrospective reconciliation limit generality",
                      "A 10% expected-return screen is not a guarantee of 10% realized gains"]}
        write_json(folder / "summary.json", report)
        write_json(folder / "artifact_manifest.json", {"ticket_id": ticket["ticket_id"], "files": inventory(root, list(folder.rglob("*.json")))})
        ticket.update(status="COMPLETE", completed_at_utc=datetime.now(timezone.utc).isoformat(),
                      artifact_manifest_sha256=sha256_file(folder / "artifact_manifest.json"))
        write_json(root / TICKET_PATH, ticket)
        return {"path": str(folder), "report_path": str(folder / "summary.json"), **report}
    except BaseException as error:
        ticket.update(status="FAILED", error_type=type(error).__name__, reason=str(error),
                      failed_at_utc=datetime.now(timezone.utc).isoformat())
        write_json(root / TICKET_PATH, ticket)
        raise


def main(argv: list[str] | None = None) -> int:
    import argparse
    import socket
    import subprocess
    import sys
    from .offline import install_guard
    parser = argparse.ArgumentParser(description="One-use protected offline evaluation; no model fitting or agent dispatch")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--campaign-summary", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        # Approved NumPy/PyArrow libraries were loaded above, before the guard.
        denied = (root / "data/raw", root / "data/normalized/weather")
        install_guard(denied)
        checks = {}
        for name, operation in (("python_socket_denied", lambda: socket.socket()),
                                ("raw_file_open_denied", lambda: (denied[0] / "not_a_source").read_bytes()),
                                ("child_process_denied", lambda: subprocess.run([sys.executable, "-c", "pass"]))):
            try: operation()
            except PermissionError: checks[name] = True
            else: raise RuntimeError("Protected evaluator offline check failed: " + name)
        checks["scope"] = "Reviewed final-evaluator Python process; protected final tables explicitly allowed, no discovery agents"
        report = run_final_evaluation(root, args.campaign_summary, offline_checks=checks)
        print(json.dumps(report, default=str, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__, "reason": str(error)}, allow_nan=False))
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
