"""Production V4 offline campaign runner.

This module is the only Start/Resume path used by the background controller.
It preserves the frozen V3 numerical gates while using the exact V4 plan and
13:30/15:00/18:00 data bundle.  Registered-plan nomination is deterministic;
the iterative behavior comes from the fixed colony allocator, evidence digest,
and independently verified follow-up rules.  It never reads protected-final
data and never creates live or paper orders.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from klax_lab.campaign_v3 import (
    CandidateEvaluationBundle, CandidateRecord, V3BudgetStop, V3CampaignEngine,
    V3CampaignError, V3EvaluationContext, V3IntegrityStop, V3ReadinessRefusal,
)
from klax_lab.orchestrator_v3 import (
    BoundedV3Orchestrator, COLONY_ORDER,
    _CampaignExecutionLease, _critic_review, _independent_replication,
    _plan_summary, _ranking_key, _read_artifacts,
)
from klax_lab.provenance import canonical_hash, sha256_file, write_json

from .execution_coverage import (
    COLONY_STAGE, EPOCH_SIZE, V4CoverageError, adaptive_followup_packet_v4,
    allocate_epoch_v4, build_cross_pollination_digest_v4,
    build_v4_execution_manifest, execution_factorial_plans_v4,
    load_ranked_v3_parent_records, load_v4_execution_registration,
    ModelTrackV4, robust_positive_milestone_v4, select_forecast_parent_v4,
    verify_cross_pollination_digest_v4,
)
from .orchestrator import _source_evidence
from .readiness_v4 import load_v4_campaign_authorization
from .evaluator_v4 import (
    OfflineCandidateEvaluatorV4, V4_BUNDLE_PATH, build_production_evaluators_v4,
)
from .local_worker_v4 import (
    PROTOCOL_V4, PinnedLocalTextWorkerV4, V4WorkerProtocolError,
    validate_v4_worker_packet,
)
from .research_plan_v4 import ResearchPlanV4, compile_plan_v4
from .stage0_funnel import (
    build_outcome_blind_funnel_v4, order_registered_plans_by_stage0_v4,
    verify_outcome_blind_funnel_v4,
)
from .verifier_v4 import (
    _self_test_candidate, _self_test_scheduler, run_v4_readiness_self_test,
    verify_candidate_v4, verify_scheduler_state_v4,
)


RUNNER_VERSION = "klax-v4-production-orchestrator-v1"
OUTPUT_PARENT = Path("runs/campaigns_v4")
DATA_BUNDLE_PATH = V4_BUNDLE_PATH
CATEGORY_ROLES = {
    "forced_coverage": "explorer",
    "evidence_guided_deepen": "synthesizer",
    "cross_track_or_cross_control_combine": "synthesizer",
    "independent_alternative": "explorer",
    "adversarial_challenge": "critic",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _production_evaluators_v4(authorization, output: Path):
    """Inject two independently loaded exact-time V4 evaluators."""
    from klax_lab.evaluator_v3 import primary_fee_scenario
    # Keep the fee constructor visible at this production boundary; the V4
    # adapter receives the same frozen V3 fee policy through its public builder.
    primary_fee_scenario()
    return build_production_evaluators_v4(authorization, output)


def _production_worker_factory_v4(authorization, output: Path):
    """Reuse the readiness-pinned local runtime through the V4 protocol."""
    from klax_lab.orchestrator_v3 import _production_worker_factory
    legacy_factory = _production_worker_factory(authorization, output)
    carrier = legacy_factory("v4-runtime-carrier", "explorer")
    local_artifacts = output / "local-inference-v4"

    def factory(worker_id: str, _role: str) -> PinnedLocalTextWorkerV4:
        return PinnedLocalTextWorkerV4(
            worker_id=worker_id, backend=carrier.backend,
            authorization=authorization,
            expected_runtime_sha256=carrier.expected_runtime_sha256,
            artifact_root=local_artifacts)
    return factory


def verify_observed_fill_eligibility_v4(
    plan: ResearchPlanV4, ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless every selected fill has a complete observed quote.

    The frozen V3 assumed-fill ledger remains useful diagnostic evidence, but
    it cannot promote a V4 candidate.  A future eligible ledger must carry the
    recorded quote timestamp, bid, ask, spread, age, volume, source hash, fees,
    and cost fields on every accepted decision.
    """
    decisions = ledger.get("decisions")
    if (not isinstance(decisions, list)
            or ledger.get("research_plan_sha256") != plan.identity
            or ledger.get("actual_orders_placed") is not False):
        raise V3IntegrityStop("V4 evaluator ledger identity or safety fields differ")
    required = {
        "observed_quote_timestamp", "observed_bid", "observed_ask",
        "observed_spread", "quote_age_minutes", "observed_volume",
        "price_source", "entry_price", "entry_fee", "entry_outlay",
        "settlement_cost", "ticker", "event_ticker", "climate_date",
        "decision_at", "side", "purchased_probability",
    }
    accepted = [row for row in decisions
                if isinstance(row, Mapping) and row.get("status") == "ACCEPTED"]
    missing = sorted({field for row in accepted for field in required
                      if row.get(field) is None})
    assumed = ledger.get("historical_assumed_fill_only") is not False or any(
        "assumed" in str(row.get("price_convention", "")).casefold()
        or "proxy" in str(row.get("price_convention", "")).casefold()
        for row in accepted)
    violations: list[str] = []
    for row in accepted:
        if any(field in missing for field in required):
            continue
        try:
            bid = Decimal(str(row["observed_bid"]))
            ask = Decimal(str(row["observed_ask"]))
            spread = Decimal(str(row["observed_spread"]))
            age = Decimal(str(row["quote_age_minutes"]))
            volume = Decimal(str(row["observed_volume"]))
            entry = Decimal(str(row["entry_price"]))
            quote_at = datetime.fromisoformat(
                str(row["observed_quote_timestamp"]).replace("Z", "+00:00"))
            decision_at = datetime.fromisoformat(
                str(row["decision_at"]).replace("Z", "+00:00"))
            if (quote_at.tzinfo is None or decision_at.tzinfo is None
                    or quote_at > decision_at):
                violations.append("quote_timestamp_after_decision_or_naive")
            measured_age = Decimal(str(
                (decision_at - quote_at).total_seconds() / 60))
            if age < 0 or age > Decimal(plan.maximum_price_age_minutes):
                violations.append("quote_age_exceeds_registered_cap")
            if abs(measured_age - age) > Decimal("0.02"):
                violations.append("quote_age_disagrees_with_timestamps")
            if not (Decimal("0") <= bid <= ask <= Decimal("1")):
                violations.append("observed_bid_ask_invalid")
            if spread != ask - bid or spread * 100 > plan.maximum_spread_cents:
                violations.append("observed_spread_invalid_or_above_cap")
            if entry != ask:
                violations.append("entry_is_not_observed_ask")
            cents = entry * 100
            if not (plan.entry_price_floor_cents <= cents
                    <= plan.entry_price_ceiling_cents):
                violations.append("entry_price_outside_registered_band")
            if volume < plan.minimum_candle_volume:
                violations.append("observed_volume_below_registered_minimum")
            source = str(row["price_source"])
            if len(source) != 64 or any(c not in "0123456789abcdef" for c in source):
                violations.append("price_source_hash_invalid")
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            violations.append("malformed_observed_quote")
    violations = sorted(set(violations))
    eligible = bool(accepted) and not missing and not assumed and not violations
    body = {
        "verification_version": "klax-v4-observed-fill-verification-v1",
        "plan_sha256": plan.identity,
        "accepted_decisions": len(accepted),
        "required_fields": sorted(required),
        "missing_required_fields": missing,
        "assumed_or_proxy_fill_detected": assumed,
        "quote_control_violations": violations,
        "promotion_eligible": eligible,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "verification_sha256": canonical_hash(body)}


def evaluate_candidate_v4(
    engine: "V4CampaignEngine", candidate_id: str,
    evaluator: OfflineCandidateEvaluatorV4,
) -> str:
    """Run the fixed evaluator; no caller-supplied result object is accepted."""
    return engine.execute_candidate(candidate_id, evaluator)


def _apply_candidate_verifier_v4(
    root: Path, verifier_record: Mapping[str, Any], critic: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the independent verifier and force a fail-closed critic verdict.

    This small boundary is shared by production and the integration self-test.
    It deliberately resolves ``verify_candidate_v4`` at call time so readiness
    can instrument the exact production dependency rather than trusting a
    claimed boolean.
    """
    independent = verify_candidate_v4(root, verifier_record)
    checked_critic = deepcopy(dict(critic))
    robust_only = (
        independent.get("status") == "PASS"
        and independent.get("gate_failures")
        == ["primary_return_at_least_10_percent"])
    if ((independent.get("status") != "PASS"
            or independent.get("promotion_eligible") is not True)
            and not robust_only):
        checked_critic.setdefault("checks", {})["fill"] = False
        checked_critic["decision"] = "REJECT"
        defects = checked_critic.setdefault("unresolved_defects", [])
        defect = ("independent_v4_verifier_failed"
                  if independent.get("status") != "PASS"
                  else "independent_v4_promotion_gates_not_met")
        if defect not in defects:
            defects.append(defect)
    return independent, checked_critic


def _apply_scheduler_verifier_v4(
    root: Path, scheduler: Mapping[str, Any], report: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the independent scheduler verifier and downgrade all failures."""
    verification = verify_scheduler_state_v4(root, scheduler)
    checked_report = deepcopy(dict(report))
    checked_report["scheduler_verification"] = verification
    if verification.get("status") != "PASS":
        checked_report["scientific_conclusion"] = "INSUFFICIENT_EVIDENCE"
    return verification, checked_report


def _render_report_markdown_v4(report: Mapping[str, Any]) -> str:
    """Render the human report from the already verified final conclusion."""
    return "\n".join([
        "# KLAX V4 twelve-hour offline campaign", "",
        f"- Campaign: `{report['campaign_id']}`",
        f"- Conclusion: **{report['scientific_conclusion']}**",
        f"- Evaluated candidates: {report['evaluated_candidates']}",
        f"- Local model calls: {report['local_model_calls']}",
        "- Protected final read: **no**",
        "- Orders placed: **no**", "",
        "All returns are adaptive historical simulations. Assumed or proxy fills are "
        "diagnostic only and cannot promote a V4 candidate.", "",
    ])


def _scientific_conclusion_v4(
    *, integrity_failures: bool, stopped_reason: str | None,
    champion: bool, robust_positive: bool, any_positive: bool,
) -> str:
    """Choose the terminal label with integrity taking absolute precedence."""
    if (integrity_failures or stopped_reason
            == "required_data_integrity_replication_critic_or_resource_boundary_failure"):
        return "INSUFFICIENT_EVIDENCE"
    if champion:
        return "PROVISIONAL_V4_DEVELOPMENT_GATE_PASS"
    if robust_positive:
        return "PROVISIONAL_ROBUST_POSITIVE_GATE_PASS"
    if any_positive:
        return "PROVISIONAL_POSITIVE_RETURN_ONLY"
    return "NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET"


def _review_candidate_v4(
    engine: V3CampaignEngine, candidate_id: str,
    replication: Mapping[str, Any], critic: Mapping[str, Any],
    independent: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the V3 promotion call to the independently verified verdict."""
    champion_eligible = (
        independent.get("status") == "PASS"
        and independent.get("promotion_eligible") is True
        and not independent.get("gate_failures"))
    robust_only = (
        independent.get("status") == "PASS"
        and independent.get("promotion_eligible") is False
        and independent.get("gate_failures")
        == ["primary_return_at_least_10_percent"])
    review_eligible = champion_eligible or robust_only
    if not review_eligible and critic.get("decision") != "REJECT":
        raise V3IntegrityStop(
            "Independent V4 verifier failure was not carried into critic review")
    gate = engine.review_candidate(candidate_id, replication, critic)
    if (not champion_eligible and gate.get("passed") is not False):
        raise V3IntegrityStop(
            "Independent V4 verifier failure reached promotion")
    return gate


def independently_verify_candidate_v4(
    engine: "V4CampaignEngine", record: CandidateRecord,
    replication_evaluator: OfflineCandidateEvaluatorV4,
    primary_directory: Path, replication_directory: Path,
) -> dict[str, Any]:
    """Recompute numbers, criticize artifacts, and enforce observed fills."""
    replication = _independent_replication(
        engine, record, replication_evaluator,
        primary_directory, replication_directory)
    critic = _critic_review(engine, record, primary_directory)
    artifacts = _read_artifacts(primary_directory)
    fill = verify_observed_fill_eligibility_v4(record.plan, artifacts["ledger"])
    predictions = []
    labels_by_day = {
        row["climate_date"]: row for row in replication_evaluator.inputs.evaluation_labels}
    for raw in artifacts["predictions"].get("rows", []):
        row = dict(raw)
        label = labels_by_day.get(row.get("climate_date"), {})
        outcomes = {value.get("ticker"): value.get("yes_outcome")
                    for value in label.get("contracts", [])}
        winners = [index for index, ticker in enumerate(row.get("tickers", []))
                   if outcomes.get(ticker) == 1]
        if len(winners) == 1:
            row["observed_index"] = winners[0]
        predictions.append(row)
    fee_model = json.loads(
        (engine.authorization.root / "configs/evaluation.json").read_text(
            encoding="utf-8"))["fee_scenario"]
    raw_ledger = json.loads(json.dumps(artifacts["ledger"]))
    raw_ledger["protected_final_read"] = False
    verifier_record = {
        "plan": record.plan.to_dict(), "plan_sha256": record.plan.identity,
        "ledger": raw_ledger, "predictions": predictions,
        "fee_model": fee_model, "protected_final_read": False,
        "orders_created": False,
        "artifact_sha256s": dict(record.artifact_sha256s or {}),
    }
    independent, critic = _apply_candidate_verifier_v4(
        engine.authorization.root, verifier_record, critic)
    return {"replication": replication, "critic": critic,
            "observed_fill": fill, "independent_v4": independent,
            "verifier_record_sha256": canonical_hash(verifier_record)}


class V4CampaignEngine(V3CampaignEngine):
    """V3 budget/gate state machine with exact V4 admission and compilation."""

    def plan_is_denied(self, plan: ResearchPlanV4 | str) -> bool:
        identity = plan.identity if isinstance(plan, ResearchPlanV4) else plan
        if (not isinstance(identity, str) or len(identity) != 64
                or any(char not in "0123456789abcdef" for char in identity)):
            raise V3CampaignError("Invalid V4 research plan identity")
        return identity in self.authorization.denied_plan_sha256s

    def require_plan_allowed(self, plan: ResearchPlanV4, *, stage: str) -> None:
        if self.plan_is_denied(plan):
            self.fail_integrity(
                f"Denied V4 research plan reached {stage}: {plan.identity}")

    def admit_registered_plan_v4(
        self, plan: ResearchPlanV4, *, worker_id: str, task_id: str,
    ) -> dict[str, Any]:
        """Admit one whole scheduler-registered plan without text recombination."""
        self._check_operable()
        if self.current_epoch < 1:
            raise V3CampaignError("Begin an epoch before V4 admission")
        self.require_plan_allowed(plan, stage="registered candidate admission")
        compiled = compile_plan_v4(plan)
        fingerprint = compiled.structural_fingerprint
        if fingerprint in self.novelty_index:
            original = self.novelty_index[fingerprint]
            record = {
                "status": "DUPLICATE_STRUCTURE", "task_id": task_id,
                "worker_id": worker_id, "novelty_fingerprint": fingerprint,
                "original_candidate_id": original,
                "experiment_slot_consumed": False,
            }
            self.duplicate_proposals.append(record)
            return record
        if self.admitted_candidates >= self.budget.maximum_distinct_executed_candidates:
            self._stop("distinct_candidate_budget_exhausted")
            raise V3BudgetStop("V4 distinct-candidate budget exhausted")
        admitted = self.epoch_admissions.get(self.current_epoch, 0)
        if admitted >= self.budget.maximum_new_candidates_per_epoch:
            raise V3BudgetStop("V4 per-epoch candidate budget exhausted")
        candidate_id = "v4-candidate-" + plan.identity[:20]
        if candidate_id in self.candidates:
            raise V3IntegrityStop("V4 candidate identity collision")
        self.candidates[candidate_id] = CandidateRecord(
            candidate_id=candidate_id, plan=plan,
            discovery_worker_id=worker_id, epoch=self.current_epoch)
        self.novelty_index[fingerprint] = candidate_id
        self.admitted_candidates += 1
        self.epoch_admissions[self.current_epoch] = admitted + 1
        return {
            "status": "ADMITTED", "candidate_id": candidate_id,
            "research_plan_sha256": plan.identity,
            "novelty_fingerprint": fingerprint,
            "execution_manifest": compiled.execution_manifest,
            "experiment_slot_consumed": True,
            "nomination_mode": "deterministic_registered_allocator",
        }

    def execute_candidate(
        self, candidate_id: str, evaluator: OfflineCandidateEvaluatorV4,
    ) -> str:
        self._check_operable()
        record = self.candidates.get(candidate_id)
        if record is None or not isinstance(record.plan, ResearchPlanV4):
            raise V3CampaignError("Unknown or non-V4 candidate")
        compiled = compile_plan_v4(record.plan)
        context = V3EvaluationContext(
            campaign_id=self.authorization.campaign_id,
            scope=("synthetic_only" if self.authorization.synthetic
                   else "development_evaluation_only"),
            data_bundle_version=self.authorization.data_bundle_version,
            data_bundle_sha256=self.authorization.data_bundle_sha256,
            partition_contract=dict(self.authorization.partition_contract),
            partition_contract_sha256=self.authorization.partition_contract_sha256,
            protected_final_roots=tuple(
                path.relative_to(self.authorization.root).as_posix()
                for path in self.authorization.protected_final_roots),
        )
        bundle = evaluator.evaluate(
            plan=record.plan, execution_manifest=compiled.execution_manifest,
            context=context)
        if not isinstance(bundle, CandidateEvaluationBundle):
            self._stop(
                "required_data_integrity_replication_critic_or_resource_boundary_failure")
            raise V3IntegrityStop("V4 evaluator returned an untyped result bundle")
        return self.record_candidate_evaluation(
            candidate_id, candidate=bundle.candidate,
            reference=bundle.reference, folds=bundle.folds,
            stress_return=bundle.stress_return,
            artifact_sha256s=bundle.artifact_sha256s)


class ProductionV4Orchestrator(BoundedV3Orchestrator):
    """Recoverable V4 host; agents nominate, deterministic code decides."""

    def __post_init__(self) -> None:
        self.output_root = self.engine.authorization.assert_development_path(
            self.output_root)
        expected_parent = (self.engine.authorization.root / OUTPUT_PARENT).resolve()
        if self.output_root.parent != expected_parent:
            raise V3CampaignError("V4 output must use runs/campaigns_v4/<campaign-id>")
        if self.output_root.name != self.engine.authorization.campaign_id:
            raise V3CampaignError("V4 output directory must equal campaign identity")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.started_at_utc = self.started_at_utc or _now()
        self.queue = [] if self.queue is None else self.queue
        if self.coverage is None:
            manifest = build_v4_execution_manifest(self.engine.authorization.root)
            stage0 = build_outcome_blind_funnel_v4(
                self.engine.authorization.root)
            verify_outcome_blind_funnel_v4(stage0)
            write_json(self.output_root / "stage0-outcome-blind-funnel.json", stage0)
            manifest["stage0_outcome_blind_funnel_sha256"] = stage0[
                "artifact_sha256"]
            manifest["manifest_sha256"] = canonical_hash({
                key: value for key, value in manifest.items()
                if key != "manifest_sha256"})
            self.coverage = {
                "architecture_version": 4,
                "runner_version": RUNNER_VERSION,
                "manifest": manifest,
                "stage0_outcome_blind_funnel": stage0,
                "colonies": {name: {
                    "proposed": 0, "admitted": 0, "executed": 0,
                    "rejected": 0, "champions": 0,
                } for name in COLONY_ORDER},
                "epochs": {}, "allocation_decisions": [], "digests": [],
                "allocation_history": [],
                "synthesis_artifacts": [], "observed_fill_verifications": [],
                "independent_candidate_verifications": [],
                "call_journal": [], "resume_history": [{
                    "started_at_utc": self.started_at_utc,
                    "candidate_calls_used": 0, "synthesis_calls_used": 0,
                    "transient_retry_calls_used": 0,
                }],
                "duplicate_proposals": 0, "nonproposal_responses": 0,
                "integrity_failures": [],
                "v4_stop_reason": None,
                "robust_positive_candidate_id": None,
                "protected_final_read": False, "actual_orders_placed": False,
            }

    def save_recovery(self) -> dict[str, Any]:
        body = {
            "recovery_version": "klax-v4-campaign-recovery-v1",
            "orchestrator_version": RUNNER_VERSION,
            "campaign_id": self.engine.authorization.campaign_id,
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "ticket_sha256": self.engine.authorization.ticket_sha256,
            "phase": self.phase, "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "queue": self.queue, "next_queue_index": self.next_queue_index,
            "coverage": self.coverage, "engine": self._engine_state(),
            "plan_language": "klax-research-plan-v4",
            "data_bundle_path": DATA_BUNDLE_PATH.as_posix(),
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        }
        value = {**body, "state_sha256": canonical_hash(body)}
        write_json(self.state_path, value)
        return value

    @classmethod
    def recover(
        cls, *, engine: V4CampaignEngine,
        primary_evaluator: OfflineCandidateEvaluatorV4,
        replication_evaluator: OfflineCandidateEvaluatorV4,
        output_root: Path, worker_factory: Any = None,
    ) -> "ProductionV4Orchestrator":
        value = json.loads(
            (Path(output_root) / "recovery-state.json").read_text(encoding="utf-8"))
        body = {key: item for key, item in value.items() if key != "state_sha256"}
        if (value.get("state_sha256") != canonical_hash(body)
                or value.get("recovery_version") != "klax-v4-campaign-recovery-v1"
                or value.get("orchestrator_version") != RUNNER_VERSION
                or value.get("campaign_id") != engine.authorization.campaign_id
                or value.get("readiness_sha256") != engine.authorization.readiness_sha256
                or value.get("ticket_sha256") != engine.authorization.ticket_sha256
                or value.get("plan_language") != "klax-research-plan-v4"
                or value.get("data_bundle_path") != DATA_BUNDLE_PATH.as_posix()
                or value.get("protected_final_evaluated") is not False
                or value.get("actual_orders_placed") is not False):
            raise V3IntegrityStop("V4 recovery state is modified or differently bound")
        state = value.get("engine")
        if not isinstance(state, dict):
            raise V3IntegrityStop("V4 recovery engine state is missing")
        engine.started_at = engine.clock() - int(state["elapsed_seconds"])
        for name in ("current_epoch", "model_calls", "model_context_tokens_reserved",
                     "admitted_candidates", "executed_candidates", "empty_epochs"):
            raw = state.get(name)
            if type(raw) is not int or raw < 0:
                raise V3IntegrityStop("V4 recovery counter is invalid")
            setattr(engine, name, raw)
        engine.stopped_reason = state.get("stopped_reason")
        engine.champion_candidate_id = state.get("champion_candidate_id")
        engine.final_authorization = state.get("final_authorization")
        engine.novelty_index = dict(state.get("novelty_index", {}))
        engine.duplicate_proposals = list(state.get("duplicate_proposals", []))
        engine.nonproposal_responses = list(state.get("nonproposal_responses", []))
        engine.epoch_admissions = {
            int(key): item for key, item in state.get("epoch_admissions", {}).items()}
        engine.transient_retries = dict(state.get("transient_retries", {}))
        engine.candidates = {}
        for candidate_id, raw in state.get("candidates", {}).items():
            plan = ResearchPlanV4.from_dict(raw["plan"])
            if candidate_id != "v4-candidate-" + plan.identity[:20]:
                raise V3IntegrityStop("V4 recovery candidate identity differs")
            engine.candidates[candidate_id] = CandidateRecord(
                candidate_id=candidate_id, plan=plan,
                discovery_worker_id=raw["discovery_worker_id"], epoch=raw["epoch"],
                artifact_sha256s=raw.get("artifact_sha256s"),
                evaluation=raw.get("evaluation"), promotion=raw.get("promotion"),
                replication=raw.get("replication"), critic=raw.get("critic"))
        expected_novelty = {
            row.plan.novelty_fingerprint: row.candidate_id
            for row in engine.candidates.values()}
        if (engine.novelty_index != expected_novelty
                or engine.admitted_candidates != len(engine.candidates)
                or engine.executed_candidates != sum(
                    row.evaluation is not None for row in engine.candidates.values())
                or engine.current_epoch > engine.budget.maximum_epochs
                or engine.model_calls > engine.budget.maximum_local_model_calls):
            raise V3IntegrityStop("V4 recovery violates a budget or identity")
        return cls(
            engine, primary_evaluator, replication_evaluator, Path(output_root),
            worker_factory=worker_factory, phase=value["phase"],
            queue=list(value["queue"]),
            next_queue_index=value["next_queue_index"],
            coverage=dict(value["coverage"]),
            started_at_utc=value["started_at_utc"],
            completed_at_utc=value.get("completed_at_utc"))

    @property
    def registration(self) -> dict[str, Any]:
        return load_v4_execution_registration(self.engine.authorization.root)

    def _registered_plans(self) -> tuple[ResearchPlanV4, ...]:
        registration = self.registration
        tracks = tuple(ModelTrackV4.from_dict(row)
                       for row in registration["execution_factorial"]["model_tracks"])
        source = load_ranked_v3_parent_records(
            self.engine.authorization.root, registration)
        parent, _ = select_forecast_parent_v4(source)
        plans = execution_factorial_plans_v4(parent, tracks)
        stage0 = self.coverage.get("stage0_outcome_blind_funnel")
        if not isinstance(stage0, Mapping):
            raise V3IntegrityStop("V4 Stage-0 opportunity funnel is missing")
        return order_registered_plans_by_stage0_v4(plans, stage0)

    def _record_evidence(self, record: CandidateRecord) -> dict[str, Any]:
        primary_dir = Path(self.primary_evaluator.artifact_root) / record.plan.identity
        artifacts = _read_artifacts(primary_dir)
        return {
            "candidate_id": record.candidate_id,
            "plan": record.plan.to_dict(),
            "evaluation": record.evaluation,
            "promotion": record.promotion,
            "replication": record.replication,
            "critic": record.critic,
            "ledger": artifacts.get("ledger"),
            "predictions": artifacts.get("predictions"),
            "artifact_sha256s": dict(record.artifact_sha256s or {}),
        }

    def _evidence_for_next_epoch(self) -> tuple[dict[str, Any], ...]:
        if self.engine.current_epoch == 1:
            registration = self.registration
            records = load_ranked_v3_parent_records(
                self.engine.authorization.root, registration)
            return _source_evidence(
                self.engine.authorization.root, registration, records)
        rows = [row for row in self.engine.candidates.values()
                if row.epoch < self.engine.current_epoch and row.promotion is not None]
        return tuple(self._record_evidence(row) for row in rows)

    def _allocate_v4_queue(self) -> list[dict[str, Any]]:
        plans = self._registered_plans()
        completed = {row.plan.identity for row in self.engine.candidates.values()}
        evidence = self._evidence_for_next_epoch()
        previous = (self.coverage["digests"][-1]["digest_sha256"]
                    if self.coverage["digests"] else None)
        previous_synthesis_artifact = (
            self.coverage["synthesis_artifacts"][-1]
            if self.coverage["synthesis_artifacts"] else None)
        previous_synthesis = (
            previous_synthesis_artifact["artifact_sha256"]
            if previous_synthesis_artifact else None)
        digest = build_cross_pollination_digest_v4(
            self.engine.current_epoch, evidence,
            previous_digest_sha256=previous,
            prior_synthesis_artifact_sha256=previous_synthesis,
            prior_synthesis_artifact=previous_synthesis_artifact)
        verify_cross_pollination_digest_v4(digest)
        self.coverage["digests"].append(digest)
        allocations = allocate_epoch_v4(
            plans, completed, evidence, cross_pollination_digest=digest)
        decision = {
            "epoch": self.engine.current_epoch,
            "digest_sha256": digest["digest_sha256"],
            "quotas": dict(Counter(row.category for row in allocations)),
            "plan_sha256s": [row.plan.identity for row in allocations],
        }
        decision["decision_sha256"] = canonical_hash(decision)
        self.coverage["allocation_decisions"].append(decision)
        queue = []
        for slot, row in enumerate(allocations, 1):
            followup = adaptive_followup_packet_v4(row, digest, self.registration)
            queue.append({
                "task_id": f"v4-e{self.engine.current_epoch:03d}-s{slot:02d}",
                "category": row.category, "role": CATEGORY_ROLES[row.category],
                "colony": row.colony, "plan": row.plan.to_dict(),
                "options": [row.plan.to_dict()], "followup_packet": followup,
                "status": "PENDING", "candidate_id": None,
            })
        self.coverage["allocation_history"].append({
            "epoch": self.engine.current_epoch, "digest": digest,
            "allocations": [{
                "task_id": item["task_id"], "category": item["category"],
                "colony": item["colony"], "plan": item["plan"],
                "plan_sha256": ResearchPlanV4.from_dict(item["plan"]).identity,
                "candidate_id": None,
                "followup_packet": item["followup_packet"],
            } for item in queue],
            "completed": False, "synthesis_artifact": None,
        })
        return queue

    def _packet_with_digest(self, item: Mapping[str, Any]) -> dict[str, Any]:
        plans = [ResearchPlanV4.from_dict(value) for value in item["options"]]
        if len(plans) != 1 or plans[0].identity != ResearchPlanV4.from_dict(
                dict(item["plan"])).identity:
            raise V3IntegrityStop("V4 deterministic nomination must contain one whole plan")
        followup = item["followup_packet"]
        digest = self.coverage["digests"][-1]
        if followup["evidence_digest_sha256"] != digest["digest_sha256"]:
            raise V3IntegrityStop("V4 follow-up prompt has different evidence lineage")
        packet = {
            "protocol": PROTOCOL_V4,
            "mode": "candidate_nomination",
            "task_id": str(item["task_id"]),
            "campaign_id": self.engine.authorization.campaign_id,
            "worker_role": str(item["role"]),
            "scope": "development_only", "synthetic": False,
            "question": (
                "Nominate the supplied whole registered V4 plan for deterministic "
                "offline evaluation. Do not alter fields or claim results."),
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "config_sha256": self.engine.authorization.config_sha256,
            "schema_sha256": self.engine.authorization.schema_sha256,
            "partition_contract_sha256": (
                self.engine.authorization.partition_contract_sha256),
            "data_bundle_version": self.engine.authorization.data_bundle_version,
            "data_bundle_sha256": self.engine.authorization.data_bundle_sha256,
            "evidence": [{
                "evidence_id": f"v4-digest-e{self.engine.current_epoch:03d}",
                "scope": "development_evaluation",
                "summary": json.dumps({
                    "followup": followup, "digest": digest,
                }, sort_keys=True, separators=(",", ":"), allow_nan=False)[:1800],
                "artifact_sha256": digest["digest_sha256"],
            }],
            "seed_plans": [plan.to_dict() for plan in plans],
            "budget_remaining": self.engine.budget_remaining(),
            "digest_sha256": digest["digest_sha256"],
            "protected_final_read": False,
        }
        return validate_v4_worker_packet(packet)

    def _execute_and_review(self, item: dict[str, Any]) -> None:
        candidate_id = item["candidate_id"]
        record = self.engine.candidates[candidate_id]
        if record.evaluation is None:
            evaluate_candidate_v4(
                self.engine, candidate_id, self.primary_evaluator)
            self.coverage["colonies"][record.plan.colony]["executed"] += 1
            item["status"] = "EVALUATED"
            self.save_recovery()
        if record.promotion is None:
            primary_dir = Path(self.primary_evaluator.artifact_root) / record.plan.identity
            replication_dir = (
                Path(self.replication_evaluator.artifact_root) / record.plan.identity)
            verification = independently_verify_candidate_v4(
                self.engine, record, self.replication_evaluator,
                primary_dir, replication_dir)
            replication = verification["replication"]
            critic = verification["critic"]
            fill = verification["observed_fill"]
            self.coverage["observed_fill_verifications"].append(fill)
            independent = verification["independent_v4"]
            self.coverage["independent_candidate_verifications"].append({
                "candidate_id": candidate_id,
                "verifier_record_sha256": verification["verifier_record_sha256"],
                "report": independent,
            })
            review_dir = self.output_root / "reviews" / candidate_id
            write_json(review_dir / "replication.json", replication)
            write_json(review_dir / "critic.json", critic)
            write_json(review_dir / "observed-fill.json", fill)
            write_json(review_dir / "independent-v4-verification.json", independent)
            gate = _review_candidate_v4(
                self.engine, candidate_id, replication, critic, independent)
            write_json(review_dir / "promotion.json", gate)
            bucket = "champions" if gate["passed"] else "rejected"
            self.coverage["colonies"][record.plan.colony][bucket] += 1
            if robust_positive_milestone_v4(self._record_evidence(record)):
                self.coverage["robust_positive_candidate_id"] = candidate_id
                self.coverage["v4_stop_reason"] = (
                    "robust_positive_nonchampion_review")
            item["status"] = "REVIEWED"
            self.save_recovery()

    def _process_item(self, item: dict[str, Any]) -> None:
        plan = ResearchPlanV4.from_dict(item["plan"])
        colony = self.coverage["colonies"][plan.colony]
        if item["status"] == "PENDING":
            packet = self._packet_with_digest(item)
            role = item["role"]
            worker_id = (
                f"{role}-{plan.colony}-e{self.engine.current_epoch:03d}"
                f"-s{item['task_id'][-2:]}")
            if self.worker_factory is None:
                raise V3IntegrityStop("V4 production worker factory is missing")
            worker = self.worker_factory(worker_id, role)
            if worker.worker_id != worker_id:
                raise V3IntegrityStop("V4 worker identity differs")
            response = None
            maximum_attempts = 1 + self.engine.budget.maximum_transient_retries_per_task
            for attempt in range(maximum_attempts):
                journal = {
                    "task_id": item["task_id"], "kind": "candidate_nomination",
                    "call_type": "candidate" if attempt == 0 else "retry",
                    "worker_id": worker_id, "status": "PENDING",
                    "attempt": attempt + 1,
                    "digest_sha256": self.coverage["digests"][-1]["digest_sha256"],
                }
                try:
                    with self.engine.model_call(worker_id):
                        journal["status"] = "RESERVED"
                        journal["model_call_number"] = self.engine.model_calls
                        journal["call_id"] = (
                            f"{journal['call_type']}-{self.engine.model_calls:04d}-"
                            f"{item['task_id']}")
                        journal["started_at_utc"] = _now()
                        self.coverage["call_journal"].append(journal)
                        self.save_recovery()
                        response = worker.respond(packet)
                    journal["status"] = "COMPLETED"
                    journal["completed_at_utc"] = _now()
                    if response.get("action") == "propose":
                        break
                except (OSError, RuntimeError, V4WorkerProtocolError):
                    journal["status"] = "FAILED"
                    journal["completed_at_utc"] = _now()
                    if attempt + 1 >= maximum_attempts:
                        raise
                if attempt + 1 < maximum_attempts:
                    self.engine.register_transient_failure(item["task_id"])
            proposed = (isinstance(response, Mapping)
                        and response.get("action") == "propose"
                        and response.get("seed_index") == 0)
            fallback = (isinstance(response, Mapping)
                        and response.get("action") in {"reject", "abstain"})
            if not proposed and not fallback:
                raise V3IntegrityStop("V4 bounded nomination returned an invalid selection")
            result = self.engine.admit_registered_plan_v4(
                plan, worker_id=worker_id, task_id=str(item["task_id"]))
            result["nomination_fallback_used"] = bool(fallback)
            saved_response = {
                "worker_response": dict(response),
                "host_disposition": (
                    "ADMIT_EXACT_REGISTERED_PLAN_AFTER_EXHAUSTED_CHARGED_ABSTENTION"
                    if fallback else "ADMIT_WORKER_NOMINATED_EXACT_REGISTERED_PLAN"),
                "plan_sha256": plan.identity,
            }
            self._save_task(item, packet, saved_response, result)
            if result["status"] != "ADMITTED":
                raise V3IntegrityStop(
                    "V4 fixed-quota task did not admit its registered plan")
            colony["proposed"] += 1
            colony["admitted"] += 1
            item["candidate_id"] = result["candidate_id"]
            history = self.coverage["allocation_history"][-1]
            history["allocations"][self.next_queue_index]["candidate_id"] = result[
                "candidate_id"]
            item["status"] = "ADMITTED"
            self.save_recovery()
        if item["status"] in {"ADMITTED", "EVALUATED"}:
            self._execute_and_review(item)

    def _synthesize_if_due(self) -> None:
        cadence = self.registration["cross_pollination"][
            "local_model_synthesis_every_epochs"]
        if self.engine.current_epoch % cadence:
            return
        digest = self.coverage["digests"][-1]
        reviewed = [item for item in self.queue if item["status"] == "REVIEWED"]
        if not reviewed:
            raise V3IntegrityStop("V4 synthesis has no reviewed candidate")
        task_id = f"v4-synthesis-e{self.engine.current_epoch:03d}"
        packet = {
            "protocol": PROTOCOL_V4, "mode": "cross_pollination_synthesis",
            "task_id": task_id,
            "campaign_id": self.engine.authorization.campaign_id,
            "worker_role": "synthesizer", "scope": "development_only",
            "synthetic": False,
            "question": (
                "Consolidate the supplied intermediate evidence into the next "
                "bounded V4 follow-up. Preserve falsified branches and uncertainty."),
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "config_sha256": self.engine.authorization.config_sha256,
            "schema_sha256": self.engine.authorization.schema_sha256,
            "partition_contract_sha256": (
                self.engine.authorization.partition_contract_sha256),
            "data_bundle_version": self.engine.authorization.data_bundle_version,
            "data_bundle_sha256": self.engine.authorization.data_bundle_sha256,
            "evidence": [{
                "evidence_id": f"v4-synthesis-source-e{self.engine.current_epoch:03d}",
                "scope": "development_evaluation",
                "summary": json.dumps(
                    digest, sort_keys=True, separators=(",", ":"))[:1800],
                "artifact_sha256": digest["digest_sha256"],
            }],
            "seed_plans": [], "budget_remaining": self.engine.budget_remaining(),
            "digest_sha256": digest["digest_sha256"],
            "protected_final_read": False,
        }
        packet = validate_v4_worker_packet(packet)
        if self.worker_factory is None:
            raise V3IntegrityStop("V4 production worker factory is missing")
        worker_id = f"synthesizer-cross-pollination-e{self.engine.current_epoch:03d}"
        worker = self.worker_factory(worker_id, "synthesizer")
        if worker.worker_id != worker_id:
            raise V3IntegrityStop("V4 synthesis worker identity differs")
        response = None
        maximum_attempts = 1 + self.engine.budget.maximum_transient_retries_per_task
        for attempt in range(maximum_attempts):
            journal = {
                "task_id": task_id, "kind": "cross_pollination_synthesis",
                "call_type": "synthesis" if attempt == 0 else "retry",
                "worker_id": worker_id, "status": "PENDING",
                "attempt": attempt + 1, "digest_sha256": digest["digest_sha256"],
            }
            try:
                with self.engine.model_call(worker_id):
                    journal["status"] = "RESERVED"
                    journal["model_call_number"] = self.engine.model_calls
                    journal["call_id"] = (
                        f"{journal['call_type']}-{self.engine.model_calls:04d}-{task_id}")
                    journal["started_at_utc"] = _now()
                    self.coverage["call_journal"].append(journal)
                    self.save_recovery()
                    response = worker.respond(packet)
                journal["status"] = "COMPLETED"
                journal["completed_at_utc"] = _now()
                if response.get("action") == "synthesize":
                    break
            except (OSError, RuntimeError, V4WorkerProtocolError):
                journal["status"] = "FAILED"
                journal["completed_at_utc"] = _now()
                if attempt + 1 >= maximum_attempts:
                    raise
            if attempt + 1 < maximum_attempts:
                self.engine.register_transient_failure(task_id)
        if (not isinstance(response, Mapping)
                or response.get("action") != "synthesize"
                or not isinstance(response.get("synthesis"), Mapping)):
            raise V3IntegrityStop("V4 bounded synthesis returned no typed content")
        result = dict(response["synthesis"])
        artifact_body = {
            "artifact_version": "klax-v4-model-synthesis-v1",
            "epoch": self.engine.current_epoch,
            "source_digest_sha256": digest["digest_sha256"],
            "packet_sha256": canonical_hash(packet),
            "response": response, "admission": {
                "status": "CONSOLIDATED", "synthesis": result},
            "used_by_next_followup": True,
            "protected_final_read": False,
        }
        artifact = {**artifact_body, "artifact_sha256": canonical_hash(artifact_body)}
        path = self.output_root / "synthesis" / f"epoch-{self.engine.current_epoch:03d}.json"
        write_json(path, artifact)
        self.coverage["synthesis_artifacts"].append(artifact)
        self.coverage["allocation_history"][-1]["synthesis_artifact"] = artifact
        self.save_recovery()

    def run(self) -> dict[str, Any]:
        try:
            while (self.engine.stopped_reason is None
                   and self.coverage.get("v4_stop_reason") is None):
                if self.phase == "BETWEEN_EPOCHS":
                    if self.engine.current_epoch > 0:
                        elapsed = self.engine.clock() - self.engine.started_at
                        average_epoch = elapsed / self.engine.current_epoch
                        remaining = self.engine.budget.maximum_wall_seconds - elapsed
                        if remaining < average_epoch:
                            self.engine.stopped_reason = "wall_time_budget_exhausted"
                            self.phase = "COMPLETE"
                            self.save_recovery()
                            break
                    self.engine.begin_epoch()
                    self.queue = self._allocate_v4_queue()
                    if len(self.queue) != EPOCH_SIZE:
                        raise V3IntegrityStop("V4 epoch queue does not contain twelve plans")
                    self.next_queue_index = 0
                    self.phase = "EXECUTING_EPOCH"
                    self.coverage["epochs"][str(self.engine.current_epoch)] = {
                        "planned": EPOCH_SIZE, "reviewed": 0, "admitted": 0,
                        "digest_sha256": self.coverage["digests"][-1]["digest_sha256"],
                    }
                    self.save_recovery()
                while (self.next_queue_index < len(self.queue)
                       and self.engine.stopped_reason is None
                       and self.coverage.get("v4_stop_reason") is None):
                    self._process_item(self.queue[self.next_queue_index])
                    self.next_queue_index += 1
                    epoch = self.coverage["epochs"][str(self.engine.current_epoch)]
                    epoch["reviewed"] = sum(
                        row["status"] == "REVIEWED" for row in self.queue)
                    epoch["admitted"] = sum(
                        row["status"] in {"ADMITTED", "EVALUATED", "REVIEWED"}
                        for row in self.queue)
                    self.save_recovery()
                if (self.engine.stopped_reason is None
                        and self.coverage.get("v4_stop_reason") is None):
                    self._synthesize_if_due()
                    self.coverage["allocation_history"][-1]["completed"] = True
                    self.engine.finish_epoch()
                self.phase = ("COMPLETE" if (
                    self.engine.stopped_reason
                    or self.coverage.get("v4_stop_reason"))
                              else "BETWEEN_EPOCHS")
                self.save_recovery()
            return self._finalize_v4()
        except (V3BudgetStop, V3IntegrityStop) as exc:
            if isinstance(exc, V3IntegrityStop):
                self.coverage.setdefault("integrity_failures", []).append({
                    "recorded_at_utc": _now(), "reason": str(exc),
                })
                if self.engine.stopped_reason is None:
                    try:
                        self.engine.fail_integrity(str(exc))
                    except V3IntegrityStop:
                        pass
            self.phase = "COMPLETE"
            self.save_recovery()
            return self._finalize_v4()

    def _finalize_v4(self) -> dict[str, Any]:
        self.completed_at_utc = self.completed_at_utc or _now()
        ranked = sorted(self.engine.candidates.values(), key=_ranking_key)
        rows = [_plan_summary(row) for row in ranked]
        any_positive = any(
            isinstance(row.get("capital_weighted_return"), (int, float))
            and row["capital_weighted_return"] > 0 for row in rows)
        robust = next((row for row in self.engine.candidates.values()
                       if robust_positive_milestone_v4(self._record_evidence(row))), None)
        conclusion = _scientific_conclusion_v4(
            integrity_failures=bool(self.coverage.get("integrity_failures")),
            stopped_reason=self.engine.stopped_reason,
            champion=self.engine.champion_candidate_id is not None,
            robust_positive=robust is not None, any_positive=any_positive)
        report = {
            "report_version": "klax-v4-offline-campaign-report-v1",
            "architecture_version": 4,
            "campaign_id": self.engine.authorization.campaign_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "scientific_conclusion": conclusion,
            "stopped_reason": (self.coverage.get("v4_stop_reason")
                               or self.engine.stopped_reason),
            "epochs_completed": self.engine.current_epoch,
            "evaluated_candidates": self.engine.executed_candidates,
            "local_model_calls": self.engine.model_calls,
            "ranked_candidates": rows,
            "protected_final_evaluated": False,
            "protected_final_authorized": False,
            "actual_orders_placed": False,
            "actual_profit_claimed": False,
            "observed_fill_required_for_promotion": True,
        }
        write_json(self.output_root / "candidate-register.json", rows)
        write_json(self.output_root / "swarm-coverage.json", self.coverage)
        started = datetime.fromisoformat(str(self.started_at_utc))
        completed_epochs = [
            row for row in self.coverage["allocation_history"]
            if row.get("completed") is True]
        active_epoch = next((
            row for row in reversed(self.coverage["allocation_history"])
            if row.get("completed") is not True), None)
        scheduler = {
            "state_version": "klax-v4-orchestrator-state-v1",
            "status": "COMPLETE",
            "current_epoch": self.engine.current_epoch,
            "allocation_history": self.coverage["allocation_history"],
            "active_queue": ([] if active_epoch is None
                             else active_epoch["allocations"]),
            "current_digest": (None if active_epoch is None
                               else active_epoch["digest"]),
            "last_completed_digest_sha256": (
                completed_epochs[-1]["digest"]["digest_sha256"]
                if completed_epochs else None),
            "completed_plan_sha256s": sorted(
                item["plan_sha256"]
                for epoch in self.coverage["allocation_history"]
                if epoch["completed"] for item in epoch["allocations"]),
            "call_journal": self.coverage["call_journal"],
            "candidate_calls_used": sum(
                row.get("call_type") == "candidate"
                for row in self.coverage["call_journal"]),
            "synthesis_calls_used": sum(
                row.get("call_type") == "synthesis"
                for row in self.coverage["call_journal"]),
            "transient_retry_calls_used": sum(
                row.get("call_type") == "retry"
                for row in self.coverage["call_journal"]),
            "resume_history": self.coverage["resume_history"],
            "budget": dict(self.registration["budget"]),
            "started_at_utc": self.started_at_utc,
            "deadline_utc": (
                started + timedelta(
                    seconds=self.registration["budget"]["maximum_wall_seconds"])
            ).isoformat(),
            "completed_at_utc": self.completed_at_utc,
            "stopped_reason": report["stopped_reason"],
            "registered_plan_count": self.coverage["manifest"][
                "registered_plan_count"],
            "manifest": self.coverage["manifest"],
            "protected_final_read": False,
            "protected_final_authorized": False,
            "orders_authorized": False,
            "actual_profit_claim": False,
        }
        scheduler["state_sha256"] = canonical_hash(scheduler)
        write_json(self.output_root / "scheduler-state.json", scheduler)
        write_json(self.output_root / "search-coverage.json", scheduler)
        scheduler_verification, report = _apply_scheduler_verifier_v4(
            self.engine.authorization.root, scheduler, report)
        write_json(self.output_root / "scheduler-verification.json",
                   scheduler_verification)
        write_json(self.output_root / "summary.json", report)
        (self.output_root / "report.md").write_text(
            _render_report_markdown_v4(report), encoding="utf-8")
        self.phase = "COMPLETE"
        self.save_recovery()
        inventory = []
        for path in sorted(self.output_root.rglob("*")):
            if path.is_file() and path.name not in {"campaign-artifacts.json", ".campaign-execution.lock"}:
                inventory.append({
                    "path": path.relative_to(self.output_root).as_posix(),
                    "bytes": path.stat().st_size, "sha256": sha256_file(path),
                })
        body = {
            "manifest_version": "klax-v4-campaign-artifacts-v1",
            "campaign_id": report["campaign_id"], "artifacts": inventory,
            "scheduler_verification_status": scheduler_verification.get("status"),
            "scientific_conclusion": report["scientific_conclusion"],
            "protected_final_read": False, "actual_orders_placed": False,
        }
        write_json(self.output_root / "campaign-artifacts.json",
                   {**body, "manifest_sha256": canonical_hash(body)})
        return {"path": str(self.output_root), **report}


def _paths(root: Path, campaign_id: str) -> Path:
    return (root / OUTPUT_PARENT / campaign_id).resolve()


def _build_runtime(root: Path, readiness_path: Path, ticket_path: Path,
                   *, resume: bool):
    authorization = load_v4_campaign_authorization(
        root, readiness_path, ticket_path, allow_existing_claim=resume)
    output = _paths(root, authorization.campaign_id)
    primary, replication = _production_evaluators_v4(authorization, output)
    workers = _production_worker_factory_v4(authorization, output)
    engine = V4CampaignEngine(authorization, claim_ticket=not resume)
    if resume:
        return ProductionV4Orchestrator.recover(
            engine=engine, primary_evaluator=primary,
            replication_evaluator=replication, output_root=output,
            worker_factory=workers)
    return ProductionV4Orchestrator(
        engine, primary, replication, output, worker_factory=workers)


def start_v4_campaign(root: Path | str, readiness_path: Path | str,
                      ticket_path: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    runner = _build_runtime(
        root, Path(readiness_path), Path(ticket_path), resume=False)
    if runner.state_path.exists() or (runner.output_root / "summary.json").exists():
        raise V3ReadinessRefusal("V4 campaign already started; use resume")
    with _CampaignExecutionLease(runner.output_root):
        runner.save_recovery()
        return runner.run()


def resume_v4_campaign(root: Path | str, readiness_path: Path | str,
                       ticket_path: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    runner = _build_runtime(
        root, Path(readiness_path), Path(ticket_path), resume=True)
    if (runner.output_root / "summary.json").exists():
        raise V3ReadinessRefusal("V4 campaign is already complete")
    with _CampaignExecutionLease(runner.output_root):
        runner.coverage["resume_history"].append({
            "resumed_at_utc": _now(),
            "candidate_calls_used": sum(
                row.get("call_type") == "candidate"
                for row in runner.coverage["call_journal"]),
            "synthesis_calls_used": sum(
                row.get("call_type") == "synthesis"
                for row in runner.coverage["call_journal"]),
            "transient_retry_calls_used": sum(
                row.get("call_type") == "retry"
                for row in runner.coverage["call_journal"]),
        })
        runner.save_recovery()
        return runner.run()


def campaign_status_v4(root: Path | str, ticket_path: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    ticket_file = Path(ticket_path)
    ticket_file = (ticket_file.resolve() if ticket_file.is_absolute()
                   else (root / ticket_file).resolve())
    ticket = json.loads(ticket_file.read_text(encoding="utf-8"))
    campaign_id = ticket.get("campaign_id")
    if not isinstance(campaign_id, str):
        raise V3ReadinessRefusal("V4 ticket campaign identity is missing")
    output = _paths(root, campaign_id)
    summary_path, recovery_path = output / "summary.json", output / "recovery-state.json"
    summary = (json.loads(summary_path.read_text(encoding="utf-8"))
               if summary_path.is_file() else None)
    recovery = (json.loads(recovery_path.read_text(encoding="utf-8"))
                if recovery_path.is_file() else None)
    return {
        "campaign_id": campaign_id,
        "status": ("COMPLETE" if summary is not None else
                   "INCOMPLETE_EXPLICIT_RESUME_REQUIRED" if recovery is not None
                   else "TICKET_READY_NOT_STARTED"),
        "phase": (summary and "COMPLETE") or (recovery or {}).get("phase"),
        "current_epoch": ((recovery or {}).get("engine") or {}).get("current_epoch"),
        "evaluated_candidates": ((summary or {}).get("evaluated_candidates")
                                 if summary else
                                 ((recovery or {}).get("engine") or {}).get(
                                     "executed_candidates")),
        "scientific_conclusion": (summary or {}).get("scientific_conclusion"),
        "protected_final_read": False, "actual_orders_placed": False,
    }


def run_v4_production_integration_self_test(root: Path | str) -> dict[str, Any]:
    """Instrument the exact verifier boundaries used by production.

    The fixtures are deliberately invalid.  A future refactor that removes
    either independent call, fails to carry its verdict into review, or writes
    a pre-verification conclusion will make this test fail closed.
    """
    root = Path(root).resolve()
    independent_self_test = run_v4_readiness_self_test(root)
    candidate_dependency = globals()["verify_candidate_v4"]
    scheduler_dependency = globals()["verify_scheduler_state_v4"]
    calls = {"candidate": 0, "scheduler": 0}

    def candidate_instrumented(
            candidate_root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
        calls["candidate"] += 1
        return candidate_dependency(candidate_root, record)

    def scheduler_instrumented(
            scheduler_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
        calls["scheduler"] += 1
        return scheduler_dependency(scheduler_root, state)

    globals()["verify_candidate_v4"] = candidate_instrumented
    globals()["verify_scheduler_state_v4"] = scheduler_instrumented
    try:
        valid = _self_test_candidate(root)
        forged = {
            "plan": valid["plan"], "plan_sha256": valid["plan_sha256"],
            "promotion": {"passed": True},
            "replication": {"status": "PASS"},
            "critic": {"decision": "NONREJECT"},
        }
        base_critic = {
            "checks": {"fill": True}, "decision": "NONREJECT",
            "unresolved_defects": [],
        }
        candidate_report, checked_critic = _apply_candidate_verifier_v4(
            root, forged, base_critic)

        class _FailClosedEngine:
            def review_candidate(self, _candidate_id, _replication, critic):
                return {"passed": critic.get("decision") != "REJECT"}

        gate = _review_candidate_v4(
            _FailClosedEngine(), "forged-candidate", {"status": "PASS"},
            checked_critic, candidate_report)

        scheduler = _self_test_scheduler(root)
        scheduler["protected_final_read"] = True
        scheduler["state_sha256"] = canonical_hash({
            key: value for key, value in scheduler.items()
            if key != "state_sha256"})
        base_report = {
            "campaign_id": "v4-production-self-test",
            "scientific_conclusion": "PROVISIONAL_V4_DEVELOPMENT_GATE_PASS",
            "evaluated_candidates": 0, "local_model_calls": 0,
        }
        scheduler_report, checked_report = _apply_scheduler_verifier_v4(
            root, scheduler, base_report)
        markdown = _render_report_markdown_v4(checked_report)
        checks = {
            "candidate_verifier_instrumented": (
                calls["candidate"] == 1
                and isinstance(candidate_report.get("verification_sha256"), str)),
            "candidate_verifier_failure_forces_critic_reject": (
                candidate_report.get("status") == "FAIL"
                and checked_critic.get("decision") == "REJECT"),
            "candidate_verifier_failure_blocks_promotion": (
                gate.get("passed") is False),
            "scheduler_verifier_instrumented": (
                calls["scheduler"] == 1
                and isinstance(scheduler_report.get("verification_sha256"), str)),
            "scheduler_verifier_failure_forces_insufficient_evidence": (
                scheduler_report.get("status") == "FAIL"
                and checked_report.get("scientific_conclusion")
                == "INSUFFICIENT_EVIDENCE"),
            "report_markdown_matches_summary_conclusion": (
                f"Conclusion: **{checked_report['scientific_conclusion']}**"
                in markdown),
        }
    finally:
        globals()["verify_candidate_v4"] = candidate_dependency
        globals()["verify_scheduler_state_v4"] = scheduler_dependency
    body = {
        "self_test_version": "klax-v4-production-integration-self-test-v2",
        "status": ("PASS" if independent_self_test.get("status") == "PASS"
                   and checks and all(checks.values()) else "FAIL"),
        "checks": checks,
        "independent_self_test_sha256": independent_self_test.get(
            "self_test_sha256"),
        "protected_final_read": False,
    }
    return {**body, "self_test_sha256": canonical_hash(body)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "resume", "status"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--ticket", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        result = campaign_status_v4(args.root, args.ticket)
    else:
        if args.readiness is None:
            parser.error("start/resume require --readiness")
        action = start_v4_campaign if args.command == "start" else resume_v4_campaign
        result = action(args.root, args.readiness, args.ticket)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
