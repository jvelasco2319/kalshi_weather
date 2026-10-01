"""Only generated synthetic tables; never opens the workspace's final data."""
from datetime import date, timedelta
from itertools import product
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import klax_lab.final_evaluation as final
import klax_lab.experiments as experiments
from klax_lab.baseline import development_paths, forecast_artifacts, probability_scores, replay_model, utc
from klax_lab.models import BASELINE_DEFINITIONS, fit_baselines, predict
from klax_lab.provenance import canonical_hash, freeze_manifest, inventory, sha256_file, write_json
from klax_lab.readiness import code_fingerprint
from test_experiments import build_dataset, SPEC


@pytest.fixture
def qualified(tmp_path_factory):
    root, old_manifest = build_dataset(tmp_path_factory.mktemp("f"), training_days=366, stride=1)
    policy = json.loads((root / "configs/evaluation.json").read_text())
    source = Path(final.__file__).parent
    for path in source.glob("*.py"): shutil.copyfile(path, root / "src/klax_lab" / path.name)
    paths = development_paths(root) + [root / "configs/evaluation.json", *sorted((root / "src/klax_lab").glob("*.py"))]
    manifest_policy = {"evaluation": policy, "baseline_definitions": BASELINE_DEFINITIONS}
    version = canonical_hash({"files": inventory(root, paths), "policy": manifest_policy})
    manifest_path = root / "data/manifests" / ("development-baselines-" + version + ".json")
    freeze_manifest(root, paths, manifest_policy, manifest_path)
    old_version = json.loads(old_manifest.read_text())["version"]
    old_baseline = root / "runs" / ("baselines-" + old_version[:16])
    baseline = root / "runs" / ("baselines-" + version[:16])
    baseline.mkdir()
    shutil.copyfile(old_baseline / "selection_eligibility.json", baseline / "selection_eligibility.json")
    training = pq.read_table(root / "data/normalized/weather_training/features/forecasts.parquet").to_pylist()
    labels = {r["climate_date"]: r for r in pq.read_table(root / "data/normalized/weather_training/labels/climate.parquet").to_pylist()}
    models = fit_baselines([{**r, "tmax_f": labels[r["climate_date"]]["tmax_f"]} for r in training], 100)
    write_json(baseline / "fitted_models.json", models)
    features = pq.read_table(root / "data/normalized/selection/features/forecasts.parquet").to_pylist()
    contracts = pq.read_table(root / "data/normalized/selection/features/contracts.parquet").to_pylist()
    outcomes = pq.read_table(root / "data/normalized/selection/labels/outcomes.parquet").to_pylist()
    labels = {r["climate_date"]: r for r in pq.read_table(root / "data/normalized/selection/labels/climate.parquet").to_pylist()}
    scores = {name: {"forecast_scores": probability_scores({r["climate_date"]: predict(model, r) for r in features}, contracts,
              outcomes, labels, {"2025-01-05", "2025-01-06"})} for name, model in models.items()}
    candles = pq.read_table(root / "data/normalized/selection/features/candles.parquet").to_pylist()
    for name, model in models.items():
        replay = replay_model(predictions={r["climate_date"]: predict(model, r) for r in features},
              contracts=contracts, candles=candles, outcomes=outcomes, eligible={"2025-01-05", "2025-01-06"}, policy=policy,
              model_version=canonical_hash(model), dataset_version=version)
        scores[name]["historical_assumed_fill"] = replay["summary"]
        write_json(baseline / (name + "_decisions.json"), replay["decisions"])
        write_json(baseline / (name + "_ledger.json"), replay["ledger"])
        forecasts = forecast_artifacts({r["climate_date"]: predict(model, r) for r in features}, features, contracts, outcomes, labels,
                                       {"2025-01-05", "2025-01-06"}, canonical_hash(model), version)
        for kind, rows in forecasts.items(): write_json(baseline / (name + "_" + kind + ".json"), rows)
        scores[name]["forecast_artifacts"] = inventory(baseline, [baseline / (name + "_" + kind + ".json") for kind in forecasts])
        sensitivities = []
        for slip, rate, quantity in product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"]):
            assumptions = {"slippage": slip, "fee_rate": rate, "quantity": quantity}
            scenario_id = canonical_hash(assumptions)[:12]
            scenario = replay_model(predictions={r["climate_date"]: predict(model, r) for r in features},
                contracts=contracts, candles=candles, outcomes=outcomes, eligible={"2025-01-05", "2025-01-06"}, policy=policy,
                model_version=canonical_hash(model) + ":" + scenario_id, dataset_version=version, **assumptions)
            sensitivities.append({"scenario_id": scenario_id, **assumptions, "summary": scenario["summary"]})
        scores[name]["predeclared_cost_sensitivity"] = sensitivities
    write_json(baseline / "summary.json", {"status": "DEVELOPMENT_BASELINES_COMPLETE", "dataset_version": version,
              "protected_final_evaluated": False, "models": scores, "training_days": len(training),
              "eligible_selection_days": 2, "excluded_selection_days": 175})
    write_json(baseline / "independent_replication.json", {"status": "PASS", "synthetic": True, "saved_prediction_artifacts_verified": True,
             "artifact_hashes": inventory(root, [baseline / "fitted_models.json", baseline / "selection_eligibility.json", *baseline.glob("*_decisions.json"), *baseline.glob("*_ledger.json"),
                  *baseline.glob("*_predictions.json"), *baseline.glob("*_contract_probabilities.json"), *baseline.glob("*_daywise_scores.json")])})
    campaign = root / "runs/campaigns/c"
    result = experiments.run_candidate_experiment(root, manifest_path, SPEC, output_root=campaign / "e")
    replica = experiments.verify_candidate_experiment(root, manifest_path, Path(result["path"]))
    replica_path = campaign / "replica.json"
    write_json(replica_path, replica)
    pilot = {"development_gate": {"minimum_relative_CRPS_improvement": -1., "maximum_Brier_degradation": 1.,
             "minimum_simulated_trades": 2, "minimum_capital_weighted_return": 0., "stress_slippage": "0.02",
             "stress_fee_rate": "0.10", "stress_quantity": 1, "minimum_stress_return": 0.}}
    write_json(root / "configs/pilot.json", pilot)
    ready = {"status": "READY_FOR_OFFLINE_CAMPAIGN", "code_sha256": code_fingerprint(root),
             "dataset_sha256": version,
             "evaluation_policy_sha256": sha256_file(root / "configs/evaluation.json"),
             "pilot_policy_sha256": sha256_file(root / "configs/pilot.json"),
             "development_manifest_path": manifest_path.relative_to(root).as_posix(), "development_manifest_sha256": sha256_file(manifest_path),
             "baseline_run_path": baseline.relative_to(root).as_posix(),
             "baseline_artifacts": inventory(root, [baseline / "summary.json", baseline / "independent_replication.json"])}
    readiness_path = root / "data/manifests/readiness.json"
    write_json(readiness_path, ready)
    campaign_path = campaign / "summary.json"
    champion = {"candidate_id": result["candidate_id"], "spec": SPEC, "experiment_path": result["path"],
             "experiment_id": result["experiment_id"], "replication_path": str(replica_path), "gate": {"passed": True, "reasons": []},
             "critic": {"action": "propose"}, "forecast_scores": result["forecast_scores"],
             "historical_assumed_fill": result["historical_assumed_fill"], "evidence_ids": ["implementation", "criticism", "replication"]}
    write_json(campaign_path, {"status": "RESEARCH_CYCLE_COMPLETE", "campaign_id": "synthetic", "protected_final_evaluated": False,
             "champion": champion, "candidate_count": 1, "local_model_calls": 2,
             "readiness_sha256": sha256_file(readiness_path), "pilot_policy_sha256": ready["pilot_policy_sha256"],
             "reference_baseline": min(scores, key=lambda name: (scores[name]["forecast_scores"]["gaussian_crps_f"], name))})
    write_json(campaign / "candidate_register.json", [champion])
    write_json(campaign / "registered-result.json", result)
    write_json(campaign / "critic.json", {"response": champion["critic"]})
    roles = [("implementation", "implementer", campaign / "registered-result.json"),
             ("criticism", "critic", campaign / "critic.json"), ("replication", "replicator", replica_path)]
    write_json(campaign / "ledger.json", {"campaign_id": "synthetic",
             "tasks": [{"id": key, "state": "SUCCEEDED", "spec": {"role": role}} for key, role, path in roles],
             "experiments": [{"id": result["experiment_id"], "record": {"experiment_id": result["experiment_id"],
                 "dataset_sha256": version, "code_sha256": ready["code_sha256"], "parameters": SPEC}}],
             "evidence": [{"id": key, "task_id": key, "record": {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}} for key, role, path in roles],
             "attempts": [{"task_id": key, "state": "SUCCEEDED", "actual": {"tokens": 100 if role == "critic" else 0, "compute_seconds": 1,
                 "paid_micros": 0, "experiments": int(role == "implementer")}} for key, role, path in roles],
             "messages": [{"kind": "REQUEST_REPLICATION"}], "decisions": [{"kind": "GATE"}],
             "events": [{"kind": "CREATED", "at": 1000.}, {"kind": "RESEARCH_CYCLE_COMPLETE", "at": 1060.}]})
    write_json(root / "data/manifests/offline_campaign_ticket.json", {"ticket_version": "offline-campaign-ticket-v1", "status": "COMPLETED",
             "campaign_id": "synthetic", "campaign_path": campaign.relative_to(root).as_posix(),
             "binding": {**ready, "readiness_sha256": sha256_file(readiness_path)},
             "artifacts": inventory(root, [campaign_path, campaign / "ledger.json", campaign / "candidate_register.json"])})
    final_rows = {name: [] for _, names in final.FINAL_NAMES for name in names}
    start, end = map(date.fromisoformat, policy["protected_final"])
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        following = (start + timedelta(days=offset + 1)).isoformat()
        ticker = "SYNTH-" + day
        final_rows["forecasts"].append({"climate_date": day, "gfs": 61., "nbm": 62., "available_at": day + "T06:00:00Z"})
        final_rows["climate"].append({"climate_date": day, "tmax_f": 64, "available_at": following + "T10:00:00Z"})
        final_rows["climate_versions"].append({"climate_date": day, "tmax_f": 64, "issued_at": following + "T10:00:00Z",
              "available_at": following + "T10:00:00Z", "source_sha256": "synthetic-climate"})
        final_rows["contracts"].append({"climate_date": day, "ticker": ticker, "lower_integer_f": None, "upper_integer_f": 65,
              "open_time": day + "T00:00:00Z", "close_time": following + "T08:00:00Z"})
        final_rows["candles"].append({"climate_date": day, "ticker": ticker, "end_period_ts": int(utc(day + "T14:00:00Z").timestamp()),
              "yes_ask_close": ".30", "yes_bid_close": ".20", "source_sha256": "synthetic"})
        final_rows["outcomes"].append({"climate_date": day, "ticker": ticker, "yes_outcome": 1,
              "event_ticker": "EVENT-" + day, "expiration_value_f": 64., "settlement_temperature_f": 64.,
              "mapping_consistent": True, "settlement_time": following + "T12:00:00Z"})
        final_rows["reconciliation"].append({"climate_date": day, "ticker": ticker, "settlement_label_reconciled": True,
              "climate_source_sha256": "synthetic-climate"})
    for path in final.final_paths(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(final_rows[path.stem]), path)
    return root, campaign_path


def test_no_qualifier_never_consumes_ticket_or_reads_final(tmp_path, monkeypatch):
    campaign = tmp_path / "runs/campaigns/c/summary.json"
    write_json(campaign, {"status": "RESEARCH_CYCLE_COMPLETE", "campaign_id": "empty", "champion": None, "protected_final_evaluated": False})
    def forbidden(*_a, **_k): raise AssertionError("final access attempted without qualifier")
    monkeypatch.setattr(final, "final_paths", forbidden)
    report = final.run_final_evaluation(tmp_path, campaign)
    assert report["status"] == "NOT_RUN_NO_QUALIFIED_CANDIDATE"
    assert not (tmp_path / final.TICKET_PATH).exists()


def test_complete_once_no_refit_and_idempotent_retrieval(qualified, monkeypatch):
    root, campaign = qualified
    original = final.freeze_manifest
    observed = []
    def before_hash(*args, **kwargs):
        ticket = json.loads((root / final.TICKET_PATH).read_text())
        observed.append(ticket["status"])
        return original(*args, **kwargs)
    monkeypatch.setattr(final, "freeze_manifest", before_hash)
    def forbidden(*_a, **_k): raise AssertionError("Final evaluation attempted a calibration refit")
    monkeypatch.setattr(experiments, "fit_candidate", forbidden)
    monkeypatch.setattr(experiments, "_reference_fit", forbidden)
    report = final.run_final_evaluation(root, campaign)
    assert observed == ["CONSUMED"]
    assert report["status"] == "PROTECTED_FINAL_COMPLETE"
    assert report["independent_verification_passed"]
    assert len(report["models"]) == 6 and report["eligible_final_days"] == 184
    assert report["forecast_refit_performed"] is False and report["agents_dispatched"] == 0
    assert all(r["verification"]["primary_candidate_selection_verified"] for r in report["models"].values())
    assert all(len(r["predeclared_cost_sensitivity"]) == 12 for r in report["models"].values())
    assert Path(report["report_path"]).is_file()
    monkeypatch.setattr(final, "_load_final", forbidden)
    assert final.run_final_evaluation(root, campaign)["ticket_id"] == report["ticket_id"]


def test_failed_ticket_prevents_new_attempt_after_coverage_failure(qualified):
    root, campaign = qualified
    forecast = root / "data/normalized/protected_final/features/forecasts.parquet"
    original = forecast.read_bytes()
    pq.write_table(pa.Table.from_pylist(pq.read_table(forecast).to_pylist()[:5]), forecast)
    with pytest.raises(ValueError, match="coverage"): final.run_final_evaluation(root, campaign)
    assert json.loads((root / final.TICKET_PATH).read_text())["status"] == "FAILED"
    forecast.write_bytes(original)
    with pytest.raises(ValueError, match="automatic retry"): final.run_final_evaluation(root, campaign)


def test_rejects_stale_champion_replica_before_consuming_ticket(qualified):
    root, campaign = qualified
    report = json.loads(campaign.read_text())
    replica_path = Path(report["champion"]["replication_path"])
    replica = json.loads(replica_path.read_text())
    replica["primary_candidate_selection_verified"] = False
    write_json(replica_path, replica)
    with pytest.raises(ValueError, match="verification"): final.run_final_evaluation(root, campaign)
    assert not (root / final.TICKET_PATH).exists()


def test_exclusive_ticket_cannot_be_consumed_twice(tmp_path):
    first = final._consume_ticket(tmp_path, {"candidate_id": "synthetic-a"})
    with pytest.raises(FileExistsError): final._consume_ticket(tmp_path, {"candidate_id": "synthetic-b"})
    assert json.loads((tmp_path / final.TICKET_PATH).read_text())["ticket_id"] == first["ticket_id"]


def test_rejects_inconsistent_final_settlement_without_retry(qualified):
    root, campaign = qualified
    path = root / "data/normalized/protected_final/labels/outcomes.parquet"
    rows = pq.read_table(path).to_pylist()
    rows[0]["yes_outcome"] = 0
    pq.write_table(pa.Table.from_pylist(rows), path)
    with pytest.raises(ValueError, match="settlement reconciliation"):
        final.run_final_evaluation(root, campaign)
    assert json.loads((root / final.TICKET_PATH).read_text())["status"] == "FAILED"


def test_fresh_final_cli_guard(qualified):
    root, campaign = qualified
    env = {**os.environ, "PYTHONPATH": str(Path(final.__file__).parents[1])}
    process = subprocess.run([sys.executable, "-m", "klax_lab.final_evaluation", "--root", str(root),
                              "--campaign-summary", str(campaign)], env=env, capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr
    report = json.loads(process.stdout)
    checks = json.loads((Path(report["path"]) / "offline_checks.json").read_text())
    assert checks["python_socket_denied"] and checks["child_process_denied"] and checks["raw_file_open_denied"]
    assert report["independent_verification_passed"]
