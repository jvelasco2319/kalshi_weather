"""Evidence-driven iterative KLAX research campaign, architecture V2.

This module preserves the V1 campaign as an immutable historical implementation
and supplies the new default research loop.  Every run is finite.  A profitable
development backtest can nominate one frozen candidate; it cannot authorize
trading or justify searching beyond the registered budget.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

from .campaign import Campaign, CampaignPaused, _ticket_binding, assess_development_gate
from .controller import ControllerError
from .local_backend import PROTOCOL, build_any_prompt, parse_any_response
from .pipeline import job_lock
from .provenance import inventory, sha256_file, write_json
from .research_plan import (
    COLONIES,
    STAGES,
    ResearchPlan,
    chronological_folds,
    combine_plans,
    compile_plan,
    fold_diagnostics,
    make_plan,
    seed_plans,
    stage_gate,
)
from .research_protocol import PROTOCOL_V2, validate_research_packet


COLONY_INDEX = {name: index + 1 for index, name in enumerate(COLONIES)}
DEFAULT_STAGE = {
    "forecast_ensemble": "forecast_skill",
    "lax_meteorology": "forecast_skill",
    "observations_measurement": "forecast_skill",
    "probability_calibration": "probability_calibration",
    "market_execution": "economic_simulation",
    "adversarial_alternatives": "forecast_skill",
}


def _summary(plan: ResearchPlan, result: dict, folds: list[dict]) -> str:
    scores = result["forecast_scores"]
    economic = result["historical_assumed_fill"]
    fold_returns = [row["capital_weighted_return"] for row in folds if row["capital_weighted_return"] is not None]
    return (
        f"V2 plan {plan.identity[:12]}: colony={plan.colony}, stage={plan.stage}, "
        f"GFS={plan.gfs_weight}, bias={plan.bias_operator}, spread={plan.spread_operator}, "
        f"scale={plan.spread_scale}, disagreement={plan.disagreement_coefficient}, "
        f"entry_threshold={plan.entry_threshold}, sides={plan.allowed_sides}. "
        f"Development CRPS={scores['gaussian_crps_f']:.6g}, Brier={scores['brier']:.6g}, "
        f"entries={economic['trade_count']}, ROI={economic.get('capital_weighted_return')}, "
        f"fold_ROI={fold_returns}. Assumed hourly fills; protected final unseen."
    )


def _return_value(row: dict) -> float:
    value = row["historical_assumed_fill"].get("capital_weighted_return")
    return -999.0 if value is None else float(value)


def _counts(total: int, allocation: dict) -> dict:
    names = ("deepen_supported", "cross_colony_combinations", "independent_alternatives", "adversarial_replication")
    if set(allocation) != set(names) or not math.isclose(sum(float(allocation[name]) for name in names), 1.0, abs_tol=1e-9):
        raise ValueError("V2 allocation weights must contain the four categories and sum to one")
    values = {name: int(total * float(allocation[name])) for name in names}
    for name in names:
        if total >= len(names) and values[name] == 0:
            values[name] = 1
    while sum(values.values()) < total:
        name = max(names, key=lambda item: float(allocation[item]) - values[item] / total)
        values[name] += 1
    while sum(values.values()) > total:
        name = max((item for item in names if values[item] > 1), key=lambda item: values[item], default=None)
        if name is None:
            break
        values[name] -= 1
    return values


def compact_research_packet(packet: dict, limits) -> dict:
    """Fit a curated V2 packet within both transport and context bounds.

    The baseline/anchor evidence stays visible while older intermediate rows
    are removed before recent rows.  Seeds and evidence are data only, so this
    compaction cannot change a previously evaluated result or spend a trial.
    """
    evidence = [dict(item) for item in packet["evidence"]]
    seeds = [dict(item) for item in packet["seed_plans"]]
    profiles = (
        (12, 12, 900), (12, 12, 650), (12, 12, 400), (12, 12, 250),
        (10, 12, 250), (8, 12, 250), (8, 10, 220), (8, 8, 200),
        (6, 8, 180), (6, 6, 160), (4, 6, 140), (4, 4, 120),
        (2, 2, 100), (1, 1, 80), (1, 0, 80),
    )
    last_error = None
    for evidence_limit, seed_limit, summary_limit in profiles:
        if len(evidence) <= evidence_limit:
            selected = evidence
        elif evidence_limit == 1:
            selected = [evidence[-1]]
        else:
            selected = [evidence[0], *evidence[-(evidence_limit - 1):]]
        candidate = {**packet,
            "evidence": [{**item, "summary": item["summary"][:summary_limit]} for item in selected],
            "seed_plans": seeds[:seed_limit],
        }
        try:
            candidate = validate_research_packet(candidate, max_bytes=limits.max_packet_bytes)
            build_any_prompt(candidate, limits)
            return candidate
        except ValueError as exc:
            last_error = exc
    raise ValueError("V2 research packet cannot fit the registered finite context") from last_error


class IterativeCampaign(Campaign):
    ORCHESTRATION_VERSION = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.pilot.get("architecture_version") != 2:
            self.controller.close()
            raise ValueError("V2 campaign requires architecture_version=2")
        if self.ready.get("architecture_version") != 2:
            self.controller.close()
            raise ValueError("V2 campaign requires a matching V2 readiness attestation")
        self.pending_plans: list[dict] = []
        self.pending_questions: list[dict] = []
        self.current_epoch = 1
        self.empty_epochs = 0
        self.coverage = {
            "proposed": 0, "compiled": 0, "executed": 0, "rejected": 0,
            "duplicates": 0, "combined": 0, "replicated": 0, "falsified": 0,
            "targeted_requests": 0, "by_colony": {}, "by_epoch": {},
        }
        state_path = self.output / "v2-state.json"
        if kwargs.get("ticket") and state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.pending_plans = state["pending_plans"]
            self.pending_questions = state["pending_questions"]
            self.current_epoch = state["current_epoch"]
            self.empty_epochs = state["empty_epochs"]
            self.coverage = state["coverage"]
            self.board = state["board"]
            self.candidates = state["candidates"]
            self.tested = set(state["tested"])
            rows = self.controller.db.execute("SELECT id FROM tasks WHERE campaign_id=?", (self.id,)).fetchall()
            self.counter = max((int(row["id"].split("-")[-1]) for row in rows), default=0)

    def save_v2_state(self) -> None:
        write_json(self.output / "v2-state.json", {
            "architecture_version": 2,
            "campaign_id": self.id,
            "current_epoch": self.current_epoch,
            "empty_epochs": self.empty_epochs,
            "pending_plans": self.pending_plans,
            "pending_questions": self.pending_questions,
            "coverage": self.coverage,
            "board": self.board,
            "candidates": self.candidates,
            "tested": sorted(self.tested),
        })

    def boundary(self):
        self.save_v2_state()
        super().boundary()

    def _coverage(self, key: str, *, colony: str | None = None, epoch: int | None = None, amount: int = 1) -> None:
        self.coverage[key] += amount
        if colony:
            row = self.coverage["by_colony"].setdefault(colony, {})
            row[key] = row.get(key, 0) + amount
        if epoch:
            row = self.coverage["by_epoch"].setdefault(str(epoch), {})
            row[key] = row.get(key, 0) + amount

    def model_task_v2(self, role: str, epoch: int, colony: str, stage: str, question: str,
                      evidence: list[dict], plans: list[ResearchPlan],
                      parent_hypothesis_ids=(), hypothesis: str | None = None):
        self.assert_inputs()
        selected = list({item["evidence_id"]: dict(item) for item in evidence}.values())[-12:]
        seeds = []
        seen = set()
        for plan in plans:
            if plan.colony != colony or plan.stage != stage:
                plan = ResearchPlan.from_dict({**plan.to_dict(), "colony": colony, "stage": stage})
            # A follow-up can be routed into a packet with fewer visible
            # parents than the plan that suggested it.  Expose only lineage
            # that is actually available in this packet.
            available = tuple(parent for parent in plan.parent_hypothesis_ids
                              if parent in parent_hypothesis_ids)
            if available != plan.parent_hypothesis_ids:
                plan = ResearchPlan.from_dict({**plan.to_dict(),
                    "parent_hypothesis_ids": list(available)})
            if plan.identity not in seen:
                seeds.append(plan.to_dict())
                seen.add(plan.identity)
            if len(seeds) == 12:
                break
        packet = {
            "protocol": PROTOCOL_V2,
            "task_id": f"task-{self.counter + 1:03d}",
            "campaign_id": self.id,
            "role": role,
            "scope": "development_only",
            "synthetic": False,
            "question": question,
            **{key: self.ready[key] for key in ("code_sha256", "dataset_sha256", "evaluation_policy_sha256")},
            "evidence": selected,
            "seed_plans": seeds,
            "colony": colony,
            "stage": stage,
            "parent_hypothesis_ids": list(parent_hypothesis_ids),
        }
        packet = compact_research_packet(packet, self.worker.limits)
        selected = packet["evidence"]
        task = self.task(role, epoch, COLONY_INDEX[colony], question,
                         [item["evidence_id"] for item in selected], hypothesis=hypothesis, model=True)
        saved = self.saved_result(task)
        if saved:
            record, item = saved
            response = parse_any_response(json.dumps(record.get("response", record)), packet, self.worker.limits)
            item["summary"] = response["rationale"][:1200]
            return response, item, task.task_id
        if self.calls >= self.pilot["max_local_model_calls"]:
            raise ValueError("Local model call budget exhausted")
        self.checkpoint(f"V2_{role.upper()}_{task.task_id}")
        record = self.worker.dispatch_one(self.controller, self.id, f"{role}-{colony}", task.task_id, packet)
        self.refresh_calls()
        if record is None:
            raise RuntimeError("Submitted V2 worker task was not claimed")
        rows = self.controller.export_ledger(self.id)["evidence"]
        saved_rows = [item for item in rows if item["task_id"] == task.task_id]
        if len(saved_rows) != 1:
            raise ValueError("V2 proposal evidence inventory differs")
        item = saved_rows[0]
        evidence_item = {"evidence_id": item["id"], "scope": "selection",
                         "summary": record["response"]["rationale"][:1200],
                         "artifact_sha256": item["record"]["sha256"]}
        return record["response"], evidence_item, task.task_id

    def handle_response(self, response: dict, evidence: dict, task_id: str, *, epoch: int, colony: str) -> ResearchPlan | None:
        for index, request in enumerate(response["requested_checks"]):
            kind = {"data_requirement": "REQUEST_DATA", "falsification": "REQUEST_FALSIFICATION"}.get(
                request["kind"], "REQUEST_CHECK")
            message_id = f"{task_id}-request-{index + 1}"
            self.controller.add_message(self.id, message_id, message_id, sender=f"{colony}-{task_id}",
                recipient=request["target_colony"], kind=kind, text=request["question"],
                evidence_ids=[evidence["evidence_id"]])
            self.pending_questions.append({**request, "evidence_id": evidence["evidence_id"], "epoch": epoch})
            self._coverage("targeted_requests", colony=colony, epoch=epoch)
        if response["action"] == "propose":
            plan = ResearchPlan.from_dict(response["plan"])
            self._coverage("proposed", colony=colony, epoch=epoch)
            return plan
        message_id = task_id + "-" + response["action"]
        self.controller.add_message(self.id, message_id, message_id, sender=f"{colony}-{task_id}",
            recipient="local_group", kind="BLOCKER", text=response["rationale"],
            evidence_ids=[evidence["evidence_id"]])
        return None

    def evaluate_plan(self, plan: ResearchPlan, epoch: int):
        identity = plan.identity
        if identity in self.tested:
            self._coverage("duplicates", colony=plan.colony, epoch=epoch)
            return None
        compile_plan(plan)
        self._coverage("compiled", colony=plan.colony, epoch=epoch)
        self.tested.add(identity)
        hid = "hyp-v2-" + identity[:16]
        self.controller.add_hypothesis(self.id, hid, hid,
            claim=f"Typed V2 plan from {plan.colony} may improve {plan.stage}",
            mechanism=(f"Bounded operators: blend={plan.gfs_weight}, bias={plan.bias_operator}, "
                       f"spread={plan.spread_operator}, threshold={plan.entry_threshold}, sides={plan.allowed_sides}"),
            falsification="Fails rolling-fold, stage, replication, critic, cost or final-promotion gates",
            planned_comparison="Registered reference on identical development dates",
            parent_ids=list(plan.parent_hypothesis_ids))
        parent_evidence = [item for item in self.board[-12:]]
        if plan.parent_hypothesis_ids:
            self.controller.record_decision(self.id, hid + "-combined", hid + "-combined", kind="COMBINE",
                rationale="Cross-pollinated typed follow-up with explicit parent lineage",
                evidence_ids=[item["evidence_id"] for item in parent_evidence[:4]])
            self._coverage("combined", colony=plan.colony, epoch=epoch)
        task = self.task("implementer", epoch, COLONY_INDEX[plan.colony],
                         "Compile and run the validated V2 research plan",
                         [item["evidence_id"] for item in parent_evidence], hypothesis=hid, experiment=True)
        directory = self.root / task.allowed_outputs[0]
        plan_path = directory / "research-plan.json"
        saved = self.saved_result(task)
        if saved:
            result, evidence = saved
        else:
            claim = self.controller.claim_task(self.id, "v2-fixed-implementation", task_id=task.task_id)
            write_json(plan_path, plan.to_dict())
            self.checkpoint("V2_OFFLINE_EXPERIMENT_" + hid)
            try:
                result, elapsed = self.fixed_child([
                    "run", "--root", str(self.root), "--manifest", str(self.manifest_path),
                    "--spec-file", str(plan_path), "--output-root", str(directory / "experiment")],
                    directory / "run.log")
                result_path = directory / "registered-result.json"
                write_json(result_path, result)
                metrics = {key: float(value) for key, value in result["forecast_scores"].items()
                           if type(value) in (int, float) and math.isfinite(value)}
                experiment = {"experiment_id": result["experiment_id"], "code_sha256": self.ready["code_sha256"],
                    "dataset_sha256": self.ready["dataset_sha256"], "parameters": plan.to_dict(),
                    "metrics": metrics, "synthetic": False}
                evidence = self.artifact_result(task, claim, result_path, {}, elapsed=elapsed, experiment=experiment)
            except Exception as exc:
                self.controller.fail_task(task.task_id, claim["lease_token"], str(exc)[:300], actual_usage=None)
                raise
        experiment_folder = Path(result["path"])
        days = json.loads((experiment_folder / "selection_eligibility.json").read_text(encoding="utf-8"))["eligible_days"]
        folds = chronological_folds(days, self.pilot["rolling_development_folds"])
        diagnostics = fold_diagnostics(
            json.loads((experiment_folder / "daywise_scores.json").read_text(encoding="utf-8")),
            json.loads((experiment_folder / "primary_ledger.json").read_text(encoding="utf-8")), folds)
        evidence["summary"] = _summary(plan, result, diagnostics)
        self.controller.record_decision(self.id, hid + "-tested", hid + "-tested", kind="GATE",
            rationale="Deterministic V2 development experiment completed", evidence_ids=[evidence["evidence_id"]],
            hypothesis_id=hid, target_state="TESTED")
        self.controller.add_message(self.id, hid + "-replication", hid + "-replication",
            sender="v2-fixed-implementation", recipient="adversarial_alternatives", kind="REQUEST_REPLICATION",
            text="Independently verify predictions, opportunity selection and accounting",
            evidence_ids=[evidence["evidence_id"]])
        critic, critic_evidence, critic_task = self.model_task_v2(
            "critic", epoch, "adversarial_alternatives", plan.stage,
            "Challenge this exact evaluated plan. Propose the same executable plan only if the evidence merits continuation; "
            "otherwise reject, abstain, or request a targeted falsification. Distinguish fitting in 2024 from development evaluation in 2025.",
            [self.board[0], evidence], [ResearchPlan.from_dict({**plan.to_dict(),
                "colony": "adversarial_alternatives"})], [hid], hid)
        self.handle_response(critic, critic_evidence, critic_task, epoch=epoch, colony="adversarial_alternatives")
        critic_allowed = False
        if critic["action"] == "propose":
            proposed = ResearchPlan.from_dict(critic["plan"])
            critic_allowed = proposed.identity == plan.identity
        task = self.task("replicator", epoch, COLONY_INDEX["adversarial_alternatives"],
                         "Independently recompute the V2 saved experiment", [evidence["evidence_id"]], hid)
        replica_path = self.root / task.allowed_outputs[0] / "replication.json"
        saved = self.saved_result(task)
        if saved:
            replica, replica_evidence = saved
        else:
            claim = self.controller.claim_task(self.id, "v2-independent-replicator", task_id=task.task_id)
            try:
                replica, elapsed = self.fixed_child([
                    "verify", "--root", str(self.root), "--manifest", str(self.manifest_path),
                    "--run-directory", result["path"], "--output", str(replica_path)],
                    replica_path.with_suffix(".log"))
                replica_evidence = self.artifact_result(task, claim, replica_path, replica, elapsed=elapsed)
            except Exception as exc:
                self.controller.fail_task(task.task_id, claim["lease_token"], str(exc)[:300], actual_usage=None)
                raise
        independently_verified = replica.get("status") == "PASS" and replica.get("primary_candidate_selection_verified") is True
        gates = {stage: stage_gate(stage, result, self.reference, diagnostics, self.pilot["stage_gates"],
                                   independently_verified=independently_verified, critic_allowed=critic_allowed)
                 for stage in STAGES}
        final_gate = assess_development_gate(result, self.reference, replica,
            {"action": "propose" if critic_allowed else critic["action"]}, self.pilot["development_gate"])
        refs = [evidence["evidence_id"], critic_evidence["evidence_id"], replica_evidence["evidence_id"]]
        if final_gate["passed"] and all(item["passed"] for item in gates.values()):
            for state in ("SUPPORTED", "REPLICATED", "CANDIDATE"):
                self.controller.record_decision(self.id, hid + "-" + state, hid + "-" + state, kind="GATE",
                    rationale="Passed every V2 stage and the registered final development gate",
                    evidence_ids=refs, hypothesis_id=hid, target_state=state)
        else:
            reasons = [reason for gate in gates.values() for reason in gate["reasons"]] + final_gate["reasons"]
            self.controller.record_decision(self.id, hid + "-rejected", hid + "-rejected", kind="REJECTION",
                rationale=", ".join(sorted(set(reasons))), evidence_ids=refs,
                hypothesis_id=hid, target_state="REJECTED")
            self._coverage("rejected", colony=plan.colony, epoch=epoch)
            if not critic_allowed:
                self._coverage("falsified", colony=plan.colony, epoch=epoch)
        self._coverage("executed", colony=plan.colony, epoch=epoch)
        if independently_verified:
            self._coverage("replicated", colony=plan.colony, epoch=epoch)
        record = {
            "hypothesis_id": hid,
            "candidate_id": identity,
            "plan": plan.to_dict(),
            "spec": asdict(compile_plan(plan).candidate),
            "parent_ids": list(plan.parent_hypothesis_ids),
            "experiment_path": result["path"],
            "experiment_id": result["experiment_id"],
            "stage_gates": gates,
            "gate": {**final_gate, "passed": final_gate["passed"] and all(item["passed"] for item in gates.values())},
            "rolling_folds": diagnostics,
            "forecast_scores": result["forecast_scores"],
            "historical_assumed_fill": result["historical_assumed_fill"],
            "critic": critic,
            "replication_path": str(replica_path),
            "evidence_ids": refs,
        }
        self.board.append(evidence)
        self.candidates.append(record)
        write_json(self.output / "candidate_register.json", self.candidates)
        self.boundary()
        return record

    def _ranked(self) -> list[dict]:
        return sorted(self.candidates, key=lambda row: (
            -sum(1 for value in row["stage_gates"].values() if value["passed"]),
            row["forecast_scores"]["gaussian_crps_f"],
            row["forecast_scores"]["brier"],
            -_return_value(row),
            row["candidate_id"],
        ))

    def neighbor_plans(self, row: dict, epoch: int) -> list[ResearchPlan]:
        base = ResearchPlan.from_dict(row["plan"])
        parent = (row["hypothesis_id"],)
        plans = []
        for weight in (0.0, .25, .5, .75, 1.0):
            plans.append(make_plan(colony=base.colony, stage=base.stage, weight=weight,
                bias=base.bias_operator, spread=base.spread_operator, scale=base.spread_scale,
                coefficient=base.disagreement_coefficient, threshold=base.entry_threshold,
                sides=base.allowed_sides, parents=parent))
        for threshold in (.1, .15, .2, .3):
            for sides in ("BOTH", "YES", "NO"):
                plans.append(make_plan(colony="market_execution", stage="economic_simulation",
                    weight=base.gfs_weight, bias=base.bias_operator, spread=base.spread_operator,
                    scale=base.spread_scale, coefficient=base.disagreement_coefficient,
                    threshold=threshold, sides=sides, parents=parent))
        return [plan for plan in plans if plan.identity not in self.tested]

    def allocate(self, epoch: int, synthesized: list[ResearchPlan]) -> list[ResearchPlan]:
        total = self.pilot["max_experiments_per_epoch"]
        counts = _counts(total, self.pilot["allocation"])
        ranked = self._ranked()
        queue = []
        categories = {}

        def add(category: str, plans):
            for plan in plans:
                if len(categories.get(category, [])) >= counts[category]:
                    break
                if plan.identity in self.tested or any(existing.identity == plan.identity for existing in queue):
                    continue
                queue.append(plan)
                categories.setdefault(category, []).append(plan.identity)

        deepen = [plan for row in ranked[:4] for plan in self.neighbor_plans(row, epoch + 1)]
        add("deepen_supported", [*synthesized, *deepen])
        combinations = []
        if len(ranked) >= 2:
            forecast_parent = min(ranked, key=lambda row: row["forecast_scores"]["gaussian_crps_f"])
            alternatives = [row for row in ranked
                            if row["hypothesis_id"] != forecast_parent["hypothesis_id"]]
            market_rows = [row for row in alternatives if row["plan"]["colony"] == "market_execution"]
            market_parent = max(market_rows or alternatives, key=_return_value)
            combinations.append(combine_plans(ResearchPlan.from_dict(forecast_parent["plan"]),
                ResearchPlan.from_dict(market_parent["plan"]), colony="probability_calibration",
                stage="economic_simulation", parent_hypothesis_ids=(forecast_parent["hypothesis_id"], market_parent["hypothesis_id"])))
        add("cross_colony_combinations", combinations)
        alternatives = [plan for colony in COLONIES for plan in seed_plans(colony, DEFAULT_STAGE[colony])]
        add("independent_alternatives", alternatives)
        adversarial = seed_plans("adversarial_alternatives", "forecast_skill")
        add("adversarial_replication", adversarial)
        # Fill any unused slots from every untested seed without changing the
        # registered category allocation record.
        for plan in [*synthesized, *deepen, *combinations, *alternatives, *adversarial]:
            if len(queue) >= total:
                break
            if plan.identity not in self.tested and all(item.identity != plan.identity for item in queue):
                queue.append(plan)
        evidence_ids = [item["evidence_id"] for item in self.board[-8:]]
        if evidence_ids:
            did = f"allocation-{epoch}"
            self.controller.record_decision(self.id, did, did, kind="ALLOCATION",
                rationale=json.dumps({"weights": self.pilot["allocation"], "selected": categories}, sort_keys=True),
                evidence_ids=evidence_ids)
            decision_kinds = {
                "cross_colony_combinations": "COMBINE",
                "independent_alternatives": "CHALLENGE",
                "adversarial_replication": "REPLICATE",
            }
            deepen = categories.get("deepen_supported", [])
            for index, identity in enumerate(deepen):
                kind = "CONTINUE" if index == 0 else "FORK"
                name = f"allocation-{epoch}-deepen-{index + 1}"
                self.controller.record_decision(self.id, name, name, kind=kind,
                    rationale=f"Allocate a bounded slot to executable plan {identity}",
                    evidence_ids=evidence_ids)
            for category, kind in decision_kinds.items():
                for index, identity in enumerate(categories.get(category, [])):
                    name = f"allocation-{epoch}-{category}-{index + 1}"
                    self.controller.record_decision(self.id, name, name, kind=kind,
                        rationale=f"Allocate a bounded {category} slot to executable plan {identity}",
                        evidence_ids=evidence_ids)
        return queue[:total]

    def synthesis(self, epoch: int, epoch_rows: list[dict]) -> list[ResearchPlan]:
        followups = []
        for colony in COLONIES:
            rows = [row for row in epoch_rows if row["plan"]["colony"] == colony]
            if not rows:
                continue
            parents = [row["hypothesis_id"] for row in self._ranked()[:4]]
            seeds = [plan for row in rows for plan in self.neighbor_plans(row, epoch + 1)][:12]
            response, evidence, task_id = self.model_task_v2(
                "synthesizer", epoch, colony, DEFAULT_STAGE[colony],
                "Synthesize this colony's positive and negative evidence. Propose one materially new typed follow-up, "
                "or request a targeted check. Do not repeat an evaluated implementation.",
                [self.board[0], *self.board[-10:]], seeds, parents)
            plan = self.handle_response(response, evidence, task_id, epoch=epoch, colony=colony)
            self.board.append(evidence)
            if plan and plan.identity not in self.tested:
                followups.append(plan)
            did = f"colony-synthesis-{epoch}-{COLONY_INDEX[colony]}"
            self.controller.record_decision(self.id, did, did, kind="SYNTHESIS",
                rationale=response["rationale"], evidence_ids=[evidence["evidence_id"]])
        ranked = self._ranked()
        if ranked:
            parents = [row["hypothesis_id"] for row in ranked[:4]]
            seeds = []
            if len(ranked) >= 2:
                seeds.append(combine_plans(ResearchPlan.from_dict(ranked[0]["plan"]),
                    ResearchPlan.from_dict(ranked[1]["plan"]), colony="adversarial_alternatives",
                    stage="economic_simulation", parent_hypothesis_ids=parents[:2]))
            seeds.extend(followups)
            response, evidence, task_id = self.model_task_v2(
                "synthesizer", epoch, "adversarial_alternatives", "economic_simulation",
                "Cross-pollinate the strongest and most contradictory findings across colonies. Propose one executable "
                "combined plan or request the falsification that should control the next allocation.",
                [self.board[0], *self.board[-12:]], seeds, parents)
            plan = self.handle_response(response, evidence, task_id, epoch=epoch, colony="adversarial_alternatives")
            self.board.append(evidence)
            if plan and plan.identity not in self.tested:
                followups.insert(0, plan)
            did = f"global-synthesis-{epoch}"
            self.controller.record_decision(self.id, did, did, kind="SYNTHESIS",
                rationale=response["rationale"], evidence_ids=[evidence["evidence_id"]])
        return followups

    def initial_proposals(self, epoch: int) -> list[ResearchPlan]:
        plans = []
        used = set()
        for colony in COLONIES:
            stage = DEFAULT_STAGE[colony]
            seeds = [plan for plan in seed_plans(colony, stage) if plan.identity not in self.tested]
            host_seed = next((plan for plan in seeds if plan.identity not in used), None)
            if host_seed:
                plans.append(host_seed)
                used.add(host_seed.identity)
            for worker_index in range(self.pilot["proposals_per_colony_epoch_one"]):
                offset = worker_index * 4
                packet_seeds = (seeds[offset:] + seeds[:offset])[:12]
                response, evidence, task_id = self.model_task_v2(
                    "explorer", epoch, colony, stage,
                    f"Independent discovery context {worker_index + 1} for {colony}. Propose a falsifiable typed plan "
                    "without seeing a declared winner. You may compose operator values beyond the supplied seeds.",
                    [self.board[0]], packet_seeds)
                plan = self.handle_response(response, evidence, task_id, epoch=epoch, colony=colony)
                if plan:
                    plans.append(plan)
        return plans

    def later_proposals(self, epoch: int) -> list[ResearchPlan]:
        plans = [ResearchPlan.from_dict(value) for value in self.pending_plans]
        self.pending_plans = []
        for colony in COLONIES:
            stage = DEFAULT_STAGE[colony]
            seeds = [plan for plan in plans if plan.colony == colony]
            if not seeds:
                seeds = [plan for plan in seed_plans(colony, stage) if plan.identity not in self.tested][:12]
            parents = [row["hypothesis_id"] for row in self._ranked()[:4]]
            related = [item for item in self.pending_questions if item["target_colony"] == colony]
            question_suffix = " ".join(item["question"] for item in related[-2:])
            response, evidence, task_id = self.model_task_v2(
                "explorer", epoch, colony, stage,
                "Use prior evidence and allocation to propose the next executable experiment. Preserve failed evidence; "
                "combine only when the parent mechanism is explicit. " + question_suffix,
                [self.board[0], *self.board[-10:]], seeds, parents)
            plan = self.handle_response(response, evidence, task_id, epoch=epoch, colony=colony)
            if plan:
                plans.append(plan)
        self.pending_questions = []
        return plans

    def run(self):
        try:
            if self.controller.status(self.id)["state"] == "RESEARCH_CYCLE_COMPLETE":
                report = json.loads((self.output / "summary.json").read_text(encoding="utf-8"))
                if report.get("architecture_version") != 2:
                    raise ValueError("Completed campaign is not V2")
                return {"path": str(self.output), **report}
            if not self.board:
                task = self.task("auditor", 1, 1, "Register V2 baseline, colonies, stage gates and rolling-fold policy")
                path = self.root / task.allowed_outputs[0] / "baseline-context.json"
                context = {
                    "architecture_version": 2,
                    "best_baseline_by_CRPS": self.reference_name,
                    "scores": self.reference["forecast_scores"],
                    "colonies": COLONIES,
                    "stage_gates": self.pilot["stage_gates"],
                    "allocation": self.pilot["allocation"],
                    "rolling_development_folds": self.pilot["rolling_development_folds"],
                    "limitations": ["Development evidence only", "Hourly assumed fills", "Protected final unseen"],
                }
                saved = self.saved_result(task)
                if saved:
                    _, evidence = saved
                else:
                    claim = self.controller.claim_task(self.id, "v2-baseline-curator", task_id=task.task_id)
                    write_json(path, context)
                    evidence = self.artifact_result(task, claim, path, context)
                self.board.append(evidence)
                self.boundary()
            while self.current_epoch <= self.pilot["max_epochs"]:
                self.checkpoint(f"V2_EPOCH_{self.current_epoch}_DISCOVERY")
                proposed = (self.initial_proposals(self.current_epoch) if self.current_epoch == 1
                            else self.later_proposals(self.current_epoch))
                unique = []
                seen = set()
                for plan in proposed:
                    if plan.identity in seen or plan.identity in self.tested:
                        self._coverage("duplicates", colony=plan.colony, epoch=self.current_epoch)
                        continue
                    seen.add(plan.identity)
                    unique.append(plan)
                remaining = self.pilot["max_experiments"] - len(self.tested)
                epoch_rows = []
                for plan in unique[:min(self.pilot["max_experiments_per_epoch"], remaining)]:
                    row = self.evaluate_plan(plan, self.current_epoch)
                    if row:
                        epoch_rows.append(row)
                qualifying = [row for row in epoch_rows if row["gate"]["passed"]]
                if qualifying:
                    self.coverage["by_epoch"].setdefault(str(self.current_epoch), {})["productive"] = True
                    evidence_ids = qualifying[0]["evidence_ids"]
                    decision_id = f"champion-stop-{self.current_epoch}"
                    self.controller.record_decision(self.id, decision_id, decision_id, kind="STOP",
                        rationale="A plan passed every registered development gate; freeze the ranked candidate set before protected evaluation",
                        evidence_ids=evidence_ids)
                    self.current_epoch += 1
                    self.boundary()
                    break
                if len(epoch_rows) >= self.pilot["minimum_experiments_per_productive_epoch"]:
                    self.empty_epochs = 0
                    followups = self.synthesis(self.current_epoch, epoch_rows)
                    queue = self.allocate(self.current_epoch, followups)
                    self.pending_plans = [plan.to_dict() for plan in queue]
                else:
                    self.empty_epochs += 1
                self.coverage["by_epoch"].setdefault(str(self.current_epoch), {})["productive"] = bool(epoch_rows)
                self.current_epoch += 1
                self.boundary()
                if len(self.tested) >= self.pilot["max_experiments"]:
                    self.controller.record_decision(self.id, "budget-stop", "budget-stop", kind="BUDGET_STOP",
                        rationale="Registered experiment budget exhausted", evidence_ids=[])
                    break
                if self.empty_epochs >= self.pilot["empty_epoch_patience"]:
                    evidence_ids = [item["evidence_id"] for item in self.board[-4:]]
                    self.controller.record_decision(self.id, "empty-stop", "empty-stop", kind="STOP",
                        rationale="Two consecutive epochs produced no novel executable experiment",
                        evidence_ids=evidence_ids)
                    break
            eligible = [row for row in self.candidates if row["gate"]["passed"]]
            eligible.sort(key=lambda row: (row["forecast_scores"]["gaussian_crps_f"],
                                           row["forecast_scores"]["brier"], row["candidate_id"]))
            champion = eligible[0] if eligible else None
            status = "RESEARCH_CYCLE_COMPLETE"
            conclusion = "DEVELOPMENT_CANDIDATE" if champion else "NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET"
            write_json(self.output / "candidate_register.json", self.candidates)
            write_json(self.output / "search_coverage.json", self.coverage)
            report = {
                "status": status,
                "architecture_version": 2,
                "campaign_id": self.id,
                "scientific_conclusion": conclusion,
                "candidate_count": len(self.candidates),
                "productive_epoch_count": sum(1 for value in self.coverage["by_epoch"].values() if value.get("productive")),
                "epochs_attempted": self.current_epoch - 1,
                "local_model_calls": self.calls,
                "reference_baseline": self.reference_name,
                "champion": champion,
                "search_coverage": self.coverage,
                "protected_final_evaluated": False,
                "goal1_complete": True,
                "goal2_complete": False,
                "readiness_sha256": sha256_file(self.readiness_path),
                "pilot_policy_sha256": self.ready["pilot_policy_sha256"],
                "stopping_rule": "Champion found, fixed budget exhausted, maximum epochs, or two empty epochs",
                "limitations": [
                    "Development selection only; a frozen candidate requires one protected evaluation",
                    "All local language roles use one model family in separate contexts",
                    "The typed V2 compiler currently executes Gaussian GFS/NBM plans and bounded entry policies",
                    "Missing intraday observations, cloud layers and historical depth remain explicit data requests",
                    "A negative bounded campaign is retained and does not authorize unbounded search",
                ],
            }
            write_json(self.output / "summary.json", report)
            self.controller.checkpoint(self.id, {
                "orchestration_version": 2,
                "binding": _ticket_binding(self.root, self.readiness_path),
                "completion_summary_sha256": sha256_file(self.output / "summary.json"),
                "candidate_register_sha256": sha256_file(self.output / "candidate_register.json"),
                "search_coverage_sha256": sha256_file(self.output / "search_coverage.json"),
            })
            self.controller.finish(self.id, "SUPPORTED_WITH_LIMITATIONS" if champion else "NO_IMPROVEMENT")
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            self.save_v2_state()
            self.checkpoint("RESEARCH_CYCLE_COMPLETE")
            return {"path": str(self.output), **report}
        except CampaignPaused:
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            return {"status": "PAUSED", "path": str(self.output), "campaign_id": self.id,
                    "architecture_version": 2, "goal2_complete": False,
                    "resources": self.controller.status(self.id)}
        except BaseException as exc:
            self.controller.suspend(self.id, type(exc).__name__ + ": " + str(exc)[:500])
            write_json(self.output / "ledger.json", self.controller.export_ledger(self.id))
            write_json(self.output / "failure.json", {"error": type(exc).__name__ + ": " + str(exc),
                                                       "architecture_version": 2, "goal2_complete": False})
            self.checkpoint("STOPPED_WITH_ACTION_REQUIRED")
            raise
        finally:
            self.controller.close()


def _ticket_path(root: Path) -> Path:
    return root / "data/manifests/offline_campaign_v2_ticket.json"


def _ticket_directory(root: Path, ticket: dict, binding: dict | None = None) -> Path:
    if ticket.get("ticket_version") != "offline-campaign-ticket-v2" or (binding is not None and ticket.get("binding") != binding):
        raise ValueError("V2 campaign ticket binding differs; explicit recovery review required")
    output = (root / ticket["campaign_path"]).resolve()
    if output.parent != root / "runs/campaigns" or output.name != ticket["campaign_id"]:
        raise ValueError("V2 campaign path differs")
    return output


def _completed(root: Path, ticket: dict, binding: dict) -> dict:
    if ticket.get("status") != "COMPLETED":
        raise ValueError("V2 campaign is unfinished")
    output = _ticket_directory(root, ticket, binding)
    names = ("summary.json", "ledger.json", "candidate_register.json", "search_coverage.json")
    expected = {output / name for name in names}
    records = ticket.get("artifacts", [])
    paths = {(root / item["path"]).resolve() for item in records}
    if paths != expected:
        raise ValueError("V2 completed artifact inventory differs")
    for item in records:
        path = (root / item["path"]).resolve()
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError("V2 completed artifact changed")
    report = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if report.get("architecture_version") != 2 or report.get("campaign_id") != ticket["campaign_id"]:
        raise ValueError("V2 completed summary differs")
    return {"path": str(output), **report}


def campaign_status_v2(root: Path) -> dict:
    root = root.resolve()
    path = _ticket_path(root)
    if not path.exists():
        return {"status": "NOT_STARTED", "architecture_version": 2, "goal2_complete": False}
    ticket = json.loads(path.read_text(encoding="utf-8"))
    return {"status": ticket["status"], "architecture_version": 2,
            "campaign_id": ticket["campaign_id"], "path": str(_ticket_directory(root, ticket)),
            "goal2_complete": False}


def request_pause_v2(root: Path) -> dict:
    root = root.resolve()
    ticket = json.loads(_ticket_path(root).read_text(encoding="utf-8"))
    output = _ticket_directory(root, ticket)
    if ticket["status"] == "ACTIVE":
        write_json(output / "pause-request.json", {"campaign_id": ticket["campaign_id"],
                   "requested_at_utc": datetime.now(timezone.utc).isoformat()})
    return campaign_status_v2(root)


def run_campaign_v2(root: Path, readiness_path: Path, *, resume=False, review_interrupted=False) -> dict:
    root, readiness_path = root.resolve(), readiness_path.resolve()
    with job_lock(root / "data/offline_campaign_v2.lock"):
        ticket_path = _ticket_path(root)
        binding = _ticket_binding(root, readiness_path)
        if ticket_path.exists():
            ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
            _ticket_directory(root, ticket, binding)
            if ticket["status"] == "COMPLETED":
                return _completed(root, ticket, binding)
            if not resume:
                raise ValueError("An unfinished V2 ticket requires explicit resume")
            if ticket["status"] != "PAUSED" and not review_interrupted:
                raise ValueError("Interrupted V2 campaign requires explicit recovery review")
            campaign = IterativeCampaign(root, readiness_path, ticket=ticket, review_interrupted=review_interrupted)
            ticket.update(status="ACTIVE", resumed_at_utc=datetime.now(timezone.utc).isoformat())
            write_json(ticket_path, ticket)
        else:
            if resume:
                raise ValueError("No V2 campaign exists to resume")
            campaign = IterativeCampaign(root, readiness_path)
            ticket = {"ticket_version": "offline-campaign-ticket-v2", "status": "ACTIVE",
                      "campaign_id": campaign.id, "campaign_path": campaign.prefix, "binding": binding,
                      "created_at_utc": datetime.now(timezone.utc).isoformat()}
            ticket_path.parent.mkdir(parents=True, exist_ok=True)
            with ticket_path.open("x", encoding="utf-8") as stream:
                json.dump(ticket, stream, indent=2, sort_keys=True, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        try:
            result = campaign.run()
            if result["status"] == "PAUSED":
                ticket.update(status="PAUSED", paused_at_utc=datetime.now(timezone.utc).isoformat())
                write_json(ticket_path, ticket)
                return result
            ticket.update(status="COMPLETED", completed_at_utc=datetime.now(timezone.utc).isoformat(),
                          artifacts=inventory(root, [campaign.output / name for name in
                              ("summary.json", "ledger.json", "candidate_register.json", "search_coverage.json")]))
            write_json(ticket_path, ticket)
            return result
        except BaseException as exc:
            ticket.update(status="FAILED", failed_at_utc=datetime.now(timezone.utc).isoformat(),
                          error=type(exc).__name__ + ": " + str(exc)[:500])
            write_json(ticket_path, ticket)
            raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded iterative KLAX research campaign V2")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--action", choices=("start", "status", "pause", "stop", "resume"), default="start")
    parser.add_argument("--review-interrupted", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.review_interrupted and args.action != "resume":
        parser.error("--review-interrupted is only valid with resume")
    if args.action == "status":
        result = campaign_status_v2(root)
    elif args.action in {"pause", "stop"}:
        result = request_pause_v2(root)
    else:
        result = run_campaign_v2(root, args.readiness or root / "data/manifests/readiness.json",
                                 resume=args.action == "resume", review_interrupted=args.review_interrupted)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
