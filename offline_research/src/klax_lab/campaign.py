"""Finite, local, packet-only research campaign with deterministic experiments.

Model text never becomes Python, a path, an executable or a shell argument.
Only a validated five-parameter recipe crosses into the fixed evaluator.
The trusted coordinator sees development summaries only, not final outcomes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from .candidates import CandidateSpec
from .controller import CampaignLimits, Controller, TaskSpec
from .local_backend import LocalTextWorker, PROTOCOL, WorkerLimits, build_prompt, load_runtime_spec, parse_response, validate_packet
from .pipeline import job_lock
from .provenance import canonical_hash, inventory, sha256_file, verify_manifest, write_json
from .readiness import code_fingerprint, validate_development_manifest_paths


def _assert_campaign_inputs(root, ready, manifest_path, manifest, baseline_path):
    if ready.get("status") != "READY_FOR_OFFLINE_CAMPAIGN":
        raise ValueError("Goal 1 is not ready")
    if code_fingerprint(root) != ready["code_sha256"]:
        raise ValueError("Code changed after readiness")
    for name, key in (("evaluation.json", "evaluation_policy_sha256"), ("pilot.json", "pilot_policy_sha256")):
        if sha256_file(root / "configs" / name) != ready[key]:
            raise ValueError("Registered policy changed")
    if sha256_file(manifest_path) != ready["development_manifest_sha256"]:
        raise ValueError("Development manifest changed")
    validate_development_manifest_paths(root, manifest)
    verify_manifest(root, manifest)
    if manifest["version"] != ready["dataset_sha256"]:
        raise ValueError("Readiness identifies another dataset")
    bound = set()
    for item in ready["baseline_artifacts"]:
        target = (root / item["path"]).resolve()
        if target.parent != baseline_path.parent:
            raise ValueError("Baseline evidence path escapes the baseline run")
        if sha256_file(target) != item["sha256"]:
            raise ValueError("Verified baseline artifact changed")
        bound.add(target)
    if baseline_path not in bound:
        raise ValueError("Readiness must bind the baseline summary")


def candidate_options(track: int) -> list[dict]:
    """Small registered search families; no post-holdout expansion."""
    choices = []
    if track == 1:
        for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
            for scale in (1.0, 1.15):
                choices.append(CandidateSpec(weight, "global", "global", scale, 0.0))
    elif track == 2:
        for weight in (0.25, 0.5, 0.75):
            for bias in ("monthly_shrinkage", "seasonal_harmonic"):
                for scale in (1.0, 1.15):
                    choices.append(CandidateSpec(weight, bias, "global", scale, 0.0))
    elif track == 3:
        for bias in ("global", "monthly_shrinkage"):
            for spread, coefficient in (("monthly_shrinkage", 0.0), ("disagreement", 0.25), ("disagreement", 0.5)):
                for scale in (1.0, 1.3):
                    choices.append(CandidateSpec(0.5, bias, spread, scale, coefficient))
    else:
        raise ValueError("Unknown registered research track")
    return [asdict(spec) for spec in choices]


def assess_development_gate(result: dict, baseline: dict, replica: dict,
                            critic: dict, gate: dict) -> dict:
    """A fixed selection screen, never an estimate of untouched performance."""
    scores = result["forecast_scores"]
    reference = baseline["forecast_scores"]
    economics = result["historical_assumed_fill"]
    stress = [row for row in result["predeclared_cost_sensitivity"]
              if str(row["slippage"]) == gate["stress_slippage"]
              and str(row["fee_rate"]) == gate["stress_fee_rate"]
              and row["quantity"] == gate["stress_quantity"]]
    reasons = []
    def finite(value):
        try:
            return type(value) in (int, float, str) and math.isfinite(float(value))
        except (ValueError, OverflowError):
            return False
    crps, ref_crps, brier, ref_brier = (scores.get("gaussian_crps_f"), reference.get("gaussian_crps_f"),
                                       scores.get("brier"), reference.get("brier"))
    improvement = None
    if all(finite(value) for value in (crps, ref_crps, brier, ref_brier)) and float(ref_crps) > 0:
        improvement = 1 - float(crps) / float(ref_crps)
        if improvement < gate["minimum_relative_CRPS_improvement"]:
            reasons.append("insufficient_CRPS_improvement")
        if float(brier) > float(ref_brier) + gate["maximum_Brier_degradation"]:
            reasons.append("Brier_degraded")
    else:
        reasons.append("forecast_scores_missing_or_invalid")
    if economics.get("trade_count", 0) < gate["minimum_simulated_trades"]:
        reasons.append("insufficient_simulated_trades")
    roi = economics.get("capital_weighted_return")
    if not finite(roi) or float(roi) < gate["minimum_capital_weighted_return"]:
        reasons.append("selection_return_below_screen")
    stress_roi = stress[0]["summary"].get("capital_weighted_return") if len(stress) == 1 else None
    if not finite(stress_roi) or float(stress_roi) < gate["minimum_stress_return"]:
        reasons.append("cost_stress_failed_or_missing")
    if replica.get("status") != "PASS" or replica.get("primary_candidate_selection_verified") is not True:
        reasons.append("independent_verification_failed")
    # A fresh critic may veto; abstention is not endorsement.
    if critic.get("action") != "propose":
        reasons.append("critic_rejected_or_abstained")
    return {"passed": not reasons, "reasons": reasons, "relative_CRPS_improvement": improvement,
            "stress_return": stress_roi, "scope": "Development screen only; adaptive selection is not final evidence"}


def candidate_evidence_summary(spec: dict, result: dict, gate: dict) -> str:
    """Compact complete metrics rather than a potentially truncated JSON blob."""
    scores, economic = result["forecast_scores"], result["historical_assumed_fill"]
    stress = [row for row in result["predeclared_cost_sensitivity"]
              if row["slippage"] == gate["stress_slippage"] and row["fee_rate"] == gate["stress_fee_rate"]
              and row["quantity"] == gate["stress_quantity"]]
    interval = economic.get("bootstrap", {})
    def fmt(value):
        return "missing" if value is None else f"{float(value):.5g}"
    return (f"Recipe: GFS={spec['gfs_weight']}, bias={spec['bias_mode']}, spread={spec['spread_mode']}, "
        f"scale={spec['spread_scale']}, disagreement={spec['disagreement_coefficient']}. "
        f"Selection: CRPS={fmt(scores['gaussian_crps_f'])}, Brier={fmt(scores['brier'])}, MAE={fmt(scores['mae_f'])}; "
        f"entries={economic['trade_count']}, weighted ROI={fmt(economic['capital_weighted_return'])}, "
        f"mean ROI={fmt(economic.get('mean_trade_return'))}, net dollars={fmt(economic['total_net_profit'])}, "
        f"outlay={fmt(economic.get('total_entry_outlay'))}. Day-bootstrap ROI=[{fmt(interval.get('lower_95'))},{fmt(interval.get('upper_95'))}]. "
        f"Registered cost-stress ROI={fmt(stress[0]['summary'].get('capital_weighted_return') if len(stress) == 1 else None)}. "
        "Fit uses 2024 only; selection is adaptive. Hourly assumed fills, fee/publication assumptions and retrospective reconciliation apply. Final unseen.")


class CampaignPaused(Exception):
    """A requested, completed task boundary; no failed task or new budget."""


class Campaign:
    ORCHESTRATION_VERSION = 1

    def __init__(self, root: Path, readiness_path: Path, *, ticket=None, review_interrupted=False):
        self.root = root.resolve()
        self.readiness_path = readiness_path.resolve()
        self.readiness_path.relative_to(self.root)
        self.ready = json.loads(self.readiness_path.read_text(encoding="utf-8"))
        self.pilot = json.loads((self.root / "configs/pilot.json").read_text(encoding="utf-8"))
        self.manifest_path = (self.root / self.ready["development_manifest_path"]).resolve()
        if self.manifest_path.parent != self.root / "data/manifests":
            raise ValueError("Development manifest must be in the manifest directory")
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.baseline_path = (self.root / self.ready["baseline_run_path"] / "summary.json").resolve()
        if self.baseline_path.parent.parent != self.root / "runs" or not self.baseline_path.parent.name.startswith("baseline"):
            raise ValueError("Readiness must reference a development baseline run")
        self.assert_inputs()
        self.baselines = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        self.reference_name = min(self.baselines["models"], key=lambda name: (
            self.baselines["models"][name]["forecast_scores"]["gaussian_crps_f"], name))
        self.reference = self.baselines["models"][self.reference_name]
        self.id = ticket["campaign_id"] if ticket else "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.output = self.root / "runs/campaigns" / self.id
        if ticket:
            if not self.output.is_dir() or ticket["campaign_path"] != self.output.relative_to(self.root).as_posix():
                raise ValueError("Saved campaign directory differs")
            if not (self.output / "controller.sqlite3").is_file():
                raise ValueError("Saved campaign registry is missing")
        else:
            self.output.mkdir(parents=True, exist_ok=False)
        self.prefix = self.output.relative_to(self.root).as_posix()
        self.started = time.monotonic()
        self.calls = 0
        self.counter = 0
        self.board = []
        self.candidates = []
        self.tested = set()
        self.worker = LocalTextWorker(load_runtime_spec(self.root, self.root / self.ready["local_worker"]["runtime_spec_path"]),
            WorkerLimits(context_tokens=self.pilot["context_tokens_per_call"],
                         generation_tokens=self.pilot["generation_tokens_per_call"]))
        self.controller = Controller(self.output / "controller.sqlite3", self.root)
        try:
            self.controller.create_campaign(self.id, CampaignLimits(
                max_hypotheses=self.pilot["max_hypotheses"], max_epochs=self.pilot["max_epochs"],
                max_concurrency=self.pilot["max_total_agent_slots"], coordinator_slots=self.pilot["reserved_coordinator_slots"],
                max_tasks=self.pilot.get("max_tasks", 64), max_experiments=self.pilot["max_experiments"], max_retries=self.pilot["max_retries"],
                wall_seconds=self.pilot["max_campaign_wall_seconds"], compute_seconds=self.pilot["max_campaign_wall_seconds"],
                token_budget=self.pilot["local_token_budget"], paid_budget_micros=0), mode="historical_research",
                provenance={"backend": "external_manual", "execution_adapter": self.pilot["backend"],
                    "readiness_path": self.readiness_path.relative_to(self.root).as_posix(),
                    "readiness_sha256": sha256_file(self.readiness_path),
                    **{key: self.ready[key] for key in ("code_sha256", "dataset_sha256", "evaluation_policy_sha256")}})
        except BaseException:
            self.controller.close()
            raise
        if ticket:
            try:
                checkpoint = self.controller.read_checkpoint(self.id)
                if checkpoint and (checkpoint.get("orchestration_version") != self.ORCHESTRATION_VERSION or checkpoint.get("binding") != _ticket_binding(self.root, self.readiness_path)):
                    raise ValueError("Coordinator checkpoint input binding differs")
                self.verify_saved_artifacts()
                self.controller.resume(self.id, review_interrupted=review_interrupted)
                pause_path = self.output / "pause-request.json"
                if pause_path.exists():
                    pause_path.unlink()
            except BaseException:
                self.controller.close()
                raise
        created = self.controller._campaign(self.id)["created"]
        self.started = time.monotonic() - max(0, self.controller.clock() - created)
        self.refresh_calls()

    def refresh_calls(self):
        self.calls = sum(1 for row in self.controller.db.execute(
            "SELECT t.spec FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.campaign_id=?", (self.id,))
            if json.loads(row["spec"])["token_limit"] > 0)

    def verify_saved_artifacts(self):
        self.controller.audit_recovery(self.id)
        for row in self.controller.export_ledger(self.id)["evidence"]:
            if row["record"]["kind"] == "implementer_result":
                result = json.loads((self.root / row["record"]["path"]).read_text(encoding="utf-8"))
                if result.get("status") == "DEVELOPMENT_EXPERIMENT_COMPLETE":
                    from .experiments import _read_completed
                    folder = Path(result["path"]).resolve()
                    if not folder.is_relative_to(self.output):
                        raise ValueError("Saved experiment escapes this campaign")
                    _read_completed(folder, result["experiment_id"])

    def boundary(self):
        """Only stop after a task returned and its durable result was handled."""
        self.refresh_calls()
        self.controller.checkpoint(self.id, {"orchestration_version": self.ORCHESTRATION_VERSION,
            "binding": _ticket_binding(self.root, self.readiness_path), "task_cursor": self.counter,
            "local_model_calls": self.calls, "tested_candidates": sorted(self.tested),
            "reconstruction": "Replay fixed task plan; reuse verified SUCCEEDED results only"})
        request = self.output / "pause-request.json"
        if request.exists():
            record = json.loads(request.read_text(encoding="utf-8"))
            if record.get("campaign_id") != self.id:
                raise ValueError("Pause request identifies another campaign")
            self.controller.pause(self.id)
            self.checkpoint("PAUSED")
            raise CampaignPaused()

    def saved_result(self, task):
        row = self.controller.db.execute("SELECT state FROM tasks WHERE id=?", (task.task_id,)).fetchone()
        if row["state"] != "SUCCEEDED":
            return None
        records = [item for item in self.controller.export_ledger(self.id)["evidence"] if item["task_id"] == task.task_id]
        if len(records) != 1:
            raise ValueError("Saved task evidence inventory differs")
        item = records[0]
        self.controller._evidence_records(self.id, [item["id"]])
        result = json.loads((self.root / item["record"]["path"]).read_text(encoding="utf-8"))
        return result, {"evidence_id": item["id"], "scope": "selection",
                        "summary": json.dumps(result, default=str)[:1200], "artifact_sha256": item["record"]["sha256"]}

    def assert_inputs(self):
        _assert_campaign_inputs(self.root, self.ready, self.manifest_path, self.manifest, self.baseline_path)

    def checkpoint(self, state: str):
        write_json(self.output / "progress.json", {"status": state, "campaign_id": self.id,
            "local_model_calls": self.calls, "tested_candidates": len(self.candidates),
            "elapsed_seconds": time.monotonic() - self.started, "goal2_complete": False})

    def task(self, role, epoch, track, question, sources=(), hypothesis=None, *, experiment=False, model=False):
        self.boundary()
        self.counter += 1
        tid = f"task-{self.counter:03d}"
        spec = TaskSpec(tid, self.id, tid, epoch, f"R{track:02d}", role, question,
            list(sources), ["protected_final", "raw_data", "network", "arbitrary_code"],
            [f"{self.prefix}/tasks/{tid}"], "Produce verified artifact or explicit rejection",
            "Missing input, malformed output, budget exhaustion or failed verification",
            hypothesis_id=hypothesis, source_evidence_ids=list(sources),
            runtime_seconds=1210 if experiment or role == "replicator" else 200,
            token_limit=self.worker.limits.context_tokens if model else 0,
            paid_limit_micros=0, experiment_limit=1 if experiment else 0)
        self.controller.submit_task(spec)
        return spec

    def artifact_result(self, task, claim, path, report, *, elapsed=1, experiment=None):
        eid = "evidence-" + task.task_id
        evidence = {"evidence_id": eid, "path": path.relative_to(self.root).as_posix(),
                    "sha256": sha256_file(path), "kind": task.role + "_result",
                    "experiment_id": experiment["experiment_id"] if experiment else None}
        self.controller.complete_task(task.task_id, claim["lease_token"], {
            "status": "completed", "claims": [{"text": "Completed fixed local evaluation; see artifact for scope and limitations",
                "kind": "finding", "evidence_ids": [eid]}], "evidence": [evidence],
            "experiments": [experiment] if experiment else [],
            "limitations": ["Assumed-fill historical scenarios; no executable edge established"], "next_steps": []},
            {"tokens": 0, "compute_seconds": max(1, math.ceil(elapsed)), "paid_micros": 0,
             "experiments": 1 if experiment else 0})
        return {"evidence_id": eid, "scope": "selection", "summary": json.dumps(report, default=str)[:1200],
                "artifact_sha256": evidence["sha256"]}

    def model_task(self, role, epoch, track, question, evidence, options, hypothesis=None):
        self.assert_inputs()
        selected = [dict(item) for item in list({item["evidence_id"]: item for item in evidence}.values())[-8:]]
        packet = {"protocol": PROTOCOL, "task_id": f"task-{self.counter + 1:03d}", "campaign_id": self.id,
            "role": role, "scope": "development_only", "synthetic": False, "question": question,
            **{key: self.ready[key] for key in ("code_sha256", "dataset_sha256", "evaluation_policy_sha256")},
            "evidence": selected, "candidate_options": options}
        # Curated summaries are excerpts with full artifact hashes, not complete
        # artifacts. Keep the registered packet/context caps unchanged.
        for maximum in (700, 500, 300):
            for item in selected:
                item["summary"] = item["summary"][:maximum]
            try:
                validate_packet(packet, self.worker.limits)
                build_prompt(packet, self.worker.limits)
                break
            except ValueError:
                if maximum == 300:
                    raise
        task = self.task(role, epoch, track, question, [e["evidence_id"] for e in selected], hypothesis, model=True)
        saved = self.saved_result(task)
        if saved:
            record, evidence = saved
            response = parse_response(json.dumps(record.get("response", record)), packet, self.worker.limits)
            evidence["summary"] = response["rationale"][:1000]
            return response, evidence
        if self.calls >= self.pilot["max_local_model_calls"]:
            raise ValueError("Local model call budget exhausted")
        self.checkpoint(f"LOCAL_{role.upper()}_{task.task_id}")
        record = self.worker.dispatch_one(self.controller, self.id, f"{role}-R{track:02d}", task.task_id, packet)
        self.refresh_calls()
        if record is None:
            raise RuntimeError("Submitted local worker task was not claimed")
        rows = self.controller.export_ledger(self.id)["evidence"]
        saved = [item for item in rows if item["task_id"] == task.task_id]
        if len(saved) != 1:
            raise ValueError("Local proposal evidence inventory differs")
        item = saved[0]
        return record["response"], {"evidence_id": item["id"], "scope": "selection",
            "summary": record["response"]["rationale"][:1000], "artifact_sha256": item["record"]["sha256"]}

    def fixed_child(self, command: list[str], log: Path, timeout=1200):
        self.assert_inputs()
        remaining = self.pilot["max_campaign_wall_seconds"] - (time.monotonic() - self.started)
        if remaining <= 10:
            raise TimeoutError("Campaign wall budget exhausted")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.root / "src")
        started = time.monotonic()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as stream, log.with_suffix(log.suffix + ".stderr").open("w", encoding="utf-8") as errors:
            process = subprocess.run([sys.executable, "-m", "klax_lab.experiments", *command],
                cwd=self.root, env=env, stdout=stream, stderr=errors, close_fds=True,
                timeout=min(timeout, remaining - 5))
        if process.returncode:
            raise RuntimeError("Fixed offline evaluator failed: " + str(log))
        return json.loads(log.read_text(encoding="utf-8")), time.monotonic() - started

    def evaluate(self, spec: dict, epoch: int, track: int, rationale: str, *, parent_ids=(), parent_evidence=()):
        validated = CandidateSpec(**spec)
        identity = validated.identity
        if identity in self.tested:
            return
        self.tested.add(identity)
        hid = "hyp-" + identity[:16]
        self.controller.add_hypothesis(self.id, hid, hid, claim=rationale,
            mechanism="Training-only Gaussian calibration of archived GFS/NBM station proxies",
            falsification="Fails fixed CRPS, Brier, sample, costs, replication or critic gate",
            planned_comparison="Best registered baseline on the same development cases", parent_ids=list(parent_ids))
        if parent_ids:
            self.controller.record_decision(self.id, hid + "-combined", hid + "-combined", kind="SYNTHESIS",
                rationale="Evidence-backed combined follow-up; retain both parents, including negative evidence",
                evidence_ids=list(parent_evidence))
        task = self.task("implementer", epoch, track, "Run the registered closed candidate recipe", parent_evidence, hypothesis=hid, experiment=True)
        directory = self.root / task.allowed_outputs[0]
        recipe = directory / "recipe.json"
        saved = self.saved_result(task)
        if saved:
            result, evidence = saved
        else:
            claim = self.controller.claim_task(self.id, "fixed-implementation", task_id=task.task_id)
            write_json(recipe, spec)
            self.checkpoint("OFFLINE_EXPERIMENT_" + hid)
            try:
                result, elapsed = self.fixed_child(["run", "--root", str(self.root), "--manifest", str(self.manifest_path),
                    "--spec-file", str(recipe), "--output-root", str(directory / "experiment")], directory / "run.log")
                result_path = directory / "registered-result.json"
                write_json(result_path, result)
                metrics = {key: float(value) for key, value in result["forecast_scores"].items()
                           if type(value) in (int, float) and math.isfinite(value)}
                experiment = {"experiment_id": result["experiment_id"], "code_sha256": self.ready["code_sha256"],
                    "dataset_sha256": self.ready["dataset_sha256"], "parameters": spec, "metrics": metrics, "synthetic": False}
                evidence = self.artifact_result(task, claim, result_path, {}, elapsed=elapsed, experiment=experiment)
            except Exception as exc:
                self.controller.fail_task(task.task_id, claim["lease_token"], str(exc)[:300], actual_usage=None)
                raise
        evidence["summary"] = candidate_evidence_summary(spec, result, self.pilot["development_gate"])
        self.controller.record_decision(self.id, hid + "-tested", hid + "-tested", kind="GATE",
            rationale="Fixed evaluator produced a historical experiment", evidence_ids=[evidence["evidence_id"]],
            hypothesis_id=hid, target_state="TESTED")
        self.controller.add_message(self.id, hid + "-exchange", hid + "-exchange", sender="fixed-implementation",
            kind="REQUEST_REPLICATION", text="Independently verify this candidate and challenge its assumptions",
            evidence_ids=[evidence["evidence_id"]])
        critic, critic_evidence = self.model_task("critic", epoch, track,
            "Independently challenge this candidate using the supplied evidence. Consider selection bias, calibration, costs and sample size. "
            "Use action=reject if the evidence contradicts advancement, abstain if insufficient, or propose with the same candidate if it merits the fixed numerical gate. "
            "Advancement means one protected historical evaluation under disclosed assumptions, never trading authorization. "
            "Identify candidate-specific problems as well as shared data limits. Model agreement cannot certify profitability.", [self.board[0], evidence], [spec], hid)
        task = self.task("replicator", epoch, track, "Independently recompute saved candidate evidence",
                         [evidence["evidence_id"]], hid)
        replica_path = self.root / task.allowed_outputs[0] / "replication.json"
        saved = self.saved_result(task)
        if saved:
            replica, replica_evidence = saved
        else:
            claim = self.controller.claim_task(self.id, "independent-fixed-replicator", task_id=task.task_id)
            try:
                replica, elapsed = self.fixed_child(["verify", "--root", str(self.root), "--manifest", str(self.manifest_path),
                    "--run-directory", result["path"], "--output", str(replica_path)], replica_path.with_suffix(".log"))
                replica_evidence = self.artifact_result(task, claim, replica_path, replica, elapsed=elapsed)
            except Exception as exc:
                self.controller.fail_task(task.task_id, claim["lease_token"], str(exc)[:300], actual_usage=None)
                raise
        gate = assess_development_gate(result, self.reference, replica, critic, self.pilot["development_gate"])
        refs = [e["evidence_id"] for e in (evidence, critic_evidence, replica_evidence)]
        if gate["passed"]:
            for state in ("SUPPORTED", "REPLICATED", "CANDIDATE"):
                self.controller.record_decision(self.id, hid + "-" + state, hid + "-" + state,
                    kind="GATE", rationale="Passed registered development screen with separate critic and verification",
                    evidence_ids=refs, hypothesis_id=hid, target_state=state)
        else:
            self.controller.record_decision(self.id, hid + "-rejected", hid + "-rejected", kind="REJECTION",
                rationale=", ".join(gate["reasons"]), evidence_ids=refs, hypothesis_id=hid, target_state="REJECTED")
        self.board.append(evidence)
        self.candidates.append({"hypothesis_id": hid, "candidate_id": identity, "spec": spec,
            "parent_ids": list(parent_ids),
            "experiment_path": result["path"], "experiment_id": result["experiment_id"], "gate": gate,
            "forecast_scores": result["forecast_scores"], "historical_assumed_fill": result["historical_assumed_fill"],
            "critic": critic, "replication_path": str(replica_path), "evidence_ids": refs})
        write_json(self.output / "candidate_register.json", self.candidates)

    def run(self):
        try:
            if self.controller.status(self.id)["state"] == "RESEARCH_CYCLE_COMPLETE":
                # Completion may have committed before its ticket/artifact export.
                checkpoint = self.controller.read_checkpoint(self.id) or {}
                if (checkpoint.get("completion_summary_sha256") != sha256_file(self.output / "summary.json")
                    or checkpoint.get("candidate_register_sha256") != sha256_file(self.output / "candidate_register.json")):
                    raise ValueError("Committed campaign output hashes differ")
                report = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
                if (report.get("campaign_id") != self.id or report.get("readiness_sha256") != sha256_file(self.readiness_path)
                    or report.get("status") != "RESEARCH_CYCLE_COMPLETE"):
                    raise ValueError("Committed campaign summary differs")
                write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
                return {"path": str(self.output), **report}
            task = self.task("auditor", 1, 1, "Register verified baseline context")
            path = self.root / task.allowed_outputs[0] / "baseline-context.json"
            context = {"best_baseline_by_CRPS": self.reference_name,
                "scores": {k: self.reference["forecast_scores"][k] for k in ("gaussian_crps_f", "brier", "mae_f")},
                "training_days": self.baselines["training_days"], "selection_days": self.baselines["eligible_selection_days"],
                "limitations": ["Retrospective reconciled subset", "Hourly fills, fees and availability assumptions",
                    "Protected final unseen", "All candidate recipes fit 2024 only"], "gate": self.pilot["development_gate"]}
            saved = self.saved_result(task)
            if saved:
                _, evidence = saved
            else:
                claim = self.controller.claim_task(self.id, "baseline-curator", task_id=task.task_id)
                write_json(path, context)
                evidence = self.artifact_result(task, claim, path, context)
            self.board.append(evidence)
            self.evaluate(self.pilot["host_registered_seed"], 1, 2, "A preregistered shrunk monthly mean and spread may improve Gaussian calibration")
            # V1 is retained only to reproduce the original three-round pilot.
            # V2 owns the configurable iterative budget.
            for epoch in range(1, min(3, self.pilot["max_epochs"]) + 1):
                for track in range(1, 4):
                    options = [spec for spec in candidate_options(track) if CandidateSpec.from_dict(spec).identity not in self.tested]
                    if not options:
                        continue
                    parents = self.candidates[-2:] if epoch >= 2 and track in (2, 3) and len(self.candidates) >= 2 else []
                    parent_ids = [row["hypothesis_id"] for row in parents]
                    parent_evidence = [row["evidence_ids"][0] for row in parents]
                    phase = ("Independent initial proposal: use only the registered baseline context." if epoch == 1 else
                             "Combination round: combine lessons from the cited parent experiments, retaining negative evidence." if epoch == 2 and parents else
                             "Challenger assignment: test a simpler or conflicting explanation against the previous evidence." if epoch == 2 else
                             "Adversarial confirmation round: challenge the apparent leader and prioritize independent falsification before freezing.")
                    sources = [self.board[0]] if epoch == 1 else [self.board[0], *self.board[-5:]]
                    response, proposal = self.model_task("explorer", epoch, track,
                        f"Round {epoch}, research track {track}. Select one untested registered candidate most likely to improve calibration. "
                        + phase + " Explain the weather mechanism and a falsifiable expectation. "
                        "Only choose from candidate_options. Abstain if no defensible option remains.",
                        sources, options)
                    if response["action"] == "propose":
                        self.evaluate(response["candidate"], epoch, track, response["rationale"], parent_ids=parent_ids,
                                      parent_evidence=[*parent_evidence, proposal["evidence_id"]] if parents else ())
                    else:
                        self.controller.add_message(self.id, f"abstain-{epoch}-{track}", f"abstain-{epoch}-{track}",
                            sender=f"explorer-R{track:02d}", kind="BLOCKER", text=response["rationale"],
                            evidence_ids=[proposal["evidence_id"]])
                objective = ("Identify components and contradictions for an evidence-backed combined follow-up." if epoch == 1 else
                             "Compare combined follow-ups against their parents and name a simpler challenger." if epoch == 2 else
                             "Audit the complete positive and negative record before the fixed numerical champion ranking; no additional search follows.")
                response, evidence = self.model_task("synthesizer", epoch, 1,
                    "Synthesize the supplied development evidence, including failures. " + objective + " "
                    "Do not invent results or claim final performance. Use action=abstain,candidate=null; this is a synthesis only.",
                    [self.board[0], *self.board[-6:]], [])
                self.controller.record_decision(self.id, f"synthesis-{epoch}", f"synthesis-{epoch}", kind="SYNTHESIS",
                    rationale=response["rationale"], evidence_ids=[evidence["evidence_id"]])
                self.board.append(evidence)
            self.boundary()
            passed = [row for row in self.candidates if row["gate"]["passed"]]
            passed.sort(key=lambda row: (row["forecast_scores"]["gaussian_crps_f"], row["forecast_scores"]["brier"], row["candidate_id"]))
            champion = passed[0] if passed else None
            report = {"status": "RESEARCH_CYCLE_COMPLETE", "campaign_id": self.id,
                "scientific_conclusion": "SUPPORTED_WITH_LIMITATIONS" if champion else "NO_IMPROVEMENT",
                "candidate_count": len(self.candidates), "local_model_calls": self.calls,
                "reference_baseline": self.reference_name, "champion": champion,
                "protected_final_evaluated": False, "goal1_complete": True, "goal2_complete": False,
                "readiness_sha256": sha256_file(self.readiness_path),
                "pilot_policy_sha256": self.ready["pilot_policy_sha256"],
                "limitations": ["Selection evidence only; frozen final evaluation remains required if a candidate qualified",
                    "All local language workers share the same model weights but use separate task contexts",
                    "Numerical verification, not agent agreement, supports arithmetic claims",
                    "Negative results and abstentions retained; campaign stops after the fixed budget"]}
            write_json(self.output / "summary.json", report)
            self.controller.checkpoint(self.id, {"orchestration_version": 1,
                "binding": _ticket_binding(self.root, self.readiness_path),
                "completion_summary_sha256": sha256_file(self.output / "summary.json"),
                "candidate_register_sha256": sha256_file(self.output / "candidate_register.json")})
            self.controller.finish(self.id, report["scientific_conclusion"])
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            self.checkpoint("RESEARCH_CYCLE_COMPLETE")
            return {"path": str(self.output), **report}
        except CampaignPaused:
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            return {"status": "PAUSED", "path": str(self.output), "campaign_id": self.id,
                    "goal2_complete": False, "resources": self.controller.status(self.id)}
        except BaseException as exc:
            self.controller.suspend(self.id, type(exc).__name__ + ": " + str(exc)[:500])
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            write_json(self.output / "failure.json", {"error": type(exc).__name__ + ": " + str(exc), "goal2_complete": False})
            self.checkpoint("STOPPED_WITH_ACTION_REQUIRED")
            raise
        finally:
            self.controller.close()


def _ticket_binding(root: Path, readiness_path: Path) -> dict:
    """Revalidate inputs without constructing a worker or dispatching a task."""
    readiness_path.relative_to(root)
    ready = json.loads(readiness_path.read_text(encoding="utf-8"))
    manifest_path = (root / ready["development_manifest_path"]).resolve()
    baseline_path = (root / ready["baseline_run_path"] / "summary.json").resolve()
    if manifest_path.parent != root / "data/manifests":
        raise ValueError("Development manifest must be in the manifest directory")
    if baseline_path.parent.parent != root / "runs" or not baseline_path.parent.name.startswith("baseline"):
        raise ValueError("Readiness must reference a development baseline run")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _assert_campaign_inputs(root, ready, manifest_path, manifest, baseline_path)
    pilot = json.loads((root / "configs/pilot.json").read_text(encoding="utf-8"))
    return {"readiness_path": readiness_path.relative_to(root).as_posix(),
            "readiness_sha256": sha256_file(readiness_path), "pilot_name": pilot["name"],
            **{key: ready[key] for key in ("code_sha256", "dataset_sha256", "development_manifest_path",
                "development_manifest_sha256", "evaluation_policy_sha256", "pilot_policy_sha256")}}


def _ticket_directory(root: Path, ticket: dict, binding: dict | None = None) -> Path:
    if ticket.get("ticket_version") != "offline-campaign-ticket-v1" or (binding is not None and ticket.get("binding") != binding):
        raise ValueError("One-use pilot ticket input binding differs; explicit failure audit required")
    relative = ticket["campaign_path"]
    output = (root / relative).resolve()
    if (output.parent != root / "runs/campaigns" or output.name != ticket["campaign_id"]
        or relative != output.relative_to(root).as_posix()):
        raise ValueError("One-use pilot ticket campaign path differs")
    return output


def _completed_ticket_result(root: Path, ticket: dict, binding: dict) -> dict:
    if ticket.get("ticket_version") != "offline-campaign-ticket-v1" or ticket.get("binding") != binding:
        raise ValueError("One-use pilot ticket input binding differs; explicit failure audit required")
    if ticket.get("status") != "COMPLETED":
        raise ValueError("One-use pilot ticket is active or failed; explicit failure audit required, no automatic new campaign")
    output = _ticket_directory(root, ticket, binding)
    expected = {output / name for name in ("summary.json", "ledger.json", "candidate_register.json")}
    records = ticket.get("artifacts", [])
    paths = [(root / item["path"]).resolve() for item in records]
    if len(paths) != 3 or set(paths) != expected:
        raise ValueError("Completed pilot ticket artifact inventory differs")
    for path, item in zip(paths, records):
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError("Completed pilot artifact changed: " + path.name)
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if (report.get("status") != "RESEARCH_CYCLE_COMPLETE" or report.get("campaign_id") != ticket["campaign_id"]
        or report.get("readiness_sha256") != binding["readiness_sha256"]
        or report.get("pilot_policy_sha256") != binding["pilot_policy_sha256"]):
        raise ValueError("Completed pilot summary binding differs")
    return {"path": str(output), **report}


def campaign_status(root: Path) -> dict:
    """Read-only runtime status, including failures and all spent reservations."""
    root = root.resolve()
    path = root / "data/manifests/offline_campaign_ticket.json"
    if not path.exists():
        return {"status": "NOT_STARTED", "goal2_complete": False}
    ticket = json.loads(path.read_text(encoding="utf-8"))
    output = _ticket_directory(root, ticket)
    with closing(sqlite3.connect((output / "controller.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (ticket["campaign_id"],)).fetchone()
        if not campaign:
            raise ValueError("Campaign ticket has no registered campaign")
        spent, reserved = ({name: 0 for name in ("tokens", "compute_seconds", "paid_micros", "experiments")} for _ in range(2))
        for row in db.execute("SELECT state,actual,reserved FROM attempts WHERE campaign_id=?", (ticket["campaign_id"],)):
            target = reserved if row["state"] == "RUNNING" else spent
            for name, amount in json.loads(row["reserved"] if row["state"] == "RUNNING" else row["actual"]).items():
                target[name] += amount
        return {"status": campaign["state"], "ticket_status": ticket["status"], "campaign_id": ticket["campaign_id"],
                "path": str(output), "created_at_unix": campaign["created"], "limits": json.loads(campaign["limits"]),
                "spent": spent, "reserved": reserved, "pause_requested": (output / "pause-request.json").exists(),
                "goal2_complete": False}


def request_pause(root: Path) -> dict:
    """Request a clean stop after the current bounded task; never kill a child."""
    root = root.resolve()
    ticket = json.loads((root / "data/manifests/offline_campaign_ticket.json").read_text(encoding="utf-8"))
    output = _ticket_directory(root, ticket)
    if ticket["status"] == "ACTIVE":
        write_json(output / "pause-request.json", {"campaign_id": ticket["campaign_id"],
                   "requested_at_utc": datetime.now(timezone.utc).isoformat()})
    return campaign_status(root)


def run_campaign(root: Path, readiness_path: Path, *, resume=False, review_interrupted=False) -> dict:
    """One pilot identity. Explicit resume replays only verified completed steps."""
    root, readiness_path = root.resolve(), readiness_path.resolve()
    with job_lock(root / "data/offline_campaign.lock"):
        ticket_path = root / "data/manifests/offline_campaign_ticket.json"
        binding = _ticket_binding(root, readiness_path)
        if ticket_path.exists():
            ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
            _ticket_directory(root, ticket, binding)
            if ticket.get("status") not in {"ACTIVE", "PAUSED", "FAILED", "COMPLETED"}:
                raise ValueError("Unknown one-use ticket state")
            if ticket["status"] == "COMPLETED" or not resume:
                return _completed_ticket_result(root, ticket, binding)
            if ticket["status"] != "PAUSED" and not review_interrupted:
                raise ValueError("Interrupted campaign requires explicit recovery review")
            campaign = Campaign(root, readiness_path, ticket=ticket, review_interrupted=review_interrupted)
            try:
                ticket.update(status="ACTIVE", resumed_at_utc=datetime.now(timezone.utc).isoformat())
                write_json(ticket_path, ticket)
            except BaseException:
                campaign.controller.suspend(campaign.id, "Could not persist resumed ticket; review required")
                campaign.controller.close()
                raise
        else:
            if resume:
                raise ValueError("No existing campaign to resume")
            campaign = Campaign(root, readiness_path)
            ticket = {"ticket_version": "offline-campaign-ticket-v1", "status": "ACTIVE",
                      "campaign_id": campaign.id, "campaign_path": campaign.prefix, "binding": binding,
                      "created_at_utc": datetime.now(timezone.utc).isoformat()}
        # Exclusive creation is the durable before-dispatch boundary. A killed
        # process leaves ACTIVE, which blocks another campaign rather than reset.
        try:
            if not resume:
                ticket_path.parent.mkdir(parents=True, exist_ok=True)
                with ticket_path.open("x", encoding="utf-8") as stream:
                    json.dump(ticket, stream, indent=2, sort_keys=True, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        except BaseException:
            campaign.controller.close()
            raise
        try:
            result = campaign.run()
            if result["status"] == "PAUSED":
                ticket.update(status="PAUSED", paused_at_utc=datetime.now(timezone.utc).isoformat())
                write_json(ticket_path, ticket)
                return result
            ticket.update(status="COMPLETED", completed_at_utc=datetime.now(timezone.utc).isoformat(),
                          artifacts=inventory(root, [campaign.output / name for name in
                              ("summary.json", "ledger.json", "candidate_register.json")]))
            write_json(ticket_path, ticket)
            return result
        except BaseException as exc:
            # Keyboard interrupts and terminations that permit cleanup consume
            # the same ticket; hard kills leave the already durable ACTIVE state.
            ticket.update(status="FAILED", failed_at_utc=datetime.now(timezone.utc).isoformat(),
                          error=type(exc).__name__ + ": " + str(exc)[:500])
            write_json(ticket_path, ticket)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--action", choices=("start", "status", "pause", "stop", "resume"), default="start")
    parser.add_argument("--review-interrupted", action="store_true",
                        help="Explicitly acknowledge unknown attempts, their full charges and bounded retry; never resets budgets")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    pilot_path = root / "configs/pilot.json"
    if pilot_path.is_file():
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        if pilot.get("architecture_version") == 2:
            from .campaign_v2 import main as iterative_main
            return iterative_main(argv)
    if args.review_interrupted and args.action != "resume":
        parser.error("--review-interrupted is only valid with --action resume")
    result = (campaign_status(root) if args.action == "status" else request_pause(root) if args.action in {"pause", "stop"} else
              run_campaign(root, args.readiness or root / "data/manifests/readiness.json", resume=args.action == "resume",
                           review_interrupted=args.review_interrupted))
    print(json.dumps(result, indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
