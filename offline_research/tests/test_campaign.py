"""Synthetic coordinator tests; no model inference or real historical analysis."""
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from klax_lab.campaign import Campaign, assess_development_gate, candidate_options
from klax_lab.candidates import CandidateSpec
from klax_lab.local_backend import PROTOCOL, WorkerLimits
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.readiness import code_fingerprint


def reports():
    scores = {"gaussian_crps_f": .9, "brier": .1, "mae_f": 1.0}
    return {"forecast_scores": scores,
        "historical_assumed_fill": {"trade_count": 50, "capital_weighted_return": ".2", "total_net_profit": "5", "total_entry_outlay": "25", "mean_trade_return": ".2"},
        "predeclared_cost_sensitivity": [{"slippage": "0.02", "fee_rate": "0.10", "quantity": 1,
            "summary": {"capital_weighted_return": ".1"}}]}


class FakeWorker:
    """Only supplies invented protocol results in an ephemeral test directory."""
    def __init__(self, spec, limits):
        self.limits = limits

    def dispatch_one(self, controller, campaign_id, worker_id, task_id, packet):
        claim = controller.claim_task(campaign_id, worker_id, task_id=task_id)
        options = packet["candidate_options"]
        response = {"protocol": PROTOCOL, "task_id": task_id, "evidence_ids": [],
                    "action": "propose" if options else "abstain", "candidate": options[0] if options else None,
                    "rationale": "Synthetic fixture opinion only", "limitations": ["Synthetic fixture"]}
        destination = controller.artifact_root / claim["task"]["allowed_outputs"][0] / "response.json"
        write_json(destination, response)
        eid = "proposal-" + task_id
        controller.complete_task(task_id, claim["lease_token"], {"status": "completed", "claims": [],
            "evidence": [{"evidence_id": eid, "path": destination.relative_to(controller.artifact_root).as_posix(),
                "sha256": sha256_file(destination), "kind": "synthetic_test_only", "experiment_id": None}],
            "experiments": [], "limitations": ["Synthetic fixture"], "next_steps": []},
            {"tokens": self.limits.context_tokens, "compute_seconds": 1, "paid_micros": 0, "experiments": 0})
        return {"response": response}


def setup_fixture(root):
    source = Path(__file__).resolve().parents[1]
    pilot = json.loads((source / "configs/pilot.json").read_text())
    write_json(root / "configs/pilot.json", pilot)
    write_json(root / "configs/evaluation.json", {"synthetic": True})
    manifest = {"files": [], "policy": {"synthetic": True}}
    manifest["version"] = canonical_hash(manifest)
    manifest_path = root / "data/manifests/synthetic.json"
    write_json(manifest_path, manifest)
    baseline_path = root / "runs/baseline/summary.json"
    baseline = reports()
    baseline["forecast_scores"]["gaussian_crps_f"] = 1.0
    write_json(baseline_path, {"dataset_version": manifest["version"], "models": {"synthetic_baseline": baseline}, "training_days": 366, "eligible_selection_days": 177, "excluded_selection_days": 0})
    ready = {"status": "READY_FOR_OFFLINE_CAMPAIGN", "offline_verified": True, "holdout_access_denied": True,
        "development_baselines_verified": True, "development_manifest_path": manifest_path.relative_to(root).as_posix(),
        "development_manifest_sha256": sha256_file(manifest_path), "dataset_sha256": manifest["version"],
        "code_sha256": code_fingerprint(root), "evaluation_policy_sha256": sha256_file(root / "configs/evaluation.json"),
        "pilot_policy_sha256": sha256_file(root / "configs/pilot.json"), "baseline_run_path": "runs/baseline",
        "baseline_artifacts": [{"path": baseline_path.relative_to(root).as_posix(), "sha256": sha256_file(baseline_path)}],
        "local_worker": {"runtime_spec_path": "synthetic-not-a-runtime.json"}}
    path = root / "data/manifests/readiness.json"
    write_json(path, ready)
    return path, pilot


def fake_child(self, command, log, timeout=1200):
    def arg(name):
        return command[command.index(name) + 1]
    if command[0] == "verify":
        result = {"status": "PASS", "primary_candidate_selection_verified": True, "synthetic_test_only": True}
        write_json(Path(arg("--output")), result)
    else:
        spec = json.loads(Path(arg("--spec-file")).read_text())
        result = {"path": arg("--output-root"), "experiment_id": CandidateSpec.from_dict(spec).identity,
                  "status": "SYNTHETIC_TEST_ONLY", **reports()}
    return result, 1.0


def test_closed_options_unique_and_valid():
    for track in (1, 2, 3):
        options = candidate_options(track)
        assert 1 <= len(options) <= 12
        assert len({CandidateSpec.from_dict(spec).identity for spec in options}) == len(options)


def test_gate_missing_costs_and_nonfinite_cannot_pass():
    with TemporaryDirectory() as directory:
        _, pilot = setup_fixture(Path(directory))
        gate = pilot["development_gate"]
        baseline = reports()
        baseline["forecast_scores"]["gaussian_crps_f"] = 1.0
        result = reports()
        assert assess_development_gate(result, baseline, {"status": "PASS", "primary_candidate_selection_verified": True}, {"action": "propose"}, gate)["passed"]
        for bad in (None, "garbage", "NaN", True):
            result["historical_assumed_fill"]["capital_weighted_return"] = bad
            assert not assess_development_gate(result, baseline, {"status": "PASS", "primary_candidate_selection_verified": True}, {"action": "propose"}, gate)["passed"]
        result = reports()
        result["predeclared_cost_sensitivity"] = []
        assert not assess_development_gate(result, baseline, {"status": "PASS", "primary_candidate_selection_verified": True}, {"action": "propose"}, gate)["passed"]


def test_complete_synthetic_coordinator_cycle_preserves_budgets_and_independence():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        readiness, pilot = setup_fixture(root)
        with patch("klax_lab.campaign.LocalTextWorker", FakeWorker), patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(Campaign, "fixed_child", fake_child):
            campaign = Campaign(root, readiness)
            report = campaign.run()
        assert report["status"] == "RESEARCH_CYCLE_COMPLETE"
        assert report["candidate_count"] == 10
        assert report["local_model_calls"] == 22
        assert report["protected_final_evaluated"] is False
        assert report["goal2_complete"] is False
        ledger = json.loads((Path(report["path"]) / "ledger.json").read_text())
        assert len(ledger["experiments"]) == 10
        assert len(ledger["messages"]) >= 10
        assert all(row["state"] == "CANDIDATE" for row in ledger["hypotheses"])
        assert all(row["state"] == "SUCCEEDED" for row in ledger["tasks"])
        assert sum(row["actual"]["tokens"] for row in ledger["attempts"]) <= pilot["local_token_budget"]
        assert sum(row["actual"]["paid_micros"] for row in ledger["attempts"]) == 0
        combined = [row for row in ledger["hypotheses"] if row["spec"]["parent_ids"]]
        assert len(combined) == 4
        assert all(len(row["spec"]["parent_ids"]) == 2 for row in combined)
        assert any("Challenger assignment" in row["spec"]["question"] for row in ledger["tasks"])
        assert any("Adversarial confirmation" in row["spec"]["question"] for row in ledger["tasks"])
        initial = [row for row in ledger["tasks"] if row["spec"]["role"] == "explorer" and row["spec"]["epoch"] == 1]
        assert all(row["spec"]["source_evidence_ids"] == ["evidence-task-001"] for row in initial)


def test_fake_cycle_cannot_claim_report_completion_without_verified_evidence():
    from klax_lab.reporting import write_research_report
    def negative_child(self, command, log, timeout=1200):
        result, elapsed = fake_child(self, command, log, timeout)
        if command[0] == "run":
            result["historical_assumed_fill"]["capital_weighted_return"] = "-0.1"
        return result, elapsed
    with TemporaryDirectory() as directory:
        root = Path(directory)
        readiness, pilot = setup_fixture(root)
        with patch("klax_lab.campaign.LocalTextWorker", FakeWorker), patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(Campaign, "fixed_child", negative_child):
            report = Campaign(root, readiness).run()
        assert report["champion"] is None
        # Reporting rejects this invented run at its earliest missing evidence
        # boundary, before it even reaches the separate completed-ticket check.
        with pytest.raises(ValueError, match="Baseline replication must be bound"):
            write_research_report(root, Path(report["path"]))
        assert not (root / "data/manifests/project_completion.json").exists()

