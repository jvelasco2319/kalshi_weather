"""Bounded, recoverable V3 multi-worker research campaign.

The orchestration layer is deliberately less powerful than the numerical
evaluator.  Workers can select only typed :class:`ResearchPlanV3` values from
curated packets.  The host compiles, evaluates, independently reproduces and
criticizes each admitted plan against one readiness-bound development bundle.
No worker can read files, run code, use the network or open the protected final
partition through this module.

The default worker is a deterministic finite-search worker.  An optional local
model adapter accepts an already configured, offline completion callable; its
output passes through the identical V3 parser and budget guard.  The campaign
never calls a hosted or paid model.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from itertools import product
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .campaign_v3 import (
    CRITIC_CHECKS, CRITIC_VERSION, REPLICATION_CHECKS, REPLICATION_VERSION,
    CandidateEvaluationBundle, CandidateRecord, V3BudgetStop, V3CampaignEngine,
    V3CampaignError, V3IntegrityStop, V3ReadinessRefusal,
    load_v3_campaign_authorization,
)
from .continuation_v3 import (
    V3ContinuationError, build_continuation_import_manifest,
    verify_continuation_import_manifest, verify_epoch_two_rebuild,
)
from .evaluator_v3 import (
    OfflineCandidateEvaluatorV3, primary_fee_scenario,
    verify_candidate_evaluation_artifacts,
)
from .provenance import canonical_hash, sha256_file, write_json
from .replication_v3 import independently_recompute_candidate
from .research_plan_v3 import ResearchPlanV3, V3_COLONIES, make_plan_v3
from .research_protocol_v3 import (
    PROTOCOL_V3, V3ResearchProtocolError, validate_v3_worker_packet,
)


ORCHESTRATOR_VERSION = "klax-v3-bounded-orchestrator-v1"
RECOVERY_VERSION = "klax-v3-campaign-recovery-v2"
REPORT_VERSION = "klax-v3-campaign-report-v2"
CAMPAIGN_ARTIFACT_MANIFEST_VERSION = "klax-v3-campaign-artifacts-v1"
CAMPAIGN_ARTIFACT_MANIFEST = "campaign-artifacts.json"
CAMPAIGN_BOOTSTRAP_VERSION = "klax-v3-campaign-bootstrap-v1"
CONTINUATION_IMPORT_FILENAME = "continuation-import.json"
CAMPAIGN_EXECUTION_LOCK = ".campaign-execution.lock"
ALLOCATION_WEIGHTS = {
    "deepen_supported": 0.50,
    "cross_colony_combinations": 0.20,
    "independent_alternatives": 0.15,
    "adversarial_replication": 0.15,
}
ALLOCATION_ORDER = tuple(ALLOCATION_WEIGHTS)
COLONY_ORDER = tuple(V3_COLONIES)
COLONY_STAGE = {
    "settlement_measurement": "forecast_skill",
    "local_weather": "forecast_skill",
    "ensemble_probability": "probability_calibration",
    "market_behavior": "market_information",
    "execution_abstention": "economic_simulation",
    "adversarial_alternatives": "economic_simulation",
}
CATEGORY_ROLE = {
    "deepen_supported": "synthesizer",
    "cross_colony_combinations": "synthesizer",
    "independent_alternatives": "explorer",
    "adversarial_replication": "critic",
    "initial_independent": "explorer",
}


class _CampaignExecutionLease:
    """Hold one OS-released, nonblocking lease for a campaign process."""

    def __init__(self, output: Path) -> None:
        self.path = output / CAMPAIGN_EXECUTION_LOCK
        self.stream = None
        self._windows = os.name == "nt"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        self.stream.seek(0)
        try:
            if self._windows:
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(
                    self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            self.stream.close()
            self.stream = None
            raise V3ReadinessRefusal(
                "Another process already holds the V3 campaign lease") from exc
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if self._windows:
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def _atomic_publish_json(path: Path, value: Any) -> None:
    """Publish deterministic JSON without exposing a partial target file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    with pending.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise V3ReadinessRefusal(
                "Existing atomic V3 artifact is invalid") from exc
        if existing != value:
            raise V3ReadinessRefusal(
                "Existing atomic V3 artifact differs")
        pending.unlink(missing_ok=True)
        return
    os.replace(pending, path)


class V3ProposalWorker(Protocol):
    """One tool-free proposal worker.  It returns inert JSON only."""

    worker_id: str

    def respond(self, packet: dict) -> bytes | str | dict:
        ...


def _aware_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise V3IntegrityStop(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V3IntegrityStop(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3IntegrityStop(f"{label} is not timezone-aware")
    return value


@dataclass(frozen=True)
class DeterministicProposalWorkerV3:
    """Choose the first host-curated seed; make no empirical claim."""

    worker_id: str

    def respond(self, packet: dict) -> dict:
        checked = validate_v3_worker_packet(packet)
        if not checked["seed_plans"]:
            return {
                "protocol": PROTOCOL_V3,
                "task_id": checked["task_id"],
                "action": "abstain",
                "seed_index": None,
                "rationale": "No untested host-registered plan was available.",
                "evidence_ids": [],
                "limitations": [
                    "Deterministic finite search; no model-generated scientific claim."],
                "requested_checks": [],
            }
        return {
            "protocol": PROTOCOL_V3,
            "task_id": checked["task_id"],
            "action": "propose",
            "seed_index": 0,
            "rationale": (
                "Select the next preregistered finite plan for deterministic "
                "offline evaluation; the evaluator, replicator and critic decide evidence."),
            "evidence_ids": [item["evidence_id"] for item in checked["evidence"][:4]],
            "limitations": [
                "Plan selection is a bounded heuristic, not evidence of profitability.",
                "Protected-final data is unavailable to this worker.",
            ],
            "requested_checks": [],
        }


@dataclass(frozen=True)
class OfflineLocalLLMWorkerV3:
    """Explicit adapter for a user-supplied local, tool-free completion runner.

    ``completion`` must accept one validated packet and return exactly one V3
    JSON response.  The adapter declares zero paid use and refuses construction
    when the caller describes a network-enabled runner.  The campaign engine
    invokes it inside its single-inference-process guard.
    """

    worker_id: str
    completion: Callable[[dict], bytes | str | dict]
    runtime_identity_sha256: str
    network_permitted: bool = False
    paid_api_dollars: int = 0

    def __post_init__(self) -> None:
        if self.network_permitted is not False or self.paid_api_dollars != 0:
            raise V3CampaignError("V3 local workers must be offline and zero-paid")
        if (not isinstance(self.runtime_identity_sha256, str)
                or len(self.runtime_identity_sha256) != 64
                or any(char not in "0123456789abcdef"
                       for char in self.runtime_identity_sha256)):
            raise V3CampaignError("Local worker runtime identity must be a SHA-256")
        if not callable(self.completion):
            raise V3CampaignError("Local worker completion runner must be callable")

    def respond(self, packet: dict) -> bytes | str | dict:
        return self.completion(validate_v3_worker_packet(packet))


@dataclass(frozen=True)
class PinnedLocalTextWorkerV3:
    """Concrete adapter around the project's verified GPT-OSS runtime."""

    worker_id: str
    backend: Any
    authorization: Any
    expected_runtime_sha256: str
    artifact_root: Path

    def respond(self, packet: dict) -> dict:
        checked = validate_v3_worker_packet(packet)
        base = Path(self.artifact_root) / checked["task_id"]
        attempt = 1
        while (base / f"attempt-{attempt}").exists():
            attempt += 1
        record = self.backend.run_v3_packet(
            checked, base / f"attempt-{attempt}",
            authorization=self.authorization,
            expected_runtime_sha256=self.expected_runtime_sha256)
        response = record.get("response")
        if not isinstance(response, dict):
            raise V3IntegrityStop("Pinned local V3 worker returned no typed response")
        return response


def allocation_counts(total: int) -> dict[str, int]:
    """Deterministically apply the registered 50/20/15/15 allocation."""
    if type(total) is not int or total < 0:
        raise ValueError("Allocation total must be a nonnegative integer")
    raw = {name: total * ALLOCATION_WEIGHTS[name] for name in ALLOCATION_ORDER}
    result = {name: int(raw[name]) for name in ALLOCATION_ORDER}
    remainder = total - sum(result.values())
    ranking = sorted(
        ALLOCATION_ORDER,
        key=lambda name: (-(raw[name] - result[name]), ALLOCATION_ORDER.index(name)),
    )
    for name in ranking[:remainder]:
        result[name] += 1
    return result


def _plan_summary(record: CandidateRecord) -> dict[str, Any]:
    evaluation = record.evaluation or {}
    candidate = evaluation.get("candidate", {})
    economics = candidate.get("historical_assumed_fill", {})
    scores = candidate.get("forecast_scores", {})
    reference_scores = (evaluation.get("reference", {}) or {}).get("forecast_scores", {})
    promotion = dict(record.promotion or {})
    replication = record.replication or {}
    critic = record.critic or {}
    folds = []
    for value in evaluation.get("folds", []) or []:
        folds.append({
            key: value.get(key) for key in (
                "fold", "trade_count", "total_net_profit", "total_entry_outlay",
                "capital_weighted_return", "mean_trade_return")
        })
    return {
        "candidate_id": record.candidate_id,
        "research_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "colony": record.plan.colony,
        "epoch": record.epoch,
        "capital_weighted_return": economics.get("capital_weighted_return"),
        "trade_count": economics.get("trade_count"),
        "bootstrap_lower_95": (economics.get("bootstrap") or {}).get("lower_95"),
        "brier": scores.get("brier"),
        "crps_f": scores.get("crps_f", scores.get("gaussian_crps_f")),
        "reference_brier": reference_scores.get("brier"),
        "reference_crps_f": reference_scores.get(
            "crps_f", reference_scores.get("gaussian_crps_f")),
        "probability_conservation_passed": scores.get(
            "probability_conservation_passed"),
        "total_net_profit": economics.get("total_net_profit"),
        "total_entry_outlay": economics.get("total_entry_outlay"),
        "mean_trade_return": economics.get("mean_trade_return"),
        "capital_weighted_return_after_removing_most_profitable_day": economics.get(
            "capital_weighted_return_after_removing_most_profitable_day"),
        "bootstrap": dict(economics.get("bootstrap") or {}),
        "fee_scenario": dict(economics.get("fee_scenario") or {}),
        "contribution_breakdowns": dict(
            economics.get("contribution_breakdowns") or {}),
        "folds": folds,
        "cost_stress": dict(evaluation.get("stress_return") or {}),
        "promotion_passed": promotion.get("passed"),
        "rejection_reasons": list(promotion.get("reasons", [])),
        "promotion": promotion,
        "replication": {
            "status": replication.get("status"),
            "verifier_id": replication.get("verifier_id"),
            "checks": dict(replication.get("checks") or {}),
            "differences": list(replication.get("differences") or []),
        },
        "critic": {
            "decision": critic.get("decision"),
            "critic_id": critic.get("critic_id"),
            "checks": dict(critic.get("checks") or {}),
            "unresolved_defects": list(critic.get("unresolved_defects") or []),
        },
        "artifact_sha256s": dict(record.artifact_sha256s or {}),
    }


def _ranking_key(record: CandidateRecord) -> tuple:
    row = _plan_summary(record)
    roi = row["capital_weighted_return"]
    lower = row["bootstrap_lower_95"]
    crps = row["crps_f"]
    return (
        0 if row["promotion_passed"] else 1,
        -(float(roi) if roi is not None else -10**9),
        -(float(lower) if lower is not None else -10**9),
        float(crps) if crps is not None else 10**9,
        record.candidate_id,
    )


def _with_lineage(
    plan: ResearchPlanV3,
    *,
    operator: str,
    parents: Iterable[CandidateRecord] = (),
) -> ResearchPlanV3:
    parent_rows = tuple(parents)
    return replace(
        plan,
        lineage_operator=operator,
        parent_hypothesis_ids=tuple(
            "hyp-" + row.plan.identity[:20] for row in parent_rows),
        parent_plan_sha256s=tuple(row.plan.identity for row in parent_rows),
    )


def _finite_plan_stream(
    *, colony: str, stage: str, data_bundle_version: str,
    data_bundle_sha256: str,
) -> Iterable[ResearchPlanV3]:
    """Enumerate only the registered V3 DSL; invalid combinations are skipped."""
    dimensions = product(
        # The frozen V3 archive has HRRR plus GEFS mean/spread.  GFS/NBM and
        # member-level GEFS remain in the general DSL for future datasets, but
        # are deliberately absent from this campaign's real-data search.
        ("hrrr_gefs_summary", "gefs_summary"),
        ("temperature_only", "intraday_station", "marine_layer", "full_local_weather"),
        ("pooled", "calendar_month", "marine_layer_classifier", "coastal_synoptic_classifier"),
        ("quantile_brackets", "ordered_logistic", "gaussian_mixture", "gaussian_blend"),
        ("isotonic_bracket", "beta_bracket", "gaussian_integer_interval"),
        ("none", "regularized_logit"),
        ("fixed_uncertainty_buffer", "split_conformal"),
        ("12:00", "15:00", "18:00"),
        (0.10, 0.15, 0.20, 0.30),
        ("BOTH", "YES", "NO"),
        (0.0, 0.02, 0.05, 0.10),
        (4.0, 6.0, 8.0, 12.0),
        (0, 10, 25, 50),
        (1, 5, 15, 60),
        (5, 10, 15, 25),
        ((5, 80), (10, 85), (15, 90), (20, 95)),
        (30, 60, 90),
    )
    for values in dimensions:
        (forecast, features, regime, family, calibration, residual, abstention,
         decision_time, threshold, sides, buffer, width, volume, age, spread,
         price_band, regime_days) = values
        try:
            yield make_plan_v3(
                colony=colony, stage=stage,
                data_bundle_version=data_bundle_version,
                data_bundle_sha256=data_bundle_sha256,
                forecast_source_set=forecast, feature_set=features,
                regime_model=regime, probability_family=family,
                calibration_operator=calibration, market_residual_model=residual,
                abstention_operator=abstention, decision_time_utc=decision_time,
                entry_threshold=threshold, sides=sides,
                uncertainty_buffer=buffer, maximum_interval_width_f=width,
                minimum_candle_volume=volume, maximum_price_age_minutes=age,
                maximum_spread_cents=spread,
                entry_price_floor_cents=price_band[0],
                entry_price_ceiling_cents=price_band[1],
                minimum_regime_training_days=regime_days,
            )
        except ValueError:
            continue


def _first_novel_plan(
    engine: V3CampaignEngine, colony: str, stage: str,
    reserved: set[str], *, operator: str = "seed",
    parents: tuple[CandidateRecord, ...] = (),
) -> ResearchPlanV3 | None:
    for plan in _finite_plan_stream(
            colony=colony, stage=stage,
            data_bundle_version=engine.authorization.data_bundle_version,
            data_bundle_sha256=engine.authorization.data_bundle_sha256):
        try:
            plan = _with_lineage(plan, operator=operator, parents=parents)
        except ValueError:
            continue
        if engine.plan_is_denied(plan):
            continue
        fingerprint = plan.novelty_fingerprint
        if fingerprint not in engine.novelty_index and fingerprint not in reserved:
            reserved.add(fingerprint)
            return plan
    return None


def _mutated_parent_plan(
    engine: V3CampaignEngine, parent: CandidateRecord, reserved: set[str],
) -> ResearchPlanV3 | None:
    if engine.plan_is_denied(parent.plan):
        return None
    plan = parent.plan
    mutations = (
        ("entry_threshold", (0.10, 0.15, 0.20, 0.30)),
        ("uncertainty_buffer", (0.0, 0.02, 0.05, 0.10)),
        ("decision_time_utc", ("12:00", "15:00", "18:00")),
        ("allowed_sides", ("BOTH", "YES", "NO")),
        ("minimum_candle_volume", (0, 10, 25, 50)),
        ("maximum_price_age_minutes", (1, 5, 15, 60)),
        ("maximum_spread_cents", (5, 10, 15, 25)),
        ("maximum_interval_width_f", (4.0, 6.0, 8.0, 12.0)),
        ("abstention_operator", ("fixed_uncertainty_buffer", "split_conformal")),
        ("market_residual_model", ("none", "regularized_logit")),
    )
    for field, values in mutations:
        for value in values:
            if value == getattr(plan, field):
                continue
            try:
                candidate = _with_lineage(
                    replace(plan, **{field: value}),
                    operator="continuation", parents=(parent,))
            except ValueError:
                continue
            if engine.plan_is_denied(candidate):
                continue
            fingerprint = candidate.novelty_fingerprint
            if fingerprint not in engine.novelty_index and fingerprint not in reserved:
                reserved.add(fingerprint)
                return candidate
    return _first_novel_plan(
        engine, plan.colony, plan.stage, reserved,
        operator="continuation", parents=(parent,))


def _combined_plan(
    engine: V3CampaignEngine, first: CandidateRecord, second: CandidateRecord,
    reserved: set[str], slot: int,
) -> ResearchPlanV3 | None:
    if (first.plan.colony == second.plan.colony
            or engine.plan_is_denied(first.plan)
            or engine.plan_is_denied(second.plan)):
        return None
    colony = "ensemble_probability" if slot % 2 == 0 else "market_behavior"
    stage = COLONY_STAGE[colony]
    left, right = first.plan, second.plan
    try:
        combined = make_plan_v3(
            colony=colony, stage=stage,
            data_bundle_version=engine.authorization.data_bundle_version,
            data_bundle_sha256=engine.authorization.data_bundle_sha256,
            forecast_source_set=left.forecast_source_set,
            feature_set=left.feature_set,
            regime_model=left.regime_model,
            probability_family=left.probability_family,
            calibration_operator=left.calibration_operator,
            market_residual_model=right.market_residual_model,
            abstention_operator=right.abstention_operator,
            decision_time_utc=right.decision_time_utc,
            entry_threshold=right.entry_threshold,
            sides=right.allowed_sides,
            uncertainty_buffer=right.uncertainty_buffer,
            maximum_interval_width_f=left.maximum_interval_width_f,
            minimum_candle_volume=right.minimum_candle_volume,
            maximum_price_age_minutes=right.maximum_price_age_minutes,
            maximum_spread_cents=right.maximum_spread_cents,
            entry_price_floor_cents=right.entry_price_floor_cents,
            entry_price_ceiling_cents=right.entry_price_ceiling_cents,
            minimum_regime_training_days=left.minimum_regime_training_days,
            lineage_operator="combination",
            parent_hypothesis_ids=(
                "hyp-" + first.plan.identity[:20],
                "hyp-" + second.plan.identity[:20]),
            parent_plan_sha256s=(first.plan.identity, second.plan.identity),
        )
    except ValueError:
        return _first_novel_plan(
            engine, colony, stage, reserved,
            operator="combination", parents=(first, second))
    if engine.plan_is_denied(combined):
        return _first_novel_plan(
            engine, colony, stage, reserved,
            operator="combination", parents=(first, second))
    fingerprint = combined.novelty_fingerprint
    if fingerprint in engine.novelty_index or fingerprint in reserved:
        return _first_novel_plan(
            engine, colony, stage, reserved,
            operator="combination", parents=(first, second))
    reserved.add(fingerprint)
    return combined


def _evidence_for_plan(
    engine: V3CampaignEngine, plan: ResearchPlanV3,
) -> list[dict[str, Any]]:
    evidence = [{
        "evidence_id": "readiness-" + engine.authorization.readiness_sha256[:16],
        "scope": "synthetic" if engine.authorization.synthetic else "weather_training",
        "summary": (
            "Readiness-bound offline data, evaluator, promotion gates and protected-final "
            "denial passed before this campaign ticket was issued."),
        "artifact_sha256": engine.authorization.readiness_sha256,
    }]
    for parent_sha in plan.parent_plan_sha256s:
        parent = next(
            (row for row in engine.candidates.values()
             if row.plan.identity == parent_sha and row.evaluation is not None), None)
        if parent is None or parent.artifact_sha256s is None:
            continue
        summary = _plan_summary(parent)
        evidence.append({
            "evidence_id": "evaluation-" + parent.candidate_id.removeprefix("v3-candidate-"),
            "scope": "synthetic" if engine.authorization.synthetic else "development_evaluation",
            "summary": json.dumps(summary, sort_keys=True, allow_nan=False)[:1800],
            "artifact_sha256": parent.artifact_sha256s["evaluation_sha256"],
        })
    return evidence[:16]


def _packet(
    engine: V3CampaignEngine, *, task_id: str, role: str,
    plans: list[ResearchPlanV3],
) -> dict[str, Any]:
    if not plans:
        raise V3CampaignError("V3 proposal packet requires at least one finite option")
    plan = plans[0]
    if any((item.colony, item.stage, item.parent_hypothesis_ids,
            item.parent_plan_sha256s) != (
                plan.colony, plan.stage, plan.parent_hypothesis_ids,
                plan.parent_plan_sha256s) for item in plans):
        raise V3CampaignError("V3 packet options must share routing and parent lineage")
    packet = {
        "protocol": PROTOCOL_V3,
        "task_id": task_id,
        "campaign_id": engine.authorization.campaign_id,
        "worker_role": role,
        "scope": "synthetic_only" if engine.authorization.synthetic else "development_only",
        "synthetic": engine.authorization.synthetic,
        "question": (
            "Nominate exactly one supplied whole seed plan for deterministic offline "
            "testing. Propose means nominate an unverified hypothesis, not endorse or "
            "deploy it. Evaluation evidence is intentionally unavailable until after "
            "nomination. Differences among seeds, missing performance results, or "
            "uncertainty about which seed is best are not reasons to reject. Reject or "
            "abstain only when every supplied seed is structurally invalid, out of "
            "scope, or a known duplicate. Cite only supplied evidence and do not claim "
            "empirical support before deterministic evaluation."),
        "readiness_sha256": engine.authorization.readiness_sha256,
        "config_sha256": engine.authorization.config_sha256,
        "schema_sha256": engine.authorization.schema_sha256,
        "partition_contract_sha256": engine.authorization.partition_contract_sha256,
        "data_bundle_version": engine.authorization.data_bundle_version,
        "data_bundle_sha256": engine.authorization.data_bundle_sha256,
        "colony": plan.colony,
        "stage": plan.stage,
        "parent_hypothesis_ids": list(plan.parent_hypothesis_ids),
        "parent_plan_sha256s": list(plan.parent_plan_sha256s),
        "evidence": _evidence_for_plan(engine, plan),
        "seed_plans": [item.to_dict() for item in plans],
        "budget_remaining": engine.budget_remaining(),
    }
    # The production backend accepts at most 10,000 canonical packet bytes.
    # Later-epoch parent evidence makes six whole plans larger than that even
    # though the same six-option registry fits in epoch one.  Retain the
    # deterministic option order and remove only complete trailing plans until
    # the packet fits; never abbreviate or recombine a plan.
    from .local_backend import WorkerLimits, build_any_prompt
    limits = WorkerLimits()
    while packet["seed_plans"]:
        try:
            checked = validate_v3_worker_packet(
                packet, max_bytes=limits.max_packet_bytes)
            build_any_prompt(checked, limits)
            return checked
        except V3ResearchProtocolError as exc:
            if str(exc) not in {
                    "V3 research packet exceeds byte limit",
                    "V3 research prompt exceeds context reservation"}:
                raise
            packet["seed_plans"] = packet["seed_plans"][:-1]
    raise V3ResearchProtocolError(
        "V3 research packet or prompt exceeds its production limit with one whole seed plan")


def _read_artifacts(directory: Path) -> dict[str, Any]:
    return {
        name: json.loads((directory / filename).read_text(encoding="utf-8"))
        for name, filename in {
            "compiled": "compiled_manifest.json",
            "predictions": "predictions.json",
            "ledger": "ledger.json",
            "folds": "fold_metrics.json",
            "evaluation": "evaluation.json",
        }.items()
    }


def _campaign_artifact_inventory(directory: Path) -> list[dict[str, Any]]:
    root = Path(directory).resolve()
    manifest_path = root / CAMPAIGN_ARTIFACT_MANIFEST
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise V3IntegrityStop("V3 campaign artifacts may not contain symbolic links")
        if (not path.is_file() or path == manifest_path
                or path.name == CAMPAIGN_EXECUTION_LOCK):
            continue
        relative = path.relative_to(root).as_posix()
        records.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return records


def publish_v3_campaign_artifact_manifest(directory: Path | str) -> dict[str, Any]:
    """Bind every completed development-campaign artifact after the final save."""
    root = Path(directory).resolve()
    summary_path = root / "summary.json"
    recovery_path = root / "recovery-state.json"
    if not summary_path.is_file() or not recovery_path.is_file():
        raise V3IntegrityStop("Completed V3 campaign lacks summary or recovery state")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    recovery_body = {key: value for key, value in recovery.items() if key != "state_sha256"}
    if (summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("protected_final_evaluated") is not False
            or summary.get("actual_orders_placed") is not False
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("state_sha256") != canonical_hash(recovery_body)):
        raise V3IntegrityStop("V3 campaign is not in a verified terminal state")
    body = {
        "manifest_version": CAMPAIGN_ARTIFACT_MANIFEST_VERSION,
        "campaign_id": summary.get("campaign_id"),
        "report_version": summary.get("report_version"),
        "artifacts": _campaign_artifact_inventory(root),
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    value = {**body, "manifest_sha256": canonical_hash(body)}
    write_json(root / CAMPAIGN_ARTIFACT_MANIFEST, value)
    return value


def verify_v3_campaign_artifacts(directory: Path | str) -> dict[str, Any]:
    """Re-hash the complete saved campaign and cross-check its terminal records."""
    root = Path(directory).resolve()
    path = root / CAMPAIGN_ARTIFACT_MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3IntegrityStop("V3 campaign artifact manifest is missing or invalid") from exc
    expected = {
        "manifest_version", "campaign_id", "report_version", "artifacts",
        "network_used", "protected_final_read", "actual_orders_placed",
        "manifest_sha256",
    }
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if (set(manifest) != expected
            or manifest.get("manifest_version") != CAMPAIGN_ARTIFACT_MANIFEST_VERSION
            or manifest.get("report_version") != REPORT_VERSION
            or manifest.get("network_used") is not False
            or manifest.get("protected_final_read") is not False
            or manifest.get("actual_orders_placed") is not False
            or manifest.get("manifest_sha256") != canonical_hash(body)):
        raise V3IntegrityStop("V3 campaign artifact manifest identity differs")
    current = _campaign_artifact_inventory(root)
    if manifest.get("artifacts") != current or not current:
        raise V3IntegrityStop("V3 campaign artifact inventory changed")
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    recovery = json.loads((root / "recovery-state.json").read_text(encoding="utf-8"))
    register = json.loads((root / "candidate-register.json").read_text(encoding="utf-8"))
    coverage = json.loads((root / "search-coverage.json").read_text(encoding="utf-8"))
    recovery_body = {key: value for key, value in recovery.items() if key != "state_sha256"}
    if (summary.get("campaign_id") != manifest.get("campaign_id")
            or summary.get("report_version") != REPORT_VERSION
            or summary.get("status") != "OFFLINE_CAMPAIGN_COMPLETE"
            or summary.get("ranked_candidates") != register
            or summary.get("coverage") != coverage
            or summary.get("protected_final_evaluated") is not False
            or summary.get("actual_orders_placed") is not False
            or recovery.get("campaign_id") != summary.get("campaign_id")
            or recovery.get("phase") != "COMPLETE"
            or recovery.get("state_sha256") != canonical_hash(recovery_body)
            or recovery.get("protected_final_evaluated") is not False
            or recovery.get("actual_orders_placed") is not False):
        raise V3IntegrityStop("V3 campaign terminal artifacts disagree")
    return {
        "status": "PASS",
        "campaign_id": summary["campaign_id"],
        "scientific_conclusion": summary.get("scientific_conclusion"),
        "artifact_count": len(current),
        "manifest_sha256": manifest["manifest_sha256"],
        "protected_final_read": False,
        "actual_orders_placed": False,
    }


def _independent_replication(
    engine: V3CampaignEngine, record: CandidateRecord,
    evaluator: OfflineCandidateEvaluatorV3,
    primary_directory: Path, replication_directory: Path,
) -> dict[str, Any]:
    """Recompute discovery evidence through the isolated numerical verifier."""
    primary_check = verify_candidate_evaluation_artifacts(
        primary_directory, expected_plan_sha256=record.plan.identity,
        expected_dataset_id=engine.authorization.data_bundle_version)
    recomputed = independently_recompute_candidate(
        plan=record.plan,
        inputs=evaluator.inputs,
        fee_scenario=evaluator.fee_scenario,
        primary_artifact_directory=primary_directory,
        output_directory=replication_directory,
        partition_contract_sha256=engine.authorization.partition_contract_sha256,
    )
    checks = dict(recomputed["checks"])
    differences = list(recomputed["differences"])
    if (primary_check.get("artifact_sha256s") != record.artifact_sha256s
            and "canonical_artifact_hashes" not in differences):
        differences.append("canonical_artifact_hashes")
    if primary_check.get("protected_final_read") is not False:
        checks["partition_roles_and_nonoverlap"] = False
        if "partition_roles_and_nonoverlap" not in differences:
            differences.append("partition_roles_and_nonoverlap")
    return {
        "contract_version": REPLICATION_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "verifier_id": "independent-numerical-replicator-v3",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "source_artifact_sha256s": dict(record.artifact_sha256s or {}),
        "checks": checks,
        "status": "PASS" if all(checks.values()) and not differences else "FAIL",
        "differences": differences,
    }


def _critic_review(
    engine: V3CampaignEngine, record: CandidateRecord,
    primary_directory: Path,
) -> dict[str, Any]:
    """Apply independent deterministic falsification checks to saved evidence."""
    defects: list[str] = []
    try:
        verification = verify_candidate_evaluation_artifacts(
            primary_directory, expected_plan_sha256=record.plan.identity,
            expected_dataset_id=engine.authorization.data_bundle_version)
        artifacts = _read_artifacts(primary_directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        verification = None
        artifacts = {}
        defects.append("artifact_verification:" + type(exc).__name__)
    evaluation = artifacts.get("evaluation", {})
    candidate = evaluation.get("candidate", {})
    scores = candidate.get("forecast_scores", {})
    economics = candidate.get("historical_assumed_fill", {})
    audit = candidate.get("partition_audit", {})
    ledger = artifacts.get("ledger", {})
    expected_audit = {
        "partition_contract_sha256": engine.authorization.partition_contract_sha256,
        "weather_model_fit_source": "weather_training_through_2024_12_31",
        "market_layer_fit_source": "fixed_2025_01_05_through_2025_02_03_calibration_prefix",
        "score_source": "fixed_2025_02_04_through_2025_06_30_development_evaluation",
        "calibration_prefix_scored": False,
        "scored_outcomes_used_for_fit_or_thresholds": False,
    }
    checks = {
        "look_ahead": audit == expected_audit,
        "settlement": verification is not None,
        "probability": scores.get("probability_conservation_passed") is True,
        "availability": (
            verification is not None
            and verification.get("network_used") is False
            and verification.get("protected_final_read") is False),
        "fill": (
            economics.get("assumed_fill") is True
            and economics.get("actual_orders_placed") is False
            and ledger.get("historical_assumed_fill_only") is True),
        "concentration": (
            isinstance(economics.get("contribution_breakdowns"), dict)
            and set((economics.get("contribution_breakdowns") or {}))
            == {"calendar_month", "weather_regime", "entry_price_band",
                "purchase_side", "decision_time"}),
        "identity": (
            candidate.get("research_plan_sha256") == record.plan.identity
            and candidate.get("dataset_id") == engine.authorization.data_bundle_version),
        "partition_overlap": audit == expected_audit,
    }
    defects.extend(name for name, passed in checks.items() if not passed)
    return {
        "contract_version": CRITIC_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "critic_id": "independent-adversarial-critic-v3",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "evidence_sha256s": dict(record.artifact_sha256s or {}),
        "checks": checks,
        "decision": "NONREJECT" if all(checks.values()) and not defects else "REJECT",
        "unresolved_defects": defects,
    }


@dataclass
class BoundedV3Orchestrator:
    engine: V3CampaignEngine
    primary_evaluator: OfflineCandidateEvaluatorV3
    replication_evaluator: OfflineCandidateEvaluatorV3
    output_root: Path
    worker_factory: Callable[[str, str], V3ProposalWorker] | None = None
    phase: str = "BETWEEN_EPOCHS"
    queue: list[dict[str, Any]] = None  # type: ignore[assignment]
    next_queue_index: int = 0
    coverage: dict[str, Any] = None  # type: ignore[assignment]
    started_at_utc: str | None = None
    completed_at_utc: str | None = None

    def __post_init__(self) -> None:
        self.output_root = self.engine.authorization.assert_development_path(
            self.output_root)
        if self.output_root.parent.name != "campaigns_v3":
            raise V3CampaignError("V3 output must use runs/campaigns_v3/<campaign-id>")
        if self.output_root.name != self.engine.authorization.campaign_id:
            raise V3CampaignError("V3 output directory must equal the campaign ID")
        self.output_root.mkdir(parents=True, exist_ok=True)
        if self.started_at_utc is None:
            self.started_at_utc = datetime.now(timezone.utc).isoformat()
        else:
            self.started_at_utc = _aware_timestamp(
                self.started_at_utc, "V3 campaign start timestamp")
        if self.completed_at_utc is not None:
            self.completed_at_utc = _aware_timestamp(
                self.completed_at_utc, "V3 campaign completion timestamp")
        if self.queue is None:
            self.queue = []
        if self.coverage is None:
            self.coverage = {
                "colonies": {name: {"proposed": 0, "admitted": 0, "executed": 0,
                                      "rejected": 0, "champions": 0}
                             for name in COLONY_ORDER},
                "epochs": {},
                "allocation_weights": dict(ALLOCATION_WEIGHTS),
                "allocation_decisions": [],
                "duplicate_proposals": 0,
                "nonproposal_responses": 0,
                "integrity_failures": [],
            }
        self.worker_factory = self.worker_factory or (
            lambda worker_id, _role: DeterministicProposalWorkerV3(worker_id))

    @property
    def state_path(self) -> Path:
        return self.output_root / "recovery-state.json"

    def _engine_state(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": max(0, int(self.engine.clock() - self.engine.started_at)),
            "current_epoch": self.engine.current_epoch,
            "model_calls": self.engine.model_calls,
            "model_context_tokens_reserved": self.engine.model_context_tokens_reserved,
            "admitted_candidates": self.engine.admitted_candidates,
            "executed_candidates": self.engine.executed_candidates,
            "empty_epochs": self.engine.empty_epochs,
            "stopped_reason": self.engine.stopped_reason,
            "champion_candidate_id": self.engine.champion_candidate_id,
            "final_authorization": self.engine.final_authorization,
            "novelty_index": dict(self.engine.novelty_index),
            "duplicate_proposals": list(self.engine.duplicate_proposals),
            "nonproposal_responses": list(self.engine.nonproposal_responses),
            "epoch_admissions": {str(key): value for key, value in self.engine.epoch_admissions.items()},
            "transient_retries": dict(self.engine.transient_retries),
            "candidates": {
                key: {
                    "candidate_id": value.candidate_id,
                    "plan": value.plan.to_dict(),
                    "discovery_worker_id": value.discovery_worker_id,
                    "epoch": value.epoch,
                    "artifact_sha256s": value.artifact_sha256s,
                    "evaluation": value.evaluation,
                    "promotion": value.promotion,
                    "replication": value.replication,
                    "critic": value.critic,
                }
                for key, value in sorted(self.engine.candidates.items())
            },
        }

    def save_recovery(self) -> dict[str, Any]:
        body = {
            "recovery_version": RECOVERY_VERSION,
            "orchestrator_version": ORCHESTRATOR_VERSION,
            "campaign_id": self.engine.authorization.campaign_id,
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "ticket_sha256": self.engine.authorization.ticket_sha256,
            "phase": self.phase,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "queue": self.queue,
            "next_queue_index": self.next_queue_index,
            "coverage": self.coverage,
            "engine": self._engine_state(),
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        }
        value = {**body, "state_sha256": canonical_hash(body)}
        write_json(self.state_path, value)
        return value

    @classmethod
    def recover(
        cls, *, engine: V3CampaignEngine,
        primary_evaluator: OfflineCandidateEvaluatorV3,
        replication_evaluator: OfflineCandidateEvaluatorV3,
        output_root: Path,
        worker_factory: Callable[[str, str], V3ProposalWorker] | None = None,
    ) -> "BoundedV3Orchestrator":
        path = Path(output_root) / "recovery-state.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        body = {key: item for key, item in value.items() if key != "state_sha256"}
        if (value.get("state_sha256") != canonical_hash(body)
                or value.get("recovery_version") != RECOVERY_VERSION
                or value.get("orchestrator_version") != ORCHESTRATOR_VERSION
                or value.get("campaign_id") != engine.authorization.campaign_id
                or value.get("readiness_sha256") != engine.authorization.readiness_sha256
                or value.get("ticket_sha256") != engine.authorization.ticket_sha256
                or value.get("protected_final_evaluated") is not False
                or value.get("actual_orders_placed") is not False):
            raise V3IntegrityStop("V3 recovery state is modified or differently bound")
        state = value.get("engine")
        if not isinstance(state, dict):
            raise V3IntegrityStop("V3 recovery engine state is missing")
        started_at_utc = _aware_timestamp(
            value.get("started_at_utc"), "V3 recovery start timestamp")
        completed_raw = value.get("completed_at_utc")
        completed_at_utc = (None if completed_raw is None else _aware_timestamp(
            completed_raw, "V3 recovery completion timestamp"))
        if value.get("phase") == "COMPLETE" and completed_at_utc is None:
            # The engine can reach a terminal state one atomic save before report
            # rendering. A same-campaign recovery may finish that transaction.
            completed_at_utc = datetime.now(timezone.utc).isoformat()
        engine.started_at = engine.clock() - int(state["elapsed_seconds"])
        for name in (
                "current_epoch", "model_calls", "model_context_tokens_reserved",
                "admitted_candidates", "executed_candidates", "empty_epochs"):
            raw = state.get(name)
            if type(raw) is not int or raw < 0:
                raise V3IntegrityStop("V3 recovery counter is invalid")
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
            plan = ResearchPlanV3.from_dict(raw["plan"])
            if candidate_id != "v3-candidate-" + plan.identity[:20]:
                raise V3IntegrityStop("V3 recovery candidate identity differs")
            engine.candidates[candidate_id] = CandidateRecord(
                candidate_id=candidate_id, plan=plan,
                discovery_worker_id=raw["discovery_worker_id"], epoch=raw["epoch"],
                artifact_sha256s=raw.get("artifact_sha256s"),
                evaluation=raw.get("evaluation"), promotion=raw.get("promotion"),
                replication=raw.get("replication"), critic=raw.get("critic"),
            )
        expected_novelty = {
            row.plan.novelty_fingerprint: row.candidate_id
            for row in engine.candidates.values()}
        reservation = (engine.budget.local_reserved_context_tokens
                       // engine.budget.maximum_local_model_calls)
        if (engine.novelty_index != expected_novelty
                or engine.admitted_candidates != len(engine.candidates)
                or engine.executed_candidates != sum(
                    row.evaluation is not None for row in engine.candidates.values())
                or engine.model_calls > engine.budget.maximum_local_model_calls
                or engine.model_context_tokens_reserved != engine.model_calls * reservation
                or engine.current_epoch > engine.budget.maximum_epochs):
            raise V3IntegrityStop("V3 recovery state violates a registered budget or identity")
        orchestrator = cls(
            engine, primary_evaluator, replication_evaluator, Path(output_root),
            worker_factory=worker_factory,
            phase=value["phase"], queue=list(value["queue"]),
            next_queue_index=value["next_queue_index"],
            coverage=dict(value["coverage"]),
            started_at_utc=started_at_utc,
            completed_at_utc=completed_at_utc,
        )
        return orchestrator

    def _initial_queue(self) -> list[dict[str, Any]]:
        reserved: set[str] = set()
        queue: list[dict[str, Any]] = []
        slots = self.engine.budget.maximum_new_candidates_per_epoch
        for index in range(slots):
            colony = COLONY_ORDER[index % len(COLONY_ORDER)]
            plan = _first_novel_plan(
                self.engine, colony, COLONY_STAGE[colony], reserved)
            if plan is not None:
                options = self._expand_options(plan, reserved)
                if options:
                    queue.append(self._queue_item(
                        plan, "initial_independent", index + 1, options=options))
        return queue

    def _expand_options(
        self, primary: ResearchPlanV3, reserved: set[str], *, maximum: int = 6,
    ) -> list[ResearchPlanV3]:
        if self.engine.plan_is_denied(primary):
            return []
        options = [primary]
        parents = tuple(
            row for parent_sha in primary.parent_plan_sha256s
            for row in self.engine.candidates.values()
            if row.plan.identity == parent_sha)
        for base in _finite_plan_stream(
                colony=primary.colony, stage=primary.stage,
                data_bundle_version=self.engine.authorization.data_bundle_version,
                data_bundle_sha256=self.engine.authorization.data_bundle_sha256):
            if len(options) >= maximum:
                break
            try:
                candidate = _with_lineage(
                    base, operator=primary.lineage_operator, parents=parents)
            except ValueError:
                continue
            if self.engine.plan_is_denied(candidate):
                continue
            fingerprint = candidate.novelty_fingerprint
            if (fingerprint in self.engine.novelty_index
                    or fingerprint in reserved):
                continue
            reserved.add(fingerprint)
            options.append(candidate)
        return options

    def _queue_item(
        self, plan: ResearchPlanV3, category: str, slot: int, *,
        options: list[ResearchPlanV3] | None = None,
    ) -> dict[str, Any]:
        task_id = f"e{self.engine.current_epoch:02d}-{category[:12]}-{slot:02d}"
        option_rows = [plan] if options is None else options
        return {
            "task_id": task_id,
            "category": category,
            "role": CATEGORY_ROLE[category],
            "plan": plan.to_dict(),
            "options": [item.to_dict() for item in option_rows],
            "status": "PENDING",
            "candidate_id": None,
        }

    def _allocated_queue(
        self, *, eligible_parent_pool_sha256: str | None = None,
    ) -> list[dict[str, Any]]:
        ranked = sorted(
            (row for row in self.engine.candidates.values()
             if (row.evaluation is not None and row.promotion is not None
                 and not self.engine.plan_is_denied(row.plan))),
            key=_ranking_key)
        if not ranked:
            return []
        total = min(
            self.engine.budget.maximum_new_candidates_per_epoch,
            self.engine.budget.maximum_distinct_executed_candidates
            - self.engine.admitted_candidates)
        counts = allocation_counts(total)
        reserved: set[str] = set()
        selected: list[tuple[str, ResearchPlanV3]] = []
        for slot in range(counts["deepen_supported"]):
            parent = ranked[slot % len(ranked)]
            plan = _mutated_parent_plan(self.engine, parent, reserved)
            if plan is not None:
                selected.append(("deepen_supported", plan))
        for slot in range(counts["cross_colony_combinations"]):
            first = ranked[slot % len(ranked)]
            distinct_colony_parents = [
                row for row in ranked
                if row.plan.colony != first.plan.colony
            ]
            if not distinct_colony_parents:
                continue
            second = distinct_colony_parents[slot % len(distinct_colony_parents)]
            plan = _combined_plan(self.engine, first, second, reserved, slot)
            if plan is not None:
                selected.append(("cross_colony_combinations", plan))
        for slot in range(counts["independent_alternatives"]):
            colony = COLONY_ORDER[(self.engine.current_epoch + slot) % len(COLONY_ORDER)]
            plan = _first_novel_plan(
                self.engine, colony, COLONY_STAGE[colony], reserved)
            if plan is not None:
                selected.append(("independent_alternatives", plan))
        for slot in range(counts["adversarial_replication"]):
            parent = ranked[-(slot % len(ranked)) - 1]
            plan = _first_novel_plan(
                self.engine, "adversarial_alternatives",
                COLONY_STAGE["adversarial_alternatives"], reserved,
                operator="alternative", parents=(parent,))
            if plan is not None:
                selected.append(("adversarial_replication", plan))

        # Preserve the original allocation record.  If an operator family is
        # exhausted, fill a remaining bounded slot with an independent novel
        # registered plan and label that fact separately.
        fill_index = 0
        while len(selected) < total:
            colony = COLONY_ORDER[fill_index % len(COLONY_ORDER)]
            fill_index += 1
            plan = _first_novel_plan(
                self.engine, colony, COLONY_STAGE[colony], reserved)
            if plan is None:
                if fill_index > len(COLONY_ORDER) * 2:
                    break
                continue
            selected.append(("independent_alternatives", plan))
        decision = {
            "epoch": self.engine.current_epoch,
            "weights": dict(ALLOCATION_WEIGHTS),
            "requested_counts": counts,
            "selected_counts": {
                name: sum(category == name for category, _ in selected)
                for name in ALLOCATION_ORDER},
            "candidate_plan_sha256s": [plan.identity for _, plan in selected],
            "source_candidate_ids": [row.candidate_id for row in ranked[:10]],
        }
        if eligible_parent_pool_sha256 is not None:
            decision["eligible_parent_pool_sha256"] = eligible_parent_pool_sha256
        decision["decision_sha256"] = canonical_hash(decision)
        self.coverage["allocation_decisions"].append(decision)
        queue = []
        for index, (category, plan) in enumerate(selected):
            options = self._expand_options(plan, reserved)
            if options:
                queue.append(self._queue_item(
                    plan, category, index + 1, options=options))
        return queue

    def _rebuild_continuation_epoch(self) -> None:
        """Replace the inert failed queue with one registered fresh allocation."""
        path = self.output_root / CONTINUATION_IMPORT_FILENAME
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            verify_continuation_import_manifest(
                root=self.engine.authorization.root, manifest=manifest,
                allow_existing_claim=True)
            rebuild = manifest["continuation"]["epoch_two_rebuild"]
            if (self.phase != "REBUILD_EPOCH_ALLOCATION"
                    or self.engine.current_epoch != 2
                    or self.engine.stopped_reason is not None
                    or self.engine.epoch_admissions.get(2) != 0
                    or self.queue != [] or self.next_queue_index != 0):
                raise V3ContinuationError(
                    "Successor state is not at the registered epoch-two rebuild point")
            source_decisions = [
                row for row in self.coverage.get("allocation_decisions", [])
                if isinstance(row, dict) and row.get("epoch") == 2]
            if (len(source_decisions) != 1
                    or canonical_hash(source_decisions[0])
                    != rebuild["superseded_source_allocation_sha256"]):
                raise V3ContinuationError(
                    "Superseded epoch-two allocation identity differs")
            rebuilt_queue = self._allocated_queue(
                eligible_parent_pool_sha256=(
                    rebuild["eligible_parent_pool_sha256"]))
            if len(self.coverage["allocation_decisions"]) < 2:
                raise V3ContinuationError(
                    "Continuation allocator did not record an epoch-two decision")
            decision = self.coverage["allocation_decisions"].pop()
            verification = verify_epoch_two_rebuild(
                manifest=manifest, allocation_decision=decision,
                queue=rebuilt_queue)
            packet_preflight = []
            for item in rebuilt_queue:
                plans = [ResearchPlanV3.from_dict(value)
                         for value in item["options"]]
                packet = _packet(
                    self.engine, task_id=item["task_id"], role=item["role"],
                    plans=plans)
                encoded = json.dumps(
                    packet, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True, allow_nan=False).encode("utf-8")
                packet_preflight.append({
                    "task_id": item["task_id"],
                    "packet_sha256": canonical_hash(packet),
                    "canonical_packet_bytes": len(encoded),
                    "seed_count_after_preflight": len(packet["seed_plans"]),
                })
            journal_body = {
                "journal_version": "klax-v3-epoch-two-rebuild-v1",
                "campaign_id": self.engine.authorization.campaign_id,
                "continuation_manifest_sha256": manifest["manifest_sha256"],
                "superseded_source_allocation_sha256": rebuild[
                    "superseded_source_allocation_sha256"],
                "allocation_decision": decision,
                "queue": rebuilt_queue,
                "packet_preflight": packet_preflight,
                "verification": verification,
                "network_used": False,
                "protected_final_read": False,
                "actual_orders_placed": False,
            }
            journal = {
                **journal_body,
                "journal_sha256": canonical_hash(journal_body),
            }
            journal_path = self.output_root / "epoch-02-rebuild.json"
            if journal_path.is_file():
                existing = json.loads(journal_path.read_text(encoding="utf-8"))
                if existing != journal:
                    raise V3ContinuationError(
                        "Existing epoch-two rebuild journal differs")
            else:
                _atomic_publish_json(journal_path, journal)
            self.coverage["allocation_decisions"].append(decision)
            self.coverage["superseded_allocation_decisions"] = source_decisions
            self.queue = rebuilt_queue
            self.coverage["continuation_import"] = {
                "manifest_path": path.relative_to(
                    self.engine.authorization.root).as_posix(),
                "manifest_sha256": manifest["manifest_sha256"],
                "source_campaign_id": manifest["source"]["campaign_id"],
                "source_state_sha256": manifest["continuation"][
                    "source_state_sha256"],
                "successor_import_state_sha256": manifest["continuation"][
                    "successor_import_state_sha256"],
                "epoch_two_rebuild": verification,
                "rebuild_journal_path": journal_path.relative_to(
                    self.engine.authorization.root).as_posix(),
                "rebuild_journal_sha256": journal["journal_sha256"],
            }
            self.next_queue_index = 0
            self.phase = "EXECUTING_EPOCH"
            self.coverage["epochs"]["2"] = {
                "planned": len(self.queue), "reviewed": 0, "admitted": 0,
                "allocation": dict(ALLOCATION_WEIGHTS),
                "continuation_rebuild": True,
            }
            self.save_recovery()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, V3IntegrityStop):
                raise
            self.coverage.setdefault("integrity_failures", []).append({
                "stage": "continuation_rebuild",
                "task_id": None,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
            })
            raise V3IntegrityStop(
                "V3 continuation epoch-two rebuild failed closed: "
                + type(exc).__name__) from exc

    def _save_task(self, item: dict[str, Any], packet: dict, raw: Any, result: dict) -> None:
        directory = self.output_root / "tasks" / item["task_id"]
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / "packet.json", packet)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {"invalid_raw_text": raw}
        write_json(directory / "response.json", raw)
        write_json(directory / "admission.json", result)

    def _execute_and_review(self, item: dict[str, Any]) -> None:
        candidate_id = item["candidate_id"]
        record = self.engine.candidates[candidate_id]
        if record.evaluation is None:
            try:
                self.engine.execute_candidate(candidate_id, self.primary_evaluator)
            except V3IntegrityStop:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.coverage.setdefault("integrity_failures", []).append({
                    "stage": "primary_evaluation", "candidate_id": candidate_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                })
                self.engine.fail_integrity(
                    "Primary V3 evaluation failed closed: " + type(exc).__name__)
            self.coverage["colonies"][record.plan.colony]["executed"] += 1
            item["status"] = "EVALUATED"
            self.save_recovery()
        if record.promotion is None:
            primary_dir = Path(self.primary_evaluator.artifact_root) / record.plan.identity
            replication_dir = Path(self.replication_evaluator.artifact_root) / record.plan.identity
            try:
                replication = _independent_replication(
                    self.engine, record, self.replication_evaluator,
                    primary_dir, replication_dir)
                critic = _critic_review(self.engine, record, primary_dir)
            except V3IntegrityStop:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.coverage.setdefault("integrity_failures", []).append({
                    "stage": "independent_review", "candidate_id": candidate_id,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                })
                self.engine.fail_integrity(
                    "Independent V3 review failed closed: " + type(exc).__name__)
            review_dir = self.output_root / "reviews" / candidate_id
            write_json(review_dir / "replication.json", replication)
            write_json(review_dir / "critic.json", critic)
            gate = self.engine.review_candidate(candidate_id, replication, critic)
            write_json(review_dir / "promotion.json", gate)
            if gate["passed"]:
                self.coverage["colonies"][record.plan.colony]["champions"] += 1
            else:
                self.coverage["colonies"][record.plan.colony]["rejected"] += 1
            item["status"] = "REVIEWED"
            self.save_recovery()

    def _process_item(self, item: dict[str, Any]) -> None:
        plans = [ResearchPlanV3.from_dict(value)
                 for value in item.get("options", [item["plan"]])]
        plan = plans[0]
        colony = self.coverage["colonies"][plan.colony]
        if item["status"] == "PENDING":
            role = item["role"]
            try:
                packet = _packet(
                    self.engine, task_id=item["task_id"], role=role, plans=plans)
            except V3ResearchProtocolError as exc:
                self.coverage.setdefault("integrity_failures", []).append({
                    "stage": "worker_packet",
                    "task_id": item["task_id"],
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                })
                self.engine.fail_integrity(
                    "V3 worker packet failed closed: " + str(exc)[:300])
            worker_id = f"{role}-{plan.colony}-e{self.engine.current_epoch:02d}"
            worker = self.worker_factory(worker_id, role)  # type: ignore[misc]
            if worker.worker_id != worker_id:
                raise V3IntegrityStop("Worker identity differs from deterministic assignment")
            holder: dict[str, Any] = {}

            def respond(checked: dict) -> Any:
                holder["raw"] = worker.respond(checked)
                return holder["raw"]

            try:
                result = self.engine.dispatch_worker(
                    worker.worker_id, packet, respond,
                    on_reserved=self.save_recovery)
            except V3BudgetStop:
                raise
            except V3IntegrityStop as exc:
                cause = exc.__cause__
                self.coverage.setdefault("integrity_failures", []).append({
                    "stage": "worker_dispatch",
                    "task_id": item["task_id"],
                    "exception_type": type(cause or exc).__name__,
                    "exception_message": str(exc)[:500],
                })
                raise
            except (OSError, RuntimeError) as exc:
                self.coverage.setdefault("integrity_failures", []).append({
                    "stage": "local_worker", "task_id": item["task_id"],
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                })
                self.engine.fail_integrity(
                    "Local V3 worker failed closed: " + type(exc).__name__)
            self._save_task(item, packet, holder.get("raw"), result)
            if result["status"] == "ADMITTED":
                colony["proposed"] += 1
                item["candidate_id"] = result["candidate_id"]
                item["status"] = "ADMITTED"
                admitted_colony = self.engine.candidates[
                    result["candidate_id"]].plan.colony
                self.coverage["colonies"][admitted_colony]["admitted"] += 1
            elif result["status"] == "DUPLICATE_STRUCTURE":
                colony["proposed"] += 1
                item["status"] = "DUPLICATE"
                self.coverage["duplicate_proposals"] += 1
            else:
                item["status"] = "NONPROPOSAL"
                self.coverage["nonproposal_responses"] += 1
            self.save_recovery()
        if item["status"] in {"ADMITTED", "EVALUATED"}:
            self._execute_and_review(item)

    def run(self) -> dict[str, Any]:
        """Execute until a registered stop; never open the protected final."""
        try:
            while self.engine.stopped_reason is None:
                if self.phase == "REBUILD_EPOCH_ALLOCATION":
                    self._rebuild_continuation_epoch()
                if self.phase == "BETWEEN_EPOCHS":
                    self.engine.begin_epoch()
                    self.queue = (self._initial_queue() if self.engine.current_epoch == 1
                                  else self._allocated_queue())
                    self.next_queue_index = 0
                    self.phase = "EXECUTING_EPOCH"
                    self.coverage["epochs"][str(self.engine.current_epoch)] = {
                        "planned": len(self.queue), "reviewed": 0,
                        "admitted": 0, "allocation": (
                            "initial_independent" if self.engine.current_epoch == 1
                            else dict(ALLOCATION_WEIGHTS)),
                    }
                    self.save_recovery()
                while (self.next_queue_index < len(self.queue)
                       and self.engine.stopped_reason is None):
                    item = self.queue[self.next_queue_index]
                    self._process_item(item)
                    epoch = self.coverage["epochs"][str(self.engine.current_epoch)]
                    epoch["reviewed"] = sum(
                        row["status"] == "REVIEWED" for row in self.queue)
                    epoch["admitted"] = sum(
                        row["status"] in {"ADMITTED", "EVALUATED", "REVIEWED"}
                        for row in self.queue)
                    self.next_queue_index += 1
                    self.save_recovery()
                if self.engine.stopped_reason is None:
                    self.engine.finish_epoch()
                self.phase = "COMPLETE" if self.engine.stopped_reason else "BETWEEN_EPOCHS"
                self.save_recovery()
            return self._finalize()
        except (V3BudgetStop, V3IntegrityStop) as exc:
            if isinstance(exc, V3IntegrityStop) and self.engine.stopped_reason is None:
                try:
                    self.engine.fail_integrity(str(exc))
                except V3IntegrityStop:
                    pass
            self.phase = "COMPLETE"
            self.save_recovery()
            return self._finalize()

    def _finalize(self) -> dict[str, Any]:
        # An integrity stop may interrupt an item before the normal loop-level
        # checkpoint.  Reconcile durable task states so the terminal report does
        # not erase admissions or completed reviews that already occurred.
        if self.queue and str(self.engine.current_epoch) in self.coverage["epochs"]:
            epoch = self.coverage["epochs"][str(self.engine.current_epoch)]
            epoch["reviewed"] = sum(
                row["status"] == "REVIEWED" for row in self.queue)
            epoch["admitted"] = sum(
                row["status"] in {"ADMITTED", "EVALUATED", "REVIEWED"}
                for row in self.queue)
        if self.completed_at_utc is None:
            self.completed_at_utc = datetime.now(timezone.utc).isoformat()
        continuation_path = self.output_root / CONTINUATION_IMPORT_FILENAME
        continuation_manifest = None
        continuation_required = (
            self.engine.authorization.continuation_import_state_path is not None)
        if continuation_required and not continuation_path.is_file():
            raise V3IntegrityStop(
                "Authorized V3 continuation manifest is missing at finalization")
        if continuation_path.is_file():
            continuation_manifest = json.loads(
                continuation_path.read_text(encoding="utf-8"))
            try:
                verify_continuation_import_manifest(
                    root=self.engine.authorization.root,
                    manifest=continuation_manifest,
                    allow_existing_claim=True)
                _verify_continuation_recovery_floor(
                    self, continuation_manifest)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise V3IntegrityStop(
                    "V3 continuation evidence changed before finalization") from exc
        if self.engine.champion_candidate_id is not None and self.engine.final_authorization is None:
            authorization = self.engine.authorize_protected_final()
            write_json(self.output_root / "protected-final-authorization.json", authorization)
            self.save_recovery()
        all_candidates = sorted(self.engine.candidates.values(), key=_ranking_key)
        quarantined_candidates = [
            row for row in all_candidates if self.engine.plan_is_denied(row.plan)]
        candidates = [
            row for row in all_candidates if not self.engine.plan_is_denied(row.plan)]
        champion = (self.engine.candidates[self.engine.champion_candidate_id]
                    if self.engine.champion_candidate_id else None)
        if champion is not None:
            conclusion = "DEVELOPMENT_CHAMPION"
        elif self.engine.stopped_reason == (
                "required_data_integrity_replication_critic_or_resource_boundary_failure"):
            conclusion = "INSUFFICIENT_EVIDENCE"
        else:
            conclusion = "NO_IMPROVEMENT_WITHIN_REGISTERED_BUDGET"
        report = {
            "report_version": REPORT_VERSION,
            "status": "OFFLINE_CAMPAIGN_COMPLETE",
            "architecture_version": 3,
            "campaign_id": self.engine.authorization.campaign_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "scientific_conclusion": conclusion,
            "stopped_reason": self.engine.stopped_reason,
            "six_registered_colonies": list(COLONY_ORDER),
            "allocation_weights": dict(ALLOCATION_WEIGHTS),
            "budget": asdict(self.engine.budget),
            "budget_used": {
                "epochs": self.engine.current_epoch,
                "local_model_calls": self.engine.model_calls,
                "reserved_context_tokens": self.engine.model_context_tokens_reserved,
                "admitted_candidates": self.engine.admitted_candidates,
                "executed_candidates": self.engine.executed_candidates,
                "paid_api_dollars": 0,
            },
            "coverage": self.coverage,
            "champion": None if champion is None else _plan_summary(champion),
            "ranked_candidates": [_plan_summary(row) for row in candidates],
            "quarantined_historical_candidates": [
                _plan_summary(row) for row in quarantined_candidates],
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "ticket_sha256": self.engine.authorization.ticket_sha256,
            "data_bundle_version": self.engine.authorization.data_bundle_version,
            "data_bundle_sha256": self.engine.authorization.data_bundle_sha256,
            "protected_final_evaluated": False,
            "protected_final_authorization_issued": self.engine.final_authorization is not None,
            "protected_final_evaluations_remaining": (
                1 if self.engine.final_authorization is not None else 0),
            "actual_orders_placed": False,
            "actual_account_gains_measured": False,
            "profitability_claimed": False,
            "execution_evidence_grade": {
                "minute_candles": "B_aggregated_historical_quote_evidence",
                "public_trades": "C_execution_proxy_without_depth_or_queue_position",
                "fill_interpretation": "historical_assumed_fill_only",
            },
            "limitations": [
                "All economic results are historical assumed-fill simulations.",
                "The historical fee schedule remains an explicitly unverified proxy.",
                "Discovery used only training, fixed calibration-prefix and scored development evidence.",
                "The protected final interval was not read or evaluated by this campaign.",
                "A bounded negative result cannot be extended by searching past the registered limits.",
            ],
        }
        if continuation_path.is_file():
            continuation = continuation_manifest
            assert continuation is not None
            report["continuation_import"] = {
                "path": continuation_path.relative_to(
                    self.engine.authorization.root).as_posix(),
                "sha256": sha256_file(continuation_path),
                "manifest_sha256": continuation.get("manifest_sha256"),
                "source_campaign_id": (
                    continuation.get("source") or {}).get("campaign_id"),
                "spent_budget_preserved": True,
                "failed_epoch_two_queue_reused": False,
            }
        write_json(self.output_root / "candidate-register.json", report["ranked_candidates"])
        write_json(self.output_root / "search-coverage.json", self.coverage)
        write_json(self.output_root / "summary.json", report)
        lines = [
            "# KLAX V3 bounded offline campaign", "",
            f"- Campaign: `{report['campaign_id']}`",
            f"- Started: `{report['started_at_utc']}`",
            f"- Completed: `{report['completed_at_utc']}`",
            f"- Conclusion: **{conclusion}**",
            f"- Stop: `{self.engine.stopped_reason}`",
            f"- Evaluated candidates: {self.engine.executed_candidates}",
            f"- Local proposal calls: {self.engine.model_calls} / {self.engine.budget.maximum_local_model_calls}",
            f"- Protected final evaluated: **no**", "",
            "## Search allocation", "",
            "The registered later-epoch portfolio assigns 50% continuation/forks, 20% "
            "cross-colony combinations, 15% independent alternatives and 15% adversarial "
            "challenges. Actual allocation decisions, if reached, are recorded in the "
            "search-coverage artifact. "
            "Admitted plans passed the typed V3 parser and structural novelty check. "
            "Evaluator, independent replay and critic completion are reported per "
            "candidate; missing stage evidence fails the corresponding gate.", "",
            "## Ranked development evidence", "",
            "| Rank | Candidate | Colony | ROI | Trades | Promotion |", "|---:|---|---|---:|---:|---|",
        ]
        for index, row in enumerate(report["ranked_candidates"], 1):
            roi = row["capital_weighted_return"]
            roi_text = "n/a" if roi is None else f"{float(roi):.3%}"
            lines.append(
                f"| {index} | `{row['candidate_id']}` | {row['colony']} | {roi_text} | "
                f"{row['trade_count']} | "
                f"{'pass' if row['promotion_passed'] is True else 'reject' if row['promotion_passed'] is False else 'incomplete'} |")
        lines.extend(["", "## Complete promotion evidence", "",
            "Every admitted candidate is shown below. Missing primary evaluation, replication, "
            "critic, or promotion evidence is labeled incomplete and cannot pass a gate. "
            "Authorization-denied diagnostic history remains bound in the continuation manifest "
            "and is excluded from this ranking. "
            "Detailed immutable predictions and ledgers exist only for stages that completed, "
            "under candidate artifact directories named by each research-plan SHA-256.", ""])

        def display(value: Any, *, percent: bool = False) -> str:
            if value is None:
                return "missing"
            if isinstance(value, bool):
                return "yes" if value else "no"
            if percent and isinstance(value, (int, float)):
                return f"{float(value):.3%}"
            return str(value).replace("|", "\\|")

        for index, row in enumerate(report["ranked_candidates"], 1):
            promotion = row["promotion"]
            stress = row["cost_stress"]
            bootstrap = row["bootstrap"]
            lines.extend([
                f"### {index}. `{row['candidate_id']}`", "",
                f"Plan `{row['research_plan_sha256']}`; colony `{row['colony']}`; "
                f"epoch {row['epoch']}.", "",
                "| Registered evidence | Value |", "|---|---:|",
                f"| Promotion | {'PASS' if row['promotion_passed'] is True else 'REJECT' if row['promotion_passed'] is False else 'INCOMPLETE'} |",
                f"| Primary capital-weighted return | {display(row['capital_weighted_return'], percent=True)} |",
                f"| Total simulated net profit | {display(row['total_net_profit'])} |",
                f"| Total entry outlay | {display(row['total_entry_outlay'])} |",
                f"| Trades | {display(row['trade_count'])} |",
                f"| Distinct settlement days | {display(promotion.get('distinct_settlement_days'))} |",
                f"| Minimum selected-trade estimated return | {display(promotion.get('minimum_selected_trade_expected_return'), percent=True)} |",
                f"| Mean trade return | {display(row['mean_trade_return'], percent=True)} |",
                f"| Bootstrap lower 95% bound | {display(bootstrap.get('lower_95'), percent=True)} |",
                f"| Bootstrap resamples / undefined | {display(bootstrap.get('resamples'))} / {display(bootstrap.get('undefined_resamples'))} |",
                f"| Return after removing best day | {display(row['capital_weighted_return_after_removing_most_profitable_day'], percent=True)} |",
                f"| Cost-stress return | {display(stress.get('capital_weighted_return'), percent=True)} |",
                f"| Profitable folds | {display(promotion.get('profitable_fold_count'))} / {display(promotion.get('required_fold_count'))} |",
                f"| Candidate / reference Brier | {display(row['brier'])} / {display(row['reference_brier'])} |",
                f"| Brier delta | {display(promotion.get('brier_delta'))} |",
                f"| Candidate / reference CRPS | {display(row['crps_f'])} / {display(row['reference_crps_f'])} |",
                f"| Relative CRPS degradation | {display(promotion.get('relative_crps_degradation'), percent=True)} |",
                f"| Probability conservation | {display(row['probability_conservation_passed'])} |",
                f"| Independent replication | {display(row['replication'].get('status'))} |",
                f"| Critic decision | {display(row['critic'].get('decision'))} |", "",
                "**Gate findings:** " + (
                    ", ".join(f"`{value}`" for value in row["rejection_reasons"])
                    if row["rejection_reasons"] else "none"), "",
                "#### Chronological folds", "",
                "| Fold | Trades | Net profit | Entry outlay | Capital-weighted return | Mean trade return |",
                "|---:|---:|---:|---:|---:|---:|",
            ])
            for fold in row["folds"]:
                lines.append(
                    f"| {display(fold.get('fold'))} | {display(fold.get('trade_count'))} | "
                    f"{display(fold.get('total_net_profit'))} | "
                    f"{display(fold.get('total_entry_outlay'))} | "
                    f"{display(fold.get('capital_weighted_return'), percent=True)} | "
                    f"{display(fold.get('mean_trade_return'), percent=True)} |")
            if not row["folds"]:
                lines.append("| missing | missing | missing | missing | missing | missing |")
            lines.extend(["", "#### Cost stress", "",
                "| Fee rate | Additional adverse price | Quantity | Unavailable entry | Trades | Return |",
                "|---:|---:|---:|---|---:|---:|",
                f"| {display(stress.get('fee_rate'), percent=True)} | "
                f"{display(stress.get('additional_adverse_price_per_contract_dollars'))} | "
                f"{display(stress.get('quantity'))} | "
                f"{display(stress.get('unavailable_entry_treatment'))} | "
                f"{display(stress.get('trade_count'))} | "
                f"{display(stress.get('capital_weighted_return'), percent=True)} |", ""])
            lines.extend(["#### Concentration tables", ""])
            breakdowns = row["contribution_breakdowns"]
            if not breakdowns:
                lines.extend(["Missing.", ""])
            for dimension, groups in sorted(breakdowns.items()):
                lines.extend([
                    f"##### {dimension.replace('_', ' ').title()}", "",
                    "| Segment | Trades | Net profit | Entry outlay | Return | Mean trade return |",
                    "|---|---:|---:|---:|---:|---:|",
                ])
                for segment, values in sorted((groups or {}).items()):
                    lines.append(
                        f"| {display(segment)} | {display(values.get('trade_count'))} | "
                        f"{display(values.get('total_net_profit'))} | "
                        f"{display(values.get('total_entry_outlay'))} | "
                        f"{display(values.get('capital_weighted_return'), percent=True)} | "
                        f"{display(values.get('mean_trade_return'), percent=True)} |")
                if not groups:
                    lines.append("| missing | missing | missing | missing | missing | missing |")
                lines.append("")
            replication = row["replication"]
            critic = row["critic"]
            lines.extend([
                "#### Independent review", "",
                f"Replication verifier: `{display(replication.get('verifier_id'))}`; "
                f"status **{display(replication.get('status'))}**; differences: "
                + (", ".join(f"`{display(value)}`" for value in replication.get("differences", []))
                   or "none") + ".", "",
                "Replication checks: " + (
                    ", ".join(f"`{name}`={display(value)}"
                              for name, value in sorted(replication.get("checks", {}).items()))
                    or "missing") + ".", "",
                f"Critic: `{display(critic.get('critic_id'))}`; decision "
                f"**{display(critic.get('decision'))}**; unresolved defects: "
                + (", ".join(f"`{display(value)}`"
                             for value in critic.get("unresolved_defects", []))
                   or "none") + ".", "",
                "Critic checks: " + (
                    ", ".join(f"`{name}`={display(value)}"
                              for name, value in sorted(critic.get("checks", {}).items()))
                    or "missing") + ".", "",
                "Artifact hashes:", "",
            ])
            for name, digest in sorted(row["artifact_sha256s"].items()):
                lines.append(f"- `{name}`: `{digest}`")
            if not row["artifact_sha256s"]:
                lines.append("- missing")
            lines.append("")
        lines.extend(["", "## Interpretation", "",
            "These are offline historical assumed-fill results. They are not account gains "
            "and do not authorize live or paper trading. The protected final interval remains "
            "unevaluated; a one-use authorization is emitted only for a candidate that passes "
            "every registered development, replication and critic gate.", ""])
        (self.output_root / "report.md").write_text("\n".join(lines), encoding="utf-8")
        self.phase = "COMPLETE"
        self.save_recovery()
        publish_v3_campaign_artifact_manifest(self.output_root)
        return {"path": str(self.output_root), **report}


def _production_evaluators(authorization, output: Path):
    readiness = json.loads(authorization.readiness_path.read_text(encoding="utf-8"))
    bundle_manifest = authorization.root / readiness["data_bundle"]["manifest_path"]
    bundle = json.loads(bundle_manifest.read_text(encoding="utf-8"))
    dataset_root = (authorization.root
                    / bundle["frozen_dataset_manifest"]["path"]).resolve().parent
    arguments = {
        "project_root": authorization.root,
        "dataset_root": dataset_root,
        "fee_scenario": primary_fee_scenario(),
        "data_bundle_manifest": bundle_manifest,
        "expected_bundle_version": authorization.data_bundle_version,
        "expected_bundle_sha256": authorization.data_bundle_sha256,
    }
    primary = OfflineCandidateEvaluatorV3.from_bound_directory(
        **arguments, artifact_root=output / "candidates" / "primary")
    replication = OfflineCandidateEvaluatorV3.from_bound_directory(
        **arguments, artifact_root=output / "candidates" / "replication")
    return primary, replication


def _bound_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise V3ReadinessRefusal(f"Invalid {label} path")
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise V3ReadinessRefusal(f"{label} path escapes the project") from exc
    if "protected_final" in {
            part.casefold().replace("-", "_") for part in path.parts}:
        raise V3ReadinessRefusal(f"{label} may not use protected-final storage")
    return path


def _production_worker_factory(authorization, output: Path):
    """Load the pinned local worker only through readiness-bound probe evidence."""
    from .local_backend import LocalTextWorker, load_runtime_spec

    component_path = authorization.root / "data/manifests/v3_worker_probe.json"
    component = json.loads(component_path.read_text(encoding="utf-8"))
    required = {
        "local_model_inference_executed": True,
        "actual_v3_protocol_probe_passed": True,
        "offline_verified": True,
        "network_used": False,
        "protected_final_read": False,
        "tool_catalog": [],
    }
    if any(component.get(key) != value for key, value in required.items()):
        raise V3ReadinessRefusal(
            "Real V3 campaign requires an actual readiness-bound local V3 worker probe")
    for name in ("runtime_sha256", "backend_code_sha256",
                 "runtime_spec_sha256", "v3_protocol_probe_sha256"):
        value = component.get(name)
        if (not isinstance(value, str) or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)):
            raise V3ReadinessRefusal(f"V3 worker probe lacks a valid {name}")
    runtime_spec_path = _bound_path(
        authorization.root, component.get("runtime_spec_path"), "runtime spec")
    protocol_probe_path = _bound_path(
        authorization.root, component.get("v3_protocol_probe_path"),
        "V3 protocol probe")
    if (not runtime_spec_path.is_file()
            or sha256_file(runtime_spec_path) != component["runtime_spec_sha256"]
            or not protocol_probe_path.is_file()
            or sha256_file(protocol_probe_path) != component["v3_protocol_probe_sha256"]):
        raise V3ReadinessRefusal("V3 local runtime or protocol probe changed after readiness")
    probe = json.loads(protocol_probe_path.read_text(encoding="utf-8"))
    if (probe.get("status") != "PASS"
            or probe.get("protocol") != PROTOCOL_V3
            or probe.get("actual_v3_protocol_probe_passed") is not True
            or probe.get("runtime_sha256") != component["runtime_sha256"]
            or probe.get("backend_code_sha256") != component["backend_code_sha256"]
            or probe.get("tool_catalog") != []):
        raise V3ReadinessRefusal("Readiness-bound V3 local protocol probe did not pass")
    backend = LocalTextWorker(load_runtime_spec(authorization.root, runtime_spec_path))
    if (backend.verification.get("runtime_sha256") != component["runtime_sha256"]
            or backend.backend_code_sha256 != component["backend_code_sha256"]):
        raise V3ReadinessRefusal("Pinned V3 local worker differs from readiness")
    local_artifacts = output / "local-inference"

    def factory(worker_id: str, _role: str) -> V3ProposalWorker:
        return PinnedLocalTextWorkerV3(
            worker_id, backend, authorization, component["runtime_sha256"],
            local_artifacts)

    return factory


def _campaign_bootstrap_path(authorization) -> Path:
    return authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".bootstrap.json")


def _campaign_bootstrap_transition_path(authorization, status: str) -> Path:
    suffixes = {
        "CLAIMED": ".bootstrap.claimed.json",
        "RECOVERY_CONSUMED": ".bootstrap.recovery-consumed.json",
        "COMMITTED": ".bootstrap.committed.json",
    }
    try:
        suffix = suffixes[status]
    except KeyError as exc:
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap transition is invalid") from exc
    return authorization.ticket_path.with_name(
        authorization.ticket_path.name + suffix)


def _campaign_bootstrap_identity(authorization, output: Path) -> dict[str, Any]:
    identity = {
        "bootstrap_version": CAMPAIGN_BOOTSTRAP_VERSION,
        "campaign_id": authorization.campaign_id,
        "ticket_sha256": authorization.ticket_sha256,
        "readiness_sha256": authorization.readiness_sha256,
        "output_path": output.relative_to(authorization.root).as_posix(),
        "synthetic": authorization.synthetic,
        "protected_final_evaluated": False,
        "actual_orders_placed": False,
    }
    if authorization.continuation_import_state_path is not None:
        path = output / CONTINUATION_IMPORT_FILENAME
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            identity["continuation_import"] = {
                "path": path.relative_to(authorization.root).as_posix(),
                "sha256": sha256_file(path),
                "manifest_sha256": manifest["manifest_sha256"],
                "source_campaign_id": manifest["source"]["campaign_id"],
                "source_state_sha256": manifest["continuation"][
                    "source_state_sha256"],
                "successor_import_state_sha256": manifest["continuation"][
                    "successor_import_state_sha256"],
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise V3ReadinessRefusal(
                "V3 continuation bootstrap identity is unavailable") from exc
    return identity


def _read_campaign_bootstrap(
        authorization, output: Path, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise V3ReadinessRefusal("V3 campaign bootstrap journal is unreadable") from exc
    body = {key: item for key, item in value.items() if key != "bootstrap_sha256"}
    if value.get("bootstrap_sha256") != canonical_hash(body):
        raise V3ReadinessRefusal("V3 campaign bootstrap journal was modified")
    identity = _campaign_bootstrap_identity(authorization, output)
    if any(value.get(key) != expected for key, expected in identity.items()):
        raise V3ReadinessRefusal("V3 campaign bootstrap identity differs")
    if value.get("status") != "PREPARED":
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap base must remain immutable")
    _aware_timestamp(value.get("created_at_utc"), "V3 campaign bootstrap timestamp")
    expected_keys = set(identity) | {
        "status", "created_at_utc", "bootstrap_sha256",
    }
    if set(value) != expected_keys:
        raise V3ReadinessRefusal("V3 campaign bootstrap fields differ")
    base_sha256 = sha256_file(path)
    transitions: dict[str, dict[str, Any]] = {}
    for transition_status in ("CLAIMED", "RECOVERY_CONSUMED", "COMMITTED"):
        transition_path = _campaign_bootstrap_transition_path(
            authorization, transition_status)
        if not transition_path.is_file():
            continue
        try:
            transition = json.loads(transition_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise V3ReadinessRefusal(
                "V3 campaign bootstrap transition is unreadable") from exc
        transition_body = {
            key: item for key, item in transition.items()
            if key != "transition_sha256"}
        if transition.get("transition_sha256") != canonical_hash(transition_body):
            raise V3ReadinessRefusal(
                "V3 campaign bootstrap transition was modified")
        expected_transition = {
            "transition_version": CAMPAIGN_BOOTSTRAP_VERSION,
            "campaign_id": authorization.campaign_id,
            "ticket_sha256": authorization.ticket_sha256,
            "readiness_sha256": authorization.readiness_sha256,
            "bootstrap_base_sha256": base_sha256,
            "previous_transition_sha256": (
                transitions["RECOVERY_CONSUMED"]["transition_sha256"]
                if transition_status == "COMMITTED"
                and "RECOVERY_CONSUMED" in transitions else
                transitions["CLAIMED"]["transition_sha256"]
                if transition_status in {"RECOVERY_CONSUMED", "COMMITTED"}
                and "CLAIMED" in transitions else base_sha256),
            "status": transition_status,
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        }
        if any(transition.get(key) != expected
               for key, expected in expected_transition.items()):
            raise V3ReadinessRefusal(
                "V3 campaign bootstrap transition identity differs")
        _aware_timestamp(
            transition.get("transitioned_at_utc"),
            "V3 campaign bootstrap transition timestamp")
        transition_keys = set(expected_transition) | {
            "transitioned_at_utc", "transition_sha256",
        }
        if transition_status == "COMMITTED":
            transition_keys.add("initial_recovery_state_sha256")
            digest = transition.get("initial_recovery_state_sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                raise V3ReadinessRefusal(
                    "V3 campaign bootstrap recovery-state digest is invalid")
        if set(transition) != transition_keys:
            raise V3ReadinessRefusal(
                "V3 campaign bootstrap transition fields differ")
        transitions[transition_status] = transition
    effective_status = (
        "COMMITTED" if "COMMITTED" in transitions else
        "RECOVERY_CONSUMED" if "RECOVERY_CONSUMED" in transitions else
        "CLAIMED" if "CLAIMED" in transitions else "PREPARED")
    return {
        **value,
        "status": effective_status,
        "bootstrap_base_sha256": base_sha256,
        "transitions": transitions,
    }


def _prepare_campaign_bootstrap(authorization, output: Path) -> dict[str, Any]:
    path = _campaign_bootstrap_path(authorization)
    body = {
        **_campaign_bootstrap_identity(authorization, output),
        "status": "PREPARED",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    value = {**body, "bootstrap_sha256": canonical_hash(body)}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap journal already exists") from exc
    return _read_campaign_bootstrap(authorization, output, path)


def _transition_campaign_bootstrap(
        authorization, output: Path, value: dict[str, Any], status: str,
        *, recovery_state_path: Path | None = None) -> dict[str, Any]:
    if status not in {"CLAIMED", "RECOVERY_CONSUMED", "COMMITTED"}:
        raise V3ReadinessRefusal("V3 campaign bootstrap transition is invalid")
    current = _read_campaign_bootstrap(
        authorization, output, _campaign_bootstrap_path(authorization))
    if (current["bootstrap_base_sha256"] != value["bootstrap_base_sha256"]
            or current["status"] != value["status"]):
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap changed during its transition")
    allowed = {
        "PREPARED": {"CLAIMED", "RECOVERY_CONSUMED", "COMMITTED"},
        "CLAIMED": {"RECOVERY_CONSUMED", "COMMITTED"},
        "RECOVERY_CONSUMED": {"COMMITTED"},
        "COMMITTED": set(),
    }
    if status not in allowed[current["status"]]:
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap transition would reuse consumed state")
    body = {
        "transition_version": CAMPAIGN_BOOTSTRAP_VERSION,
        "campaign_id": authorization.campaign_id,
        "ticket_sha256": authorization.ticket_sha256,
        "readiness_sha256": authorization.readiness_sha256,
        "bootstrap_base_sha256": current["bootstrap_base_sha256"],
        "previous_transition_sha256": (
            current["transitions"]["RECOVERY_CONSUMED"]["transition_sha256"]
            if status == "COMMITTED"
            and "RECOVERY_CONSUMED" in current["transitions"] else
            current["transitions"]["CLAIMED"]["transition_sha256"]
            if status in {"RECOVERY_CONSUMED", "COMMITTED"}
            and "CLAIMED" in current["transitions"] else
            current["bootstrap_base_sha256"]),
        "status": status,
        "transitioned_at_utc": datetime.now(timezone.utc).isoformat(),
        "protected_final_evaluated": False,
        "actual_orders_placed": False,
    }
    if status == "COMMITTED":
        if recovery_state_path is None or not recovery_state_path.is_file():
            raise V3ReadinessRefusal(
                "V3 campaign bootstrap cannot commit without recovery state")
        body.update({
            "initial_recovery_state_sha256": sha256_file(recovery_state_path),
        })
    transition = {**body, "transition_sha256": canonical_hash(body)}
    transition_path = _campaign_bootstrap_transition_path(authorization, status)
    try:
        with transition_path.open("x", encoding="utf-8") as stream:
            json.dump(
                transition, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise V3ReadinessRefusal(
            "V3 campaign bootstrap transition was already consumed") from exc
    return _read_campaign_bootstrap(
        authorization, output, _campaign_bootstrap_path(authorization))


def _continuation_import_for_start(
    authorization, output: Path, *, allow_existing_claim: bool,
) -> dict[str, Any] | None:
    """Create or re-verify the immutable continuation before state import."""
    if authorization.continuation_import_state_path is None:
        return None
    if authorization.source_campaign_id is None:
        raise V3ReadinessRefusal("V3 continuation source campaign is missing")
    destination = output / CONTINUATION_IMPORT_FILENAME
    source = Path("runs/campaigns_v3") / authorization.source_campaign_id
    claim = authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".claimed.json")
    try:
        if allow_existing_claim and not claim.is_file():
            raise V3ContinuationError(
                "Continuation recovery requires the bound one-use claim")
        if destination.is_file():
            manifest = json.loads(destination.read_text(encoding="utf-8"))
        else:
            if allow_existing_claim:
                raise V3ContinuationError(
                    "Claimed successor lacks its continuation import")
            manifest = build_continuation_import_manifest(
                root=authorization.root,
                source_campaign=source,
                successor_campaign_id=authorization.campaign_id,
                successor_readiness_path=authorization.readiness_path,
                successor_ticket_path=authorization.ticket_path,
            )
            _atomic_publish_json(destination, manifest)
        verify_continuation_import_manifest(
            root=authorization.root, manifest=manifest,
            allow_existing_claim=allow_existing_claim)
        layer_a_path = authorization.continuation_import_state_path
        assert layer_a_path is not None
        if (not layer_a_path.is_file()
                or sha256_file(layer_a_path)
                != authorization.continuation_import_state_sha256):
            raise V3ContinuationError(
                "Amendment-bound continuation state changed")
        layer_a = json.loads(layer_a_path.read_text(encoding="utf-8"))
        preserved = manifest["continuation"]["preserved_source_state"]
        runtime_rows = (
            manifest["continuation"]["epoch_two_rebuild"][
                "eligible_imported_candidates"]
            + manifest["continuation"]["epoch_two_rebuild"][
                "ineligible_historical_candidates"])
        counters = layer_a.get("counters") or {}
        engine = preserved["engine"]
        counter_keys = {
            "current_epoch", "admitted_candidates", "executed_candidates",
            "model_calls", "model_context_tokens_reserved", "elapsed_seconds",
            "epoch_admissions",
        }
        if (layer_a.get("source_campaign_id")
                != manifest["source"]["campaign_id"]
                or layer_a.get("source_campaign_artifacts_sha256")
                != manifest["source"]["campaign_artifact_manifest"]["sha256"]
                or layer_a.get("source_recovery_sha256")
                != manifest["source"]["recovery"]["sha256"]
                or sorted(row.get("plan_sha256") for row in layer_a.get(
                    "candidates", []))
                != sorted(row["plan_sha256"] for row in runtime_rows)
                or any(counters.get(key) != engine.get(key)
                       for key in counter_keys)
                or layer_a.get("epoch_2_queue_sha256")
                != canonical_hash(preserved["queue"])
                or layer_a.get("epoch_2_allocation")
                != preserved["coverage"].get("allocation_decisions")):
            raise V3ContinuationError(
                "Executable continuation differs from its amendment-bound state")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise V3ReadinessRefusal(
            "V3 continuation import is unavailable or invalid") from exc


def _write_initial_continuation_recovery(
    authorization, output: Path, manifest: dict[str, Any],
) -> Path:
    """Durably import only the state authorized by the continuation manifest."""
    try:
        imported = manifest["continuation"]["successor_import_state"]
        required = {
            "campaign_id", "readiness_sha256", "ticket_sha256", "phase",
            "started_at_utc", "completed_at_utc", "engine", "coverage",
            "queue", "next_queue_index",
        }
        if (not isinstance(imported, dict) or set(imported) != required
                or imported["campaign_id"] != authorization.campaign_id
                or imported["readiness_sha256"] != authorization.readiness_sha256
                or imported["ticket_sha256"] != authorization.ticket_sha256
                or imported["phase"] != "REBUILD_EPOCH_ALLOCATION"
                or imported["completed_at_utc"] is not None
                or imported["queue"] != []
                or imported["next_queue_index"] != 0):
            raise V3ContinuationError(
                "Authorized successor import state differs")
        body = {
            "recovery_version": RECOVERY_VERSION,
            "orchestrator_version": ORCHESTRATOR_VERSION,
            **imported,
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        }
        value = {**body, "state_sha256": canonical_hash(body)}
        state = output / "recovery-state.json"
        _atomic_publish_json(state, value)
        return state
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise V3ReadinessRefusal(
            "V3 continuation state could not be imported") from exc


def _verify_continuation_recovery_floor(
    orchestrator: BoundedV3Orchestrator, manifest: dict[str, Any],
) -> None:
    """Prevent a successor recovery from refunding or rewriting imported work."""
    try:
        imported = manifest["continuation"]["successor_import_state"]
        floor = imported["engine"]
        current = orchestrator._engine_state()
        phases = {
            "REBUILD_EPOCH_ALLOCATION", "EXECUTING_EPOCH",
            "BETWEEN_EPOCHS", "COMPLETE",
        }
        if (orchestrator.phase not in phases
                or current["current_epoch"] < floor["current_epoch"]
                or current["model_calls"] < floor["model_calls"]
                or current["model_context_tokens_reserved"]
                < floor["model_context_tokens_reserved"]
                or current["admitted_candidates"] < floor["admitted_candidates"]
                or current["executed_candidates"] < floor["executed_candidates"]
                or current["elapsed_seconds"] < floor["elapsed_seconds"]
                or current["epoch_admissions"].get("1")
                != floor["epoch_admissions"].get("1")):
            raise V3ContinuationError(
                "Continuation recovery refunded or rewrote imported counters")
        for candidate_id, expected in floor["candidates"].items():
            if current["candidates"].get(candidate_id) != expected:
                raise V3ContinuationError(
                    "Continuation recovery changed imported candidate evidence")
        for fingerprint, candidate_id in floor["novelty_index"].items():
            if current["novelty_index"].get(fingerprint) != candidate_id:
                raise V3ContinuationError(
                    "Continuation recovery changed imported novelty evidence")
        if (current["empty_epochs"] < floor["empty_epochs"]
                or current["duplicate_proposals"][:len(
                    floor["duplicate_proposals"])]
                != floor["duplicate_proposals"]
                or current["nonproposal_responses"][:len(
                    floor["nonproposal_responses"])]
                != floor["nonproposal_responses"]
                or any(current["transient_retries"].get(key) != value
                       for key, value in floor["transient_retries"].items())):
            raise V3ContinuationError(
                "Continuation recovery changed imported engine history")
        source_coverage = imported["coverage"]
        coverage = orchestrator.coverage
        if (coverage.get("allocation_weights")
                != source_coverage.get("allocation_weights")
                or coverage.get("epochs", {}).get("1")
                != source_coverage.get("epochs", {}).get("1")
                or coverage.get("duplicate_proposals", 0)
                < source_coverage.get("duplicate_proposals", 0)
                or coverage.get("nonproposal_responses", 0)
                < source_coverage.get("nonproposal_responses", 0)
                or coverage.get("integrity_failures", [])[:len(
                    source_coverage.get("integrity_failures", []))]
                != source_coverage.get("integrity_failures", [])):
            raise V3ContinuationError(
                "Continuation recovery changed imported coverage evidence")
        for colony, expected in source_coverage.get("colonies", {}).items():
            observed = coverage.get("colonies", {}).get(colony)
            if (not isinstance(observed, dict)
                    or any(observed.get(key, -1) < value
                           for key, value in expected.items())):
                raise V3ContinuationError(
                    "Continuation recovery refunded imported colony coverage")
        source_decisions = imported["coverage"].get("allocation_decisions", [])
        current_decisions = orchestrator.coverage.get("allocation_decisions", [])
        if (current_decisions[:len(source_decisions)] != source_decisions
                or orchestrator.started_at_utc != imported["started_at_utc"]
                or (orchestrator.phase != "COMPLETE"
                    and orchestrator.completed_at_utc is not None)):
            raise V3ContinuationError(
                "Continuation recovery changed source chronology or allocation")
        if orchestrator.phase == "REBUILD_EPOCH_ALLOCATION":
            if (orchestrator.queue != [] or orchestrator.next_queue_index != 0
                    or current["current_epoch"] != 2
                    or orchestrator.coverage != imported["coverage"]
                    or current["model_calls"] != floor["model_calls"]
                    or current["model_context_tokens_reserved"]
                    != floor["model_context_tokens_reserved"]
                    or current["admitted_candidates"]
                    != floor["admitted_candidates"]
                    or current["executed_candidates"]
                    != floor["executed_candidates"]
                    or current["novelty_index"] != floor["novelty_index"]):
                raise V3ContinuationError(
                    "Continuation rebuild recovery point differs")
            return
        journal_path = orchestrator.output_root / "epoch-02-rebuild.json"
        if not journal_path.is_file():
            rebuild_failures = [
                row for row in orchestrator.coverage.get(
                    "integrity_failures", [])
                if isinstance(row, dict)
                and row.get("stage") == "continuation_rebuild"]
            if (orchestrator.phase == "COMPLETE" and rebuild_failures
                    and current.get("champion_candidate_id") is None
                    and current.get("final_authorization") is None):
                return
            raise V3ContinuationError(
                "Post-rebuild continuation recovery lacks its journal")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal_body = {
            key: value for key, value in journal.items()
            if key != "journal_sha256"}
        decision = journal.get("allocation_decision")
        rebuilt_queue = journal.get("queue")
        verification = verify_epoch_two_rebuild(
            manifest=manifest, allocation_decision=decision,
            queue=rebuilt_queue)
        continuation_record = orchestrator.coverage.get("continuation_import")
        if (journal.get("journal_sha256") != canonical_hash(journal_body)
                or journal.get("continuation_manifest_sha256")
                != manifest["manifest_sha256"]
                or journal.get("verification") != verification
                or current_decisions[len(source_decisions):].count(decision) != 1
                or current_decisions[len(source_decisions)] != decision
                or orchestrator.coverage.get(
                    "superseded_allocation_decisions") != source_decisions
                or not isinstance(continuation_record, dict)
                or continuation_record.get("manifest_sha256")
                != manifest["manifest_sha256"]
                or continuation_record.get("rebuild_journal_sha256")
                != journal["journal_sha256"]
                or continuation_record.get("epoch_two_rebuild") != verification):
            raise V3ContinuationError(
                "Continuation rebuild journal or coverage binding differs")
        if (current["current_epoch"] == 2
                and orchestrator.phase == "EXECUTING_EPOCH"):
            if (not 0 <= orchestrator.next_queue_index <= len(
                        orchestrator.queue)
                    or len(rebuilt_queue) != len(orchestrator.queue)):
                raise V3ContinuationError(
                    "Continuation rebuild journal or queue differs")
            stable = ("task_id", "category", "role", "plan", "options")
            for expected, observed in zip(journal["queue"], orchestrator.queue):
                if any(expected.get(key) != observed.get(key) for key in stable):
                    raise V3ContinuationError(
                        "Continuation epoch-two queue identity changed")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise V3ReadinessRefusal(
            "V3 continuation recovery violates its import floor") from exc


def run_v3_campaign(
    root: Path | str, readiness_path: Path | str, ticket_path: Path | str,
    *, resume: bool = False,
    worker_factory: Callable[[str, str], V3ProposalWorker] | None = None,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    """Load a substantive ticket and run or explicitly resume one campaign."""
    try:
        authorization = load_v3_campaign_authorization(
            root, readiness_path, ticket_path, allow_synthetic=allow_synthetic)
    except V3ReadinessRefusal:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise V3ReadinessRefusal(
            "V3 substantive readiness or campaign ticket is unavailable") from exc
    output = authorization.assert_development_path(
        Path("runs/campaigns_v3") / authorization.campaign_id)
    with _CampaignExecutionLease(output):
        return _run_v3_campaign_authorized(
            authorization, output, resume=resume,
            worker_factory=worker_factory)


def _run_v3_campaign_authorized(
    authorization, output: Path, *, resume: bool,
    worker_factory: Callable[[str, str], V3ProposalWorker] | None,
) -> dict[str, Any]:
    state = output / "recovery-state.json"
    claim = authorization.ticket_path.with_name(
        authorization.ticket_path.name + ".claimed.json")
    bootstrap_path = _campaign_bootstrap_path(authorization)
    bootstrap = (_read_campaign_bootstrap(
        authorization, output, bootstrap_path) if bootstrap_path.is_file() else None)
    if resume:
        if not state.is_file():
            raise V3ReadinessRefusal("No V3 recovery state exists for explicit resume")
        if bootstrap is None:
            raise V3ReadinessRefusal(
                "V3 recovery state lacks its durable bootstrap journal")
    else:
        if state.exists():
            raise V3ReadinessRefusal("Existing V3 campaign state requires explicit resume")
        # A crash can occur after the one-use claim is durably written but before
        # the initial recovery state is.  Only an absent or empty campaign
        # directory is a valid claim-only bootstrap.  Any other partial output is
        # refused because it cannot be proved to precede candidate evaluation.
        if output.exists():
            existing = list(output.iterdir())
            allowed = {CAMPAIGN_EXECUTION_LOCK}
            if authorization.continuation_import_state_path is not None:
                allowed.update({
                    CONTINUATION_IMPORT_FILENAME,
                    CONTINUATION_IMPORT_FILENAME + ".pending",
                    "recovery-state.json.pending",
                })
            safe_prestate = all(
                item.is_file() and item.name in allowed for item in existing)
            if existing and not safe_prestate:
                raise V3ReadinessRefusal(
                    "Existing V3 campaign artifacts require a recovery state")
        if bootstrap is not None and bootstrap["status"] in {
                "RECOVERY_CONSUMED", "COMMITTED"}:
            raise V3ReadinessRefusal(
                "One-use V3 campaign bootstrap has already been consumed")
        if claim.is_file() and bootstrap is None:
            raise V3ReadinessRefusal(
                "Claimed V3 campaign lacks its durable bootstrap journal")

    # Construct and verify every fallible production dependency before consuming
    # the one-use ticket.  This keeps model/evaluator setup failures retryable.
    primary, replication = _production_evaluators(authorization, output)
    if worker_factory is None and not authorization.synthetic:
        worker_factory = _production_worker_factory(authorization, output)
    continuation_manifest = _continuation_import_for_start(
        authorization, output,
        allow_existing_claim=(resume or claim.is_file()))
    if resume:
        engine = V3CampaignEngine(authorization, claim_ticket=False)
        orchestrator = BoundedV3Orchestrator.recover(
            engine=engine, primary_evaluator=primary,
            replication_evaluator=replication, output_root=output,
            worker_factory=worker_factory)
        if continuation_manifest is not None:
            _verify_continuation_recovery_floor(
                orchestrator, continuation_manifest)
        if bootstrap["status"] != "COMMITTED":
            bootstrap = _transition_campaign_bootstrap(
                authorization, output, bootstrap, "COMMITTED",
                recovery_state_path=state)
        if orchestrator.phase == "COMPLETE":
            summary = output / "summary.json"
            if not summary.is_file():
                return orchestrator._finalize()
            verify_v3_campaign_artifacts(output)
            return {"path": str(output), **json.loads(summary.read_text(encoding="utf-8"))}
    else:
        if bootstrap is None:
            bootstrap = _prepare_campaign_bootstrap(authorization, output)
        recovering_claim_only = claim.is_file()
        if recovering_claim_only:
            bootstrap = _transition_campaign_bootstrap(
                authorization, output, bootstrap, "RECOVERY_CONSUMED")
        # Create the one-use claim only after every fallible production dependency
        # and the durable bootstrap journal are ready.
        engine = V3CampaignEngine(
            authorization, claim_ticket=not recovering_claim_only)
        if not recovering_claim_only:
            bootstrap = _transition_campaign_bootstrap(
                authorization, output, bootstrap, "CLAIMED")
        if continuation_manifest is None:
            orchestrator = BoundedV3Orchestrator(
                engine, primary, replication, output,
                worker_factory=worker_factory)
            orchestrator.save_recovery()
        else:
            _write_initial_continuation_recovery(
                authorization, output, continuation_manifest)
            orchestrator = BoundedV3Orchestrator.recover(
                engine=engine, primary_evaluator=primary,
                replication_evaluator=replication, output_root=output,
                worker_factory=worker_factory)
            _verify_continuation_recovery_floor(
                orchestrator, continuation_manifest)
        if bootstrap["status"] == "CLAIMED":
            bootstrap = _transition_campaign_bootstrap(
                authorization, output, bootstrap, "RECOVERY_CONSUMED")
        bootstrap = _transition_campaign_bootstrap(
            authorization, output, bootstrap, "COMMITTED",
            recovery_state_path=state)
    return orchestrator.run()


def campaign_status_v3(root: Path | str, ticket_path: Path | str) -> dict[str, Any]:
    root = Path(root).resolve()
    ticket = Path(ticket_path)
    ticket = ticket if ticket.is_absolute() else root / ticket
    if not ticket.is_file():
        return {"status": "NOT_READY_OR_NOT_TICKETED", "architecture_version": 3}
    value = json.loads(ticket.read_text(encoding="utf-8"))
    campaign_id = value.get("campaign_id")
    if not isinstance(campaign_id, str):
        raise V3ReadinessRefusal("Malformed V3 campaign ticket")
    output = (root / "runs/campaigns_v3" / campaign_id).resolve()
    output.relative_to(root)
    summary = output / "summary.json"
    state = output / "recovery-state.json"
    if summary.is_file():
        verify_v3_campaign_artifacts(output)
        return {"path": str(output), **json.loads(summary.read_text(encoding="utf-8"))}
    if state.is_file():
        recovery = json.loads(state.read_text(encoding="utf-8"))
        return {
            "status": "INCOMPLETE_EXPLICIT_RESUME_REQUIRED",
            "architecture_version": 3,
            "campaign_id": campaign_id,
            "phase": recovery.get("phase"),
            "state_sha256": recovery.get("state_sha256"),
            "path": str(output),
        }
    return {
        "status": "TICKET_READY_NOT_STARTED", "architecture_version": 3,
        "campaign_id": campaign_id, "path": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded offline KLAX V3 multi-worker research campaign")
    parser.add_argument("action", choices=("start", "resume", "status"), nargs="?", default="start")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--readiness", type=Path,
                        default=Path("data/manifests/v3_readiness.json"))
    parser.add_argument("--ticket", type=Path,
                        default=Path("runs/v3_offline_campaign_ticket.json"))
    args = parser.parse_args(argv)
    if args.action == "status":
        result = campaign_status_v3(args.root, args.ticket)
    else:
        result = run_v3_campaign(
            args.root, args.readiness, args.ticket,
            resume=args.action == "resume")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
