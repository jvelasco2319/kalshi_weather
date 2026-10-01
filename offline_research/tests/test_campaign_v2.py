"""Synthetic-only integration coverage for the iterative V2 coordinator."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from klax_lab.campaign_v2 import IterativeCampaign, _counts, compact_research_packet
from klax_lab.local_backend import WorkerLimits, canonical_json
from klax_lab.provenance import canonical_hash, sha256_file, write_json
from klax_lab.readiness import code_fingerprint
from klax_lab.research_plan import seed_plans
from klax_lab.research_protocol import PROTOCOL_V2, build_research_prompt, validate_research_packet


class FakeV2Worker:
    def __init__(self, spec, limits):
        self.limits = limits

    def dispatch_one(self, controller, campaign_id, worker_id, task_id, packet):
        claim = controller.claim_task(campaign_id, worker_id, task_id=task_id)
        selected = packet["seed_plans"][-1] if packet["seed_plans"] else None
        action = "propose" if selected else "abstain"
        response = {"protocol": PROTOCOL_V2, "task_id": task_id, "action": action,
                    "plan": selected, "rationale": "Synthetic typed proposal for coordinator testing only.",
                    "evidence_ids": [row["evidence_id"] for row in packet["evidence"][:1]],
                    "limitations": ["Synthetic fixture"], "requested_checks": []}
        destination = controller.artifact_root / claim["task"]["allowed_outputs"][0] / "response.json"
        write_json(destination, response)
        evidence_id = "proposal-" + task_id
        controller.complete_task(task_id, claim["lease_token"], {
            "status": "completed", "claims": [],
            "evidence": [{"evidence_id": evidence_id,
                          "path": destination.relative_to(controller.artifact_root).as_posix(),
                          "sha256": sha256_file(destination), "kind": "synthetic_test_only",
                          "experiment_id": None}],
            "experiments": [], "limitations": ["Synthetic fixture"], "next_steps": []},
            {"tokens": self.limits.context_tokens, "compute_seconds": 1,
             "paid_micros": 0, "experiments": 0})
        return {"response": response}


def fixture(root: Path):
    source = Path(__file__).resolve().parents[1]
    pilot = json.loads((source / "configs/pilot.json").read_text())
    # Keep this synthetic integration small; readiness separately validates the
    # production 3-5 epoch, 20-40 experiment and 60-120 call registration.
    pilot.update(max_epochs=1, max_experiments=6, max_experiments_per_epoch=6,
                 max_local_model_calls=60, local_token_budget=60 * 16384,
                 proposals_per_colony_epoch_one=1)
    write_json(root / "configs/pilot.json", pilot)
    write_json(root / "configs/evaluation.json", {"synthetic": True})
    manifest = {"files": [], "policy": {"synthetic": True}}
    manifest["version"] = canonical_hash(manifest)
    manifest_path = root / "data/manifests/synthetic.json"
    write_json(manifest_path, manifest)
    baseline_path = root / "runs/baseline/summary.json"
    reference = {"forecast_scores": {"gaussian_crps_f": 1.0, "brier": .1, "mae_f": 1.0},
                 "historical_assumed_fill": {"trade_count": 30, "capital_weighted_return": ".1"}}
    write_json(baseline_path, {"dataset_version": manifest["version"],
        "models": {"synthetic_baseline": reference}, "training_days": 366,
        "eligible_selection_days": 6, "excluded_selection_days": 0})
    ready = {"status": "READY_FOR_OFFLINE_CAMPAIGN", "architecture_version": 2,
        "offline_verified": True, "holdout_access_denied": True,
        "development_baselines_verified": True,
        "development_manifest_path": manifest_path.relative_to(root).as_posix(),
        "development_manifest_sha256": sha256_file(manifest_path),
        "dataset_sha256": manifest["version"], "code_sha256": code_fingerprint(root),
        "evaluation_policy_sha256": sha256_file(root / "configs/evaluation.json"),
        "pilot_policy_sha256": sha256_file(root / "configs/pilot.json"),
        "baseline_run_path": "runs/baseline",
        "baseline_artifacts": [{"path": baseline_path.relative_to(root).as_posix(),
                                "sha256": sha256_file(baseline_path)}],
        "local_worker": {"runtime_spec_path": "synthetic-not-a-runtime.json"}}
    readiness = root / "data/manifests/readiness.json"
    write_json(readiness, ready)
    return readiness


def fake_child(self, command, log, timeout=1200):
    def arg(name):
        return command[command.index(name) + 1]
    if command[0] == "verify":
        result = {"status": "PASS", "primary_candidate_selection_verified": True,
                  "synthetic_test_only": True}
        write_json(Path(arg("--output")), result)
        return result, 1.0
    output = Path(arg("--output-root")) / "synthetic-experiment"
    days = [f"2025-01-{day:02d}" for day in range(1, 7)]
    write_json(output / "selection_eligibility.json", {"eligible_days": days})
    write_json(output / "daywise_scores.json",
               [{"climate_date": day, "crps_f": .9, "brier": .1} for day in days])
    write_json(output / "primary_ledger.json", [{"climate_date": day,
               "net_profit": "0.1", "entry_outlay": "1"} for day in days])
    identity = canonical_hash(json.loads(Path(arg("--spec-file")).read_text()))
    report = {"path": str(output), "experiment_id": identity,
        "status": "SYNTHETIC_TEST_ONLY",
        "forecast_scores": {"gaussian_crps_f": .9, "brier": .1, "mae_f": 1.0},
        "historical_assumed_fill": {"trade_count": 6, "capital_weighted_return": ".1",
            "total_net_profit": ".6", "total_entry_outlay": "6", "mean_trade_return": ".1"},
        "predeclared_cost_sensitivity": [{"slippage": "0.02", "fee_rate": "0.10",
            "quantity": 1, "summary": {"capital_weighted_return": ".01"}}]}
    return report, 1.0


def test_allocation_rounding_preserves_the_registered_portfolio():
    assert _counts(10, {"deepen_supported": .5, "cross_colony_combinations": .2,
                        "independent_alternatives": .15, "adversarial_replication": .15}) == {
        "deepen_supported": 5, "cross_colony_combinations": 2,
        "independent_alternatives": 2, "adversarial_replication": 1}


def test_synthesis_packet_compacts_evidence_and_seeds_within_both_limits():
    evidence = [{"evidence_id": f"evidence-{index}", "scope": "selection",
                 "summary": (f"summary-{index}-" + "x" * 1780),
                 "artifact_sha256": f"{index:064x}"} for index in range(12)]
    seeds = [plan.to_dict() for plan in seed_plans("probability_calibration", "probability_calibration")[:12]]
    packet = {"protocol": PROTOCOL_V2, "task_id": "task-compact", "campaign_id": "campaign-compact",
              "role": "synthesizer", "scope": "development_only", "synthetic": False,
              "question": "Synthesize a bounded follow-up from curated evidence.",
              "code_sha256": "a" * 64, "dataset_sha256": "b" * 64,
              "evaluation_policy_sha256": "c" * 64, "evidence": evidence,
              "seed_plans": seeds, "colony": "probability_calibration",
              "stage": "probability_calibration", "parent_hypothesis_ids": []}
    limits = WorkerLimits(max_packet_bytes=10000, context_tokens=16384)
    compacted = compact_research_packet(packet, limits)
    assert compacted["evidence"][0]["evidence_id"] == "evidence-0"
    assert compacted["evidence"][-1]["evidence_id"] == "evidence-11"
    assert len(compacted["evidence"]) < len(evidence) or len(compacted["seed_plans"]) < len(seeds)
    assert len(canonical_json(compacted).encode()) <= limits.max_packet_bytes
    validate_research_packet(compacted, max_bytes=limits.max_packet_bytes)
    build_research_prompt(compacted, context_tokens=limits.context_tokens,
                          generation_tokens=limits.generation_tokens)


def test_synthetic_v2_cycle_executes_new_plans_synthesizes_and_stops_on_budget():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        readiness = fixture(root)
        with patch("klax_lab.campaign.LocalTextWorker", FakeV2Worker), \
             patch("klax_lab.campaign.load_runtime_spec", return_value=None), \
             patch.object(IterativeCampaign, "fixed_child", fake_child):
            report = IterativeCampaign(root, readiness).run()
        assert report["status"] == "RESEARCH_CYCLE_COMPLETE"
        assert report["architecture_version"] == 2
        assert 1 <= report["candidate_count"] <= 6
        assert report["champion"] is None
        folder = Path(report["path"])
        coverage = json.loads((folder / "search_coverage.json").read_text())
        ledger = json.loads((folder / "ledger.json").read_text())
        assert coverage["executed"] == report["candidate_count"]
        assert coverage["replicated"] == report["candidate_count"]
        kinds = {row["record"]["kind"] for row in ledger["decisions"]}
        assert {"SYNTHESIS", "ALLOCATION"} <= kinds
        assert any(kind in kinds for kind in {"CONTINUE", "FORK"})
        assert all(row["state"] == "SUCCEEDED" for row in ledger["tasks"])
