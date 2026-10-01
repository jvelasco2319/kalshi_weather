"""Controller engineering tests use synthetic artifacts, never market outcomes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from klax_lab.controller import (
    BudgetExceeded, CampaignLimits, Controller, ControllerError, InvalidResult,
    InvalidTransition, TaskSpec, file_sha256, run_fixture_campaign,
)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.db = self.root / "registry.sqlite"
        self.artifacts = self.root / "artifacts"
        self.c = Controller(self.db, self.artifacts, clock=lambda: self.now)
        self.c.create_campaign("fixture", CampaignLimits(token_budget=100, max_experiments=10))

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def task(self, task_id="task-a", **kwargs):
        spec = TaskSpec(task_id=task_id, campaign_id="fixture", idempotency_key=task_id,
                        epoch=1, track="R36", role="explorer", question="Synthetic arithmetic",
                        permitted_inputs=["synthetic-input"], prohibited_inputs=["holdout"],
                        allowed_outputs=[task_id], success_condition="Toy arithmetic matches",
                        failure_condition="Toy arithmetic mismatch", runtime_seconds=10, token_limit=10)
        return replace(spec, **kwargs)

    def result(self, task_id="task-a", *, experiment=True):
        output = self.artifacts / task_id / "synthetic.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"synthetic": true, "value": 4}', encoding="utf-8")
        eid, xid = f"{task_id}-evidence", f"{task_id}-experiment"
        return {
            "status": "completed",
            "claims": [{"text": "Synthetic result equals four", "kind": "finding", "evidence_ids": [eid]}],
            "evidence": [{"evidence_id": eid, "path": output.relative_to(self.artifacts).as_posix(),
                          "sha256": file_sha256(output), "kind": "synthetic_fixture", "experiment_id": xid if experiment else None}],
            "experiments": [{"experiment_id": xid, "code_sha256": "a" * 64, "dataset_sha256": file_sha256(output),
                             "parameters": {}, "metrics": {"value": 4}, "synthetic": True}] if experiment else [],
            "limitations": ["Synthetic engineering test only"], "next_steps": [],
        }

    def usage(self, **kwargs):
        return {
            **dict(tokens=2, compute_seconds=1, paid_micros=0, experiments=1), **kwargs}

    def complete(self, task_id="task-a", worker="worker-a"):
        accepted = self.c.claim_task("fixture", worker, task_id=task_id)
        self.assertIsNotNone(accepted)
        result = self.result(task_id)
        self.c.complete_task(task_id, accepted["lease_token"], result, self.usage())
        return accepted, result

    def test_fixture_cycle_failure_exchange_synthesis_replication_budget(self):
        summary = run_fixture_campaign(self.root / "complete-fixture")
        self.assertEqual(summary["state"], "FIXTURE_COMPLETE")
        self.assertFalse(summary["goal2_complete"])
        self.assertTrue(summary["synthetic"])
        self.assertEqual(summary["budget_exhaustion_demonstrated"], ["hypotheses", "experiments"])
        self.assertEqual(summary["experiments"], 4)
        ledger = json.loads((self.root / "complete-fixture/fixture-ledger.json").read_text())
        self.assertTrue(any(a["state"] == "FAILED" for a in ledger["attempts"]))
        self.assertTrue(any(h["state"] == "REJECTED" for h in ledger["hypotheses"]))
        self.assertTrue(any(d["record"]["kind"] == "SYNTHESIS" for d in ledger["decisions"]))
        self.assertTrue(any(t["spec"]["role"] == "replicator" and t["state"] == "SUCCEEDED" for t in ledger["tasks"]))
        self.assertEqual(summary["reserved"], dict(tokens=0, compute_seconds=0, paid_micros=0, experiments=0))

    def test_idempotent_submission_and_completion_do_not_double_spend(self):
        task = self.task()
        self.c.submit_task(task)
        self.c.submit_task(task)
        accepted, result = self.complete()
        self.c.complete_task(task.task_id, accepted["lease_token"], result, self.usage())
        status = self.c.status("fixture")
        self.assertEqual(len(status["tasks"]), 1)
        self.assertEqual(status["experiments"], 1)
        self.assertEqual(status["spent"]["tokens"], 2)
        with self.assertRaises(ControllerError):
            self.c.submit_task(replace(task, question="Different request"))
        result["status"] = "no_improvement"
        with self.assertRaises(InvalidResult):
            self.c.complete_task(task.task_id, accepted["lease_token"], result, self.usage())

    def test_atomic_claim_across_connections_only_one_worker_accepts(self):
        self.c.submit_task(self.task())

        def claim(worker):
            with Controller(self.db, self.artifacts, clock=lambda: self.now) as other:
                return other.claim_task("fixture", worker)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-1", "worker-2"]))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.c.status("fixture")["reserved"]["tokens"], 10)

    def test_four_slots_include_coordinator_and_pre_dispatch_reservations(self):
        for i in range(4):
            self.c.submit_task(self.task(f"task-{i}", token_limit=30))
        for i in range(3):
            self.c.claim_task("fixture", f"worker-{i}")
        self.assertEqual(self.c.status("fixture")["reserved"]["tokens"], 90)
        with self.assertRaises(BudgetExceeded):
            self.c.claim_task("fixture", "worker-4")

    def test_restart_timeouts_charge_unknown_usage_and_bound_retries(self):
        self.c.submit_task(self.task(token_limit=0))
        for attempt_number in range(3):
            accepted = self.c.claim_task("fixture", f"worker-{attempt_number}")
            self.c.close()
            self.now += 11
            self.c = Controller(self.db, self.artifacts, clock=lambda: self.now)
            recovered = self.c.recover_timeouts("fixture")
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["state"], "QUEUED" if attempt_number < 2 else "FAILED")
            self.assertEqual(self.c.recover_timeouts("fixture"), [])
            with self.assertRaises(InvalidTransition):
                self.c.complete_task("task-a", accepted["lease_token"], self.result(), self.usage(tokens=0))
        self.assertEqual(self.c.status("fixture")["spent"]["compute_seconds"], 30)
        self.assertIsNone(self.c.claim_task("fixture", "worker-after-limit"))

    def test_pause_resume_preserves_spend_and_original_wall_deadline(self):
        self.c.submit_task(self.task())
        self.complete()
        before = self.c.status("fixture")
        self.c.pause("fixture")
        self.assertEqual(self.c.status("fixture")["state"], "PAUSED")
        with self.assertRaises(InvalidTransition):
            self.c.claim_task("fixture", "paused-worker")
        self.c.resume("fixture")
        after = self.c.status("fixture")
        self.assertEqual(after["spent"], before["spent"])
        self.assertEqual(after["limits"], before["limits"])
        self.c.pause("fixture")
        self.now += after["limits"]["wall_seconds"] + 1
        with self.assertRaisesRegex(BudgetExceeded, "Original campaign wall-time"):
            self.c.resume("fixture")

    def test_reviewed_recovery_keeps_full_unknown_charges_and_retry_ceiling(self):
        self.c.submit_task(self.task())
        for attempt in range(3):
            claimed = self.c.claim_task("fixture", "interrupted-worker")
            self.assertIsNotNone(claimed)
            with self.assertRaises(InvalidTransition):
                self.c.pause("fixture")
            self.c.suspend("fixture", "synthetic interrupted coordinator")
            with self.assertRaisesRegex(InvalidTransition, "explicit recovery review"):
                self.c.resume("fixture")
            self.assertEqual(self.c.status("fixture")["spent"]["tokens"], 10 * (attempt + 1))
            if attempt < 2:
                self.c.resume("fixture", review_interrupted=True)
            else:
                with self.assertRaisesRegex(InvalidTransition, "retry ceiling"):
                    self.c.resume("fixture", review_interrupted=True)
        self.assertEqual(len(self.c.export_ledger("fixture")["attempts"]), 3)
        self.assertEqual(self.c.status("fixture")["spent"]["compute_seconds"], 30)

    def test_invalid_result_rolls_back_without_releasing_reservation(self):
        self.c.submit_task(self.task())
        accepted = self.c.claim_task("fixture", "worker-a")
        result = self.result()
        result["evidence"][0]["sha256"] = "0" * 64
        with self.assertRaises(InvalidResult):
            self.c.complete_task("task-a", accepted["lease_token"], result, self.usage())
        status = self.c.status("fixture")
        self.assertEqual(status["tasks"][0]["state"], "RUNNING")
        self.assertEqual(status["experiments"], 0)
        self.assertEqual(status["reserved"]["tokens"], 10)
        result = self.result()
        result["experiments"][0]["metrics"]["bad"] = float("nan")
        with self.assertRaises(ControllerError):
            self.c.complete_task("task-a", accepted["lease_token"], result, self.usage())

    def test_budget_charged_for_lost_worker_cannot_be_reused_on_retry(self):
        self.c.submit_task(self.task(token_limit=60))
        accepted = self.c.claim_task("fixture", "lost-worker")
        self.c.fail_task("task-a", accepted["lease_token"], "Lost connection", transient=True)
        with self.assertRaises(BudgetExceeded):
            self.c.claim_task("fixture", "retry-worker")
        status = self.c.status("fixture")
        self.assertEqual(status["spent"]["tokens"], 60)
        self.assertEqual(status["reserved"]["tokens"], 0)
        self.assertEqual(status["remaining"]["tokens"], 40)

    def test_conflicting_experiment_id_rolls_back_all_new_evidence(self):
        self.c.submit_task(self.task())
        self.complete()
        self.c.submit_task(self.task("task-b"))
        accepted = self.c.claim_task("fixture", "worker-b", task_id="task-b")
        result = self.result("task-b")
        result["experiments"][0]["experiment_id"] = "task-a-experiment"
        result["evidence"][0]["experiment_id"] = "task-a-experiment"
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.complete_task("task-b", accepted["lease_token"], result, self.usage())
        status = self.c.status("fixture")
        self.assertEqual(status["experiments"], 1)
        self.assertEqual({t["id"]: t["state"] for t in status["tasks"]}["task-b"], "RUNNING")
        self.assertEqual(len(self.c.export_ledger("fixture")["evidence"]), 1)

    def test_tampered_source_evidence_rejected_on_later_use(self):
        self.c.submit_task(self.task())
        self.complete()
        (self.artifacts / "task-a/synthetic.json").write_text("tampered", encoding="utf-8")
        with self.assertRaises(InvalidResult):
            self.c.add_message("fixture", "m1", "m1", sender="worker-a", kind="FINDING", text="Claim", evidence_ids=["task-a-evidence"])

    def test_independent_reviewer_identity_required(self):
        self.c.submit_task(self.task())
        self.complete(worker="discoverer")
        self.c.submit_task(self.task("replicate", role="replicator", source_evidence_ids=["task-a-evidence"], dependencies=["task-a"]))
        with self.assertRaises(ControllerError):
            self.c.claim_task("fixture", "discoverer", task_id="replicate")
        accepted = self.c.claim_task("fixture", "independent-worker", task_id="replicate")
        self.assertIsNotNone(accepted)

    def test_output_traversal_overlap_and_bad_evidence_rejected(self):
        with self.assertRaises(ControllerError):
            self.c.submit_task(self.task(allowed_outputs=["../outside"]))
        self.c.submit_task(self.task())
        with self.assertRaises(ControllerError):
            self.c.submit_task(self.task("task-b", allowed_outputs=["task-a/subdir"]))
        accepted = self.c.claim_task("fixture", "worker-a")
        result = self.result()
        result["evidence"][0]["path"] = "../outside.json"
        with self.assertRaises(ControllerError):
            self.c.complete_task("task-a", accepted["lease_token"], result, self.usage())

    def test_failed_dependency_blocks_followup_and_cannot_be_completed(self):
        self.c.submit_task(self.task())
        self.c.submit_task(self.task("followup", dependencies=["task-a"]))
        accepted = self.c.claim_task("fixture", "worker-a", task_id="task-a")
        self.c.fail_task("task-a", accepted["lease_token"], "Known failure", transient=False, actual_usage=self.usage(experiments=0))
        self.assertIsNone(self.c.claim_task("fixture", "worker-b"))
        self.assertEqual({t["id"]: t["state"] for t in self.c.status("fixture")["tasks"]}, {"task-a": "FAILED", "followup": "BLOCKED"})
        with self.assertRaises(InvalidTransition):
            self.c.complete_task("task-a", accepted["lease_token"], self.result(), self.usage())

    def test_hypothesis_and_epoch_budgets_and_illegal_gate(self):
        for i in range(12):
            self.c.add_hypothesis("fixture", f"h{i}", f"h{i}", claim="C", mechanism="M", falsification="F", planned_comparison="P")
        with self.assertRaises(BudgetExceeded):
            self.c.add_hypothesis("fixture", "h13", "h13", claim="C", mechanism="M", falsification="F", planned_comparison="P")
        with self.assertRaises(BudgetExceeded):
            self.c.submit_task(self.task(epoch=4))
        self.c.submit_task(self.task(hypothesis_id="h0"))
        self.complete()
        with self.assertRaises(InvalidTransition):
            self.c.record_decision("fixture", "gate", "gate", kind="GATE", rationale="Invalid jump", evidence_ids=["task-a-evidence"], hypothesis_id="h0", target_state="CANDIDATE")

    def test_historical_campaign_refuses_missing_readiness_and_synthetic_shortcut(self):
        with self.assertRaises(ControllerError):
            self.c.create_campaign("real", mode="historical_research")
        provenance = dict(readiness_path="readiness.json", readiness_sha256="a" * 64, code_sha256="a" * 64,
                          dataset_sha256="b" * 64, evaluation_policy_sha256="c" * 64, backend="external_manual")
        readiness = {"status": "BLOCKED", "code_sha256": "a" * 64, "dataset_sha256": "b" * 64,
                     "evaluation_policy_sha256": "c" * 64, "offline_verified": False, "holdout_access_denied": False}
        path = self.artifacts / "readiness.json"
        path.write_text(json.dumps(readiness), encoding="utf-8")
        provenance["readiness_sha256"] = file_sha256(path)
        with self.assertRaises(ControllerError):
            self.c.create_campaign("real", mode="historical_research", provenance=provenance)

    def test_wall_time_and_paid_budget_and_false_completion(self):
        with self.assertRaises(BudgetExceeded):
            self.c.submit_task(self.task(paid_limit_micros=1))
        with self.assertRaises(InvalidTransition):
            self.c.finish("fixture", "NO_IMPROVEMENT")
        self.c.submit_task(self.task())
        self.now += 21600
        with self.assertRaises(BudgetExceeded):
            self.c.claim_task("fixture", "late-worker")

    def test_schema_is_valid_json_and_matches_task_contract(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/task.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(self.task().__dataclass_fields__))


if __name__ == "__main__":
    unittest.main()
