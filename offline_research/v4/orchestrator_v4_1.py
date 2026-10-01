"""V4.1 production continuation runner for the V4 nomination incident.

V4.1 starts under a new readiness document, one-use ticket, campaign identity,
and output directory.  The failed V4 source remains immutable.  The runner
imports and revalidates its 82 completed candidates, reconciles call 86 from
the saved raw process artifact, applies one zero-call fallback to the exact
preallocated task, and continues the unchanged 3,072-plan universe under the
original absolute deadline and cumulative budgets.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from klax_lab.campaign_v3 import (
    CandidateRecord, V3BudgetStop, V3CampaignError, V3IntegrityStop,
    V3ReadinessRefusal,
)
from klax_lab.orchestrator_v3 import _CampaignExecutionLease, _read_artifacts
from klax_lab.provenance import canonical_hash, sha256_file, write_json

from .continuation_v4_1 import (
    ABSOLUTE_DEADLINE_AT_UTC, COMPLETED_EPOCHS, CONSUMED_CANDIDATES,
    CONSUMED_CONTEXT_TOKENS, CONSUMED_MODEL_CALLS, CONSUMED_WALL_SECONDS,
    CONTINUATION_RECORD_VERSION, CURRENT_EPOCH, FAILED_PLAN_SHA256,
    FAILED_TASK_ID, SOURCE_CAMPAIGN_ID, SOURCE_RECOVERY_PATH, SOURCE_ROOT,
    SOURCE_STARTED_AT_UTC, V4_1ContinuationError,
    build_continuation_import_record_v4_1, reconcile_failed_nomination_v4_1,
)
from .evaluator_v4 import OfflineCandidateEvaluatorV4
from .local_worker_v4 import V4WorkerProtocolError, validate_v4_worker_packet
from .local_worker_v4_1 import (
    PinnedLocalTextWorkerV4_1, V4_1CandidateResponseProtocolError,
    parse_v4_1_worker_response,
)
from .orchestrator_v4 import (
    DATA_BUNDLE_PATH, ProductionV4Orchestrator, V4CampaignEngine,
    _now, _render_report_markdown_v4,
    _production_evaluators_v4, _production_worker_factory_v4,
)
from .research_plan_v4 import ResearchPlanV4
from .verifier_v4 import verify_scheduler_state_v4


RUNNER_VERSION_V4_1 = "klax-v4.1-production-continuation-orchestrator-v1"
RECOVERY_VERSION_V4_1 = "klax-v4.1-campaign-recovery-v1"
OUTPUT_PARENT_V4_1 = Path("runs/campaigns_v4_1")
IMPORT_MANIFEST_PATH = Path("data/manifests/v4_1_continuation_import.json")
CONTEXT_TOKENS_PER_MODEL_CALL = 16_384
TERMINAL_ARTIFACT_NAMES = (
    "candidate-register.json",
    "swarm-coverage.json",
    "scheduler-state.json",
    "search-coverage.json",
    "scheduler-verification.json",
    "report.md",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V3IntegrityStop("V4.1 timestamp is not timezone-aware")
    return value.isoformat()


def _aware(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise V3IntegrityStop(f"Invalid V4.1 {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V3IntegrityStop(f"V4.1 {label} lacks a timezone")
    return parsed


def _atomic_publish_file(source: Path, destination: Path) -> None:
    """Copy one staged artifact and expose it with a same-directory replace."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(destination.name + ".v4-1-pending")
    shutil.copyfile(source, pending)
    pending.replace(destination)


def _valid_terminal_commit_v4_1(output: Path) -> bool:
    """Return true only for a complete, hash-bound V4.1 terminal publication."""
    try:
        summary_path = output / "summary.json"
        verification_path = output / "scheduler-verification.json"
        manifest_path = output / "campaign-artifacts.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_body = {
            key: value for key, value in manifest.items()
            if key != "manifest_sha256"
        }
        if (summary.get("report_version")
                != "klax-v4.1-offline-campaign-report-v1"
                or summary.get("source_campaign_id") != SOURCE_CAMPAIGN_ID
                or verification.get("verifier_version")
                != "klax-v4.1-independent-scheduler-verifier-v1"
                or verification.get("status") not in {"PASS", "FAIL"}
                or manifest.get("manifest_version")
                != "klax-v4.1-campaign-artifacts-v1"
                or manifest.get("campaign_id") != summary.get("campaign_id")
                or manifest.get("source_campaign_id") != SOURCE_CAMPAIGN_ID
                or manifest.get("scheduler_verification_status")
                != verification.get("status")
                or manifest.get("scientific_conclusion")
                != summary.get("scientific_conclusion")
                or summary.get("scheduler_verification_v4_1")
                != verification.get("status")
                or manifest.get("manifest_sha256")
                != canonical_hash(manifest_body)):
            return False
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            return False
        by_path = {
            row.get("path"): row for row in artifacts
            if isinstance(row, Mapping) and isinstance(row.get("path"), str)
        }
        if len(by_path) != len(artifacts):
            return False
        required = {
            "summary.json", "scheduler-verification.json",
            "scheduler-state.json", "report.md", "recovery-state.json",
        }
        if not required.issubset(by_path):
            return False
        for relative, row in by_path.items():
            path = (output / relative).resolve()
            if (output.resolve() not in path.parents or not path.is_file()
                    or row.get("bytes") != path.stat().st_size
                    or row.get("sha256") != sha256_file(path)):
                return False
        return True
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _load_import_manifest(root: Path) -> dict[str, Any]:
    path = (root / IMPORT_MANIFEST_PATH).resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V3ReadinessRefusal("V4.1 continuation import manifest is unavailable") from exc
    if not isinstance(value, dict):
        raise V3ReadinessRefusal("V4.1 continuation import manifest is malformed")
    manifest_version = value.pop("manifest_version", None)
    claimed = value.pop("import_sha256", None)
    expected = build_continuation_import_record_v4_1(root)
    if (manifest_version != "klax-v4-1-continuation-import-manifest-v1"
            or claimed != canonical_hash(expected) or value != expected):
        raise V3ReadinessRefusal("V4.1 continuation import manifest differs")
    return expected


class V4_1CampaignEngine(V4CampaignEngine):
    """V4 engine with a non-resettable UTC deadline."""

    def __init__(
        self, authorization: Any, *, absolute_deadline_at_utc: str,
        utc_clock: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = time.monotonic,
        claim_ticket: bool = True,
    ) -> None:
        self.absolute_deadline_at_utc = absolute_deadline_at_utc
        self.utc_clock = utc_clock
        self._absolute_deadline = _aware(
            absolute_deadline_at_utc, "absolute deadline")
        super().__init__(authorization, clock=clock, claim_ticket=claim_ticket)

    def cumulative_wall_seconds(self) -> int:
        original = _aware(SOURCE_STARTED_AT_UTC, "source start")
        elapsed = int((self.utc_clock() - original).total_seconds())
        return max(CONSUMED_WALL_SECONDS, elapsed)

    def _check_operable(self) -> None:
        if self.utc_clock() >= self._absolute_deadline:
            self._stop("wall_time_budget_exhausted")
            raise V3BudgetStop("V4.1 original absolute deadline exhausted")
        super()._check_operable()

    def budget_remaining(self) -> dict[str, int]:
        remaining = super().budget_remaining()
        utc_remaining = max(
            0, int((self._absolute_deadline - self.utc_clock()).total_seconds()))
        remaining["wall_seconds_remaining"] = min(
            remaining["wall_seconds_remaining"], utc_remaining)
        return remaining


def _reconciled_source_state(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    record = build_continuation_import_record_v4_1(root)
    source_path = (root / SOURCE_RECOVERY_PATH).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    journal = deepcopy(source["coverage"]["call_journal"])
    last = journal[-1]
    if (last.get("model_call_number") != 86 or last.get("status") != "RESERVED"):
        raise V3IntegrityStop("V4.1 source call 86 cannot be reconciled")
    last.update({
        "status": "FAILED",
        "completed_at_utc": "2026-09-26T21:51:14.126716+00:00",
        "failure_class": "MODEL_NOMINATION_FAILED",
        "failure_reason": "schema_invalid_singleton_nonproposal",
    })
    if canonical_hash(journal) != record["scheduler_lineage"][
            "reconciled_call_journal_sha256"]:
        raise V3IntegrityStop("V4.1 reconciled call journal differs")
    source["coverage"]["call_journal"] = journal
    return source, record


def _hydrate_engine_from_source(
    engine: V4_1CampaignEngine, source: Mapping[str, Any],
) -> None:
    state = source.get("engine")
    if not isinstance(state, Mapping):
        raise V3IntegrityStop("V4.1 source engine state is missing")
    elapsed = engine.cumulative_wall_seconds()
    engine.started_at = engine.clock() - elapsed
    for name, expected in {
        "current_epoch": CURRENT_EPOCH,
        "model_calls": CONSUMED_MODEL_CALLS,
        "model_context_tokens_reserved": CONSUMED_CONTEXT_TOKENS,
        "admitted_candidates": CONSUMED_CANDIDATES,
        "executed_candidates": CONSUMED_CANDIDATES,
    }.items():
        if state.get(name) != expected:
            raise V3IntegrityStop(f"V4.1 source counter differs: {name}")
        setattr(engine, name, expected)
    engine.empty_epochs = int(state.get("empty_epochs", 0))
    engine.stopped_reason = None
    engine.champion_candidate_id = state.get("champion_candidate_id")
    engine.final_authorization = None
    engine.novelty_index = dict(state.get("novelty_index", {}))
    engine.duplicate_proposals = list(state.get("duplicate_proposals", []))
    engine.nonproposal_responses = list(state.get("nonproposal_responses", []))
    engine.epoch_admissions = {
        int(key): int(value) for key, value in state.get("epoch_admissions", {}).items()}
    engine.transient_retries = dict(state.get("transient_retries", {}))
    engine.candidates = {}
    for candidate_id, raw in state.get("candidates", {}).items():
        plan = ResearchPlanV4.from_dict(raw["plan"])
        if candidate_id != "v4-candidate-" + plan.identity[:20]:
            raise V3IntegrityStop("V4.1 imported candidate identity differs")
        engine.candidates[candidate_id] = CandidateRecord(
            candidate_id=candidate_id,
            plan=plan,
            discovery_worker_id=raw["discovery_worker_id"],
            epoch=raw["epoch"],
            artifact_sha256s=raw.get("artifact_sha256s"),
            evaluation=raw.get("evaluation"),
            promotion=raw.get("promotion"),
            replication=raw.get("replication"),
            critic=raw.get("critic"),
        )
    expected_novelty = {
        row.plan.novelty_fingerprint: row.candidate_id
        for row in engine.candidates.values()}
    if (engine.novelty_index != expected_novelty
            or len(engine.candidates) != CONSUMED_CANDIDATES
            or engine.epoch_admissions.get(CURRENT_EPOCH)
            != source["next_queue_index"]):
        raise V3IntegrityStop("V4.1 imported engine lineage differs")


def _v4_1_worker_factory(authorization: Any, output: Path):
    carrier_factory = _production_worker_factory_v4(authorization, output)
    carrier = carrier_factory("v4-1-runtime-carrier", "explorer")
    local_artifacts = output / "local-inference-v4-1"

    def factory(worker_id: str, _role: str) -> PinnedLocalTextWorkerV4_1:
        return PinnedLocalTextWorkerV4_1(
            worker_id=worker_id,
            backend=carrier.backend,
            authorization=authorization,
            expected_runtime_sha256=carrier.expected_runtime_sha256,
            artifact_root=local_artifacts,
        )
    return factory


def _verify_future_nomination_failures_v4_1(
    root: Path, state: Mapping[str, Any],
) -> bool:
    records = state.get("model_nomination_failures_v4_1")
    journal = state.get("call_journal")
    history = state.get("allocation_history")
    campaign_id = state.get("campaign_id")
    if (not isinstance(records, list) or not isinstance(journal, list)
            or not isinstance(history, list) or not isinstance(campaign_id, str)
            or not campaign_id.startswith("v4-1-offline-")):
        return False
    allocations: dict[str, tuple[Mapping[str, Any], str]] = {}
    for epoch in history:
        if not isinstance(epoch, Mapping):
            return False
        digest = epoch.get("digest")
        digest_sha256 = (
            digest.get("digest_sha256") if isinstance(digest, Mapping) else None)
        for allocation in epoch.get("allocations", []):
            if not isinstance(allocation, Mapping):
                return False
            task_id = allocation.get("task_id")
            if not isinstance(task_id, str) or task_id in allocations:
                return False
            allocations[task_id] = (allocation, digest_sha256)
    journal_by_task: dict[str, list[Mapping[str, Any]]] = {}
    tasks_with_protocol_errors: set[str] = set()
    for row in journal:
        if not isinstance(row, Mapping):
            return False
        task_id = row.get("task_id")
        if isinstance(task_id, str) and row.get("kind") == "candidate_nomination":
            journal_by_task.setdefault(task_id, []).append(row)
            if row.get("failure_class") == "MODEL_NOMINATION_PROTOCOL_ERROR":
                tasks_with_protocol_errors.add(task_id)
    record_tasks: set[str] = set()
    output = (root / OUTPUT_PARENT_V4_1 / campaign_id).resolve()
    if output.parent != (root / OUTPUT_PARENT_V4_1).resolve():
        return False
    for raw in records:
        if not isinstance(raw, Mapping):
            return False
        value = dict(raw)
        claimed = value.pop("record_sha256", None)
        task_id = value.get("task_id")
        attempts = value.get("attempts")
        allocation_entry = allocations.get(task_id)
        task_rows = journal_by_task.get(task_id, [])
        if (claimed != canonical_hash(value)
                or task_id in record_tasks
                or value.get("failure_version")
                != "klax-v4.1-model-nomination-failure-v1"
                or value.get("status") != "MODEL_NOMINATION_FAILED"
                or value.get("fallback") != {
                    "status": "HOST_EXACT_PLAN_FALLBACK",
                    "model_calls_charged": 0}
                or value.get("scientific_evidence_created") is not False
                or not isinstance(attempts, list) or len(attempts) != 3
                or allocation_entry is None or len(task_rows) != 3):
            return False
        record_tasks.add(task_id)
        allocation, epoch_digest = allocation_entry
        if (allocation.get("plan_sha256") != value.get("plan_sha256")
                or epoch_digest != value.get("digest_sha256")):
            return False
        task_rows = sorted(task_rows, key=lambda row: row.get("attempt", -1))
        if ([row.get("attempt") for row in task_rows] != [1, 2, 3]
                or any(row.get("status") != "FAILED"
                       or row.get("failure_class")
                       != "MODEL_NOMINATION_PROTOCOL_ERROR"
                       for row in task_rows)
                or value.get("model_call_numbers")
                != [row.get("model_call_number") for row in task_rows]):
            return False
        for attempt, row in zip(attempts, task_rows, strict=True):
            if (not isinstance(attempt, Mapping)
                    or attempt.get("model_call_number")
                    != row.get("model_call_number")
                    or attempt.get("call_id") != row.get("call_id")
                    or row.get("response_error") != {
                        key: item for key, item in attempt.items()
                        if key not in {"model_call_number", "call_id"}
                    }):
                return False
            try:
                attempt_path = (root / attempt["attempt_directory"]).resolve()
                expected_parent = (
                    output / "local-inference-v4-1" / task_id).resolve()
                packet = json.loads((attempt_path / "packet.json").read_text(
                    encoding="utf-8"))
                process = json.loads((attempt_path / "process.json").read_text(
                    encoding="utf-8"))
                saved_error = json.loads((
                    attempt_path / "response-error.json").read_text(
                        encoding="utf-8"))
                plan_seed = ResearchPlanV4.from_dict(packet["seed_plans"][0])
                if (attempt_path.parent != expected_parent
                        or packet.get("task_id") != task_id
                        or packet.get("digest_sha256")
                        != value.get("digest_sha256")
                        or len(packet.get("seed_plans", [])) != 1
                        or plan_seed.identity != value.get("plan_sha256")
                        or canonical_hash(packet) != attempt.get("packet_sha256")
                        or saved_error != {
                            key: item for key, item in attempt.items()
                            if key not in {
                                "model_call_number", "call_id",
                                "response_error_sha256"}
                        }
                        or sha256_file(attempt_path / "stdout.bin")
                        != attempt.get("raw_stdout_sha256")
                        or sha256_file(attempt_path / "process.json")
                        != attempt.get("process_sha256")
                        or sha256_file(attempt_path / "response-error.json")
                        != attempt.get("response_error_sha256")
                        or process.get("exit_code") != 0
                        or process.get("timed_out") is not False
                        or process.get("output_limit_exceeded") is not False
                        or process.get("cleanup_failed") is not False
                        or process.get("reader_errors") != []):
                    return False
            except (KeyError, IndexError, OSError, UnicodeError,
                    json.JSONDecodeError, TypeError, ValueError):
                return False
    exhausted_protocol_tasks = {
        task_id for task_id in tasks_with_protocol_errors
        if len(journal_by_task.get(task_id, [])) == 3
        and all(row.get("failure_class")
                == "MODEL_NOMINATION_PROTOCOL_ERROR"
                for row in journal_by_task[task_id])
    }
    for task_id in tasks_with_protocol_errors:
        rows = sorted(
            journal_by_task.get(task_id, []),
            key=lambda row: row.get("attempt", -1))
        allocation_entry = allocations.get(task_id)
        if (not 1 <= len(rows) <= 3 or allocation_entry is None
                or (task_id not in exhausted_protocol_tasks
                    and rows[-1].get("status") != "COMPLETED")):
            return False
        allocation, epoch_digest = allocation_entry
        for row in rows:
            if row.get("failure_class") != "MODEL_NOMINATION_PROTOCOL_ERROR":
                continue
            audit = row.get("response_error")
            if not isinstance(audit, Mapping):
                return False
            try:
                attempt_path = (root / audit["attempt_directory"]).resolve()
                expected_parent = (
                    output / "local-inference-v4-1" / task_id).resolve()
                packet = json.loads((attempt_path / "packet.json").read_text(
                    encoding="utf-8"))
                process = json.loads((attempt_path / "process.json").read_text(
                    encoding="utf-8"))
                saved_error = json.loads((
                    attempt_path / "response-error.json").read_text(
                        encoding="utf-8"))
                seed = ResearchPlanV4.from_dict(packet["seed_plans"][0])
                if (attempt_path.parent != expected_parent
                        or packet.get("task_id") != task_id
                        or packet.get("digest_sha256") != epoch_digest
                        or len(packet.get("seed_plans", [])) != 1
                        or seed.identity != allocation.get("plan_sha256")
                        or canonical_hash(packet) != audit.get("packet_sha256")
                        or saved_error != {
                            key: item for key, item in audit.items()
                            if key != "response_error_sha256"}
                        or sha256_file(attempt_path / "stdout.bin")
                        != audit.get("raw_stdout_sha256")
                        or sha256_file(attempt_path / "process.json")
                        != audit.get("process_sha256")
                        or sha256_file(attempt_path / "response-error.json")
                        != audit.get("response_error_sha256")
                        or process.get("exit_code") != 0
                        or process.get("timed_out") is not False
                        or process.get("output_limit_exceeded") is not False
                        or process.get("cleanup_failed") is not False
                        or process.get("reader_errors") != []):
                    return False
            except (KeyError, IndexError, OSError, UnicodeError,
                    json.JSONDecodeError, TypeError, ValueError):
                return False
    return exhausted_protocol_tasks == record_tasks


def verify_scheduler_state_v4_1(
    root: Path | str, state: Mapping[str, Any],
    continuation_record: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently bind a terminal scheduler to the V4.1 continuation."""
    root = Path(root).resolve()
    failures: list[str] = []
    checks: dict[str, bool] = {}
    try:
        expected_record = build_continuation_import_record_v4_1(root)
        checks["continuation_record_exact"] = dict(continuation_record) == expected_record
        continuation = state.get("continuation_v4_1")
        checks["continuation_scheduler_binding"] = (
            isinstance(continuation, Mapping)
            and continuation.get("source_campaign_id") == SOURCE_CAMPAIGN_ID
            and continuation.get("source_recovery_state_sha256")
            == expected_record["source_recovery_state_sha256"]
            and continuation.get("continuation_import_sha256")
            == canonical_hash(expected_record)
            and continuation.get("candidate_imports_sha256")
            == expected_record["candidate_imports_sha256"])
        checks["original_absolute_deadline"] = (
            state.get("started_at_utc") == SOURCE_STARTED_AT_UTC
            and state.get("deadline_utc") == ABSOLUTE_DEADLINE_AT_UTC
            and isinstance(continuation, Mapping)
            and continuation.get("absolute_deadline_at_utc")
            == ABSOLUTE_DEADLINE_AT_UTC)
        reconciliation = (
            continuation.get("incident_reconciliation")
            if isinstance(continuation, Mapping) else None)
        checks["exact_zero_call_fallback"] = (
            isinstance(reconciliation, Mapping)
            and reconciliation.get("status") == "HOST_EXACT_PLAN_FALLBACK"
            and reconciliation.get("task_id") == FAILED_TASK_ID
            and reconciliation.get("plan_sha256") == FAILED_PLAN_SHA256
            and reconciliation.get("model_calls_before") == CONSUMED_MODEL_CALLS
            and reconciliation.get("model_calls_charged") == 0
            and reconciliation.get("model_calls_after") == CONSUMED_MODEL_CALLS
            and reconciliation.get("model_nomination_succeeded") is False
            and reconciliation.get("model_response_fabricated") is False)
        history = state.get("allocation_history")
        incident_allocations = []
        if isinstance(history, list) and len(history) >= CURRENT_EPOCH:
            allocations = history[CURRENT_EPOCH - 1].get("allocations")
            if isinstance(allocations, list):
                incident_allocations = [
                    row for row in allocations
                    if isinstance(row, Mapping)
                    and row.get("task_id") == FAILED_TASK_ID]
        checks["fallback_plan_is_preallocated_singleton"] = (
            len(incident_allocations) == 1
            and incident_allocations[0].get("plan_sha256") == FAILED_PLAN_SHA256
            and isinstance(incident_allocations[0].get("candidate_id"), str))
        journal = state.get("call_journal")
        call_86 = None
        call_numbers: list[int] = []
        if isinstance(journal, list):
            for row in journal:
                if not isinstance(row, Mapping):
                    continue
                number = row.get("model_call_number")
                if type(number) is int:
                    call_numbers.append(number)
                if number == 86:
                    call_86 = row
        checks["call_86_reconciled_failed"] = (
            isinstance(call_86, Mapping)
            and call_86.get("task_id") == FAILED_TASK_ID
            and call_86.get("status") == "FAILED"
            and call_86.get("completed_at_utc")
            == "2026-09-26T21:51:14.126716+00:00"
            and call_86.get("failure_class") == "MODEL_NOMINATION_FAILED")
        checks["model_call_numbers_contiguous"] = call_numbers == list(
            range(1, len(call_numbers) + 1))
        reserved_tokens = state.get("model_context_tokens_reserved")
        recorded_model_calls = state.get("model_calls_reserved")
        checks["actual_context_token_accounting"] = (
            type(recorded_model_calls) is int
            and recorded_model_calls == len(call_numbers)
            and type(reserved_tokens) is int
            and reserved_tokens == (
                recorded_model_calls * CONTEXT_TOKENS_PER_MODEL_CALL)
            and reserved_tokens >= CONSUMED_CONTEXT_TOKENS)
        checks["future_nomination_failures_bound"] = (
            _verify_future_nomination_failures_v4_1(root, state))
        checks["cumulative_budget_floors"] = (
            len(call_numbers) >= CONSUMED_MODEL_CALLS
            and int(state.get("candidate_calls_used", -1)) >= 83
            and int(state.get("synthesis_calls_used", -1)) >= 1
            and int(state.get("transient_retry_calls_used", -1)) >= 2
            and isinstance(continuation, Mapping)
            and continuation.get("consumed_candidates_floor")
            == CONSUMED_CANDIDATES
            and continuation.get("consumed_model_calls_floor")
            == CONSUMED_MODEL_CALLS
            and continuation.get("consumed_context_tokens_floor")
            == CONSUMED_CONTEXT_TOKENS
            and continuation.get("consumed_wall_seconds_floor")
            == CONSUMED_WALL_SECONDS)
        base = verify_scheduler_state_v4(root, state)
        checks["base_v4_scheduler_verifier"] = base.get("status") == "PASS"
        for name, passed in checks.items():
            if not passed:
                failures.append(name.upper())
    except (KeyError, TypeError, ValueError, V4_1ContinuationError) as exc:
        failures.append(f"FAIL_CLOSED:{exc}")
    passed = bool(checks) and all(checks.values()) and not failures
    body = {
        "verifier_version": "klax-v4.1-independent-scheduler-verifier-v1",
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "failures": sorted(set(failures)),
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "verification_sha256": canonical_hash(body)}


@dataclass
class ProductionV4_1Orchestrator(ProductionV4Orchestrator):
    """New-campaign continuation host with immutable V4 ancestry."""

    continuation_record: dict[str, Any] | None = None
    source_output_root: Path | None = None
    absolute_deadline_at_utc: str = ABSOLUTE_DEADLINE_AT_UTC

    def __post_init__(self) -> None:
        self.output_root = self.engine.authorization.assert_development_path(
            self.output_root)
        expected_parent = (
            self.engine.authorization.root / OUTPUT_PARENT_V4_1).resolve()
        if self.output_root.parent != expected_parent:
            raise V3CampaignError(
                "V4.1 output must use runs/campaigns_v4_1/<campaign-id>")
        if (self.output_root.name != self.engine.authorization.campaign_id
                or not self.output_root.name.startswith("v4-1-offline-")
                or self.output_root.name == SOURCE_CAMPAIGN_ID):
            raise V3CampaignError("V4.1 campaign identity is invalid")
        if (self.continuation_record is None
                or self.continuation_record.get("record_version")
                != CONTINUATION_RECORD_VERSION):
            raise V3IntegrityStop("V4.1 continuation record is missing")
        if self.source_output_root is None:
            raise V3IntegrityStop("V4.1 source output root is missing")
        self.source_output_root = Path(self.source_output_root).resolve()
        expected_source = (
            self.engine.authorization.root / SOURCE_ROOT).resolve()
        if self.source_output_root != expected_source:
            raise V3IntegrityStop("V4.1 source output root differs")
        if (self.started_at_utc != SOURCE_STARTED_AT_UTC
                or self.absolute_deadline_at_utc != ABSOLUTE_DEADLINE_AT_UTC):
            raise V3IntegrityStop("V4.1 original time boundary differs")
        self.output_root.mkdir(parents=True, exist_ok=True)
        if self.queue is None or self.coverage is None:
            raise V3IntegrityStop("V4.1 requires imported queue and coverage")
        self.coverage["architecture_version"] = "4.1-continuation"
        self.coverage["runner_version"] = RUNNER_VERSION_V4_1
        self.coverage["continuation_import"] = {
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "record_sha256": canonical_hash(self.continuation_record),
            "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
            "source_candidate_count": CONSUMED_CANDIDATES,
            "source_model_calls": CONSUMED_MODEL_CALLS,
        }

    def _engine_state(self) -> dict[str, Any]:
        state = super()._engine_state()
        assert isinstance(self.engine, V4_1CampaignEngine)
        state["elapsed_seconds"] = self.engine.cumulative_wall_seconds()
        state["absolute_deadline_at_utc"] = self.absolute_deadline_at_utc
        state["source_campaign_id"] = SOURCE_CAMPAIGN_ID
        return state

    def save_recovery(self) -> dict[str, Any]:
        body = {
            "recovery_version": RECOVERY_VERSION_V4_1,
            "orchestrator_version": RUNNER_VERSION_V4_1,
            "campaign_id": self.engine.authorization.campaign_id,
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "continuation_import_sha256": canonical_hash(
                self.continuation_record),
            "readiness_sha256": self.engine.authorization.readiness_sha256,
            "ticket_sha256": self.engine.authorization.ticket_sha256,
            "phase": self.phase,
            "started_at_utc": SOURCE_STARTED_AT_UTC,
            "absolute_deadline_at_utc": self.absolute_deadline_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "queue": self.queue,
            "next_queue_index": self.next_queue_index,
            "coverage": self.coverage,
            "engine": self._engine_state(),
            "plan_language": "klax-research-plan-v4",
            "data_bundle_path": DATA_BUNDLE_PATH.as_posix(),
            "protected_final_evaluated": False,
            "actual_orders_placed": False,
        }
        value = {**body, "state_sha256": canonical_hash(body)}
        write_json(self.state_path, value)
        return value

    def _record_evidence(self, record: CandidateRecord) -> dict[str, Any]:
        primary_dir = Path(self.primary_evaluator.artifact_root) / record.plan.identity
        if not primary_dir.is_dir():
            primary_dir = self.source_output_root / "candidates" / "primary" / (
                record.plan.identity)
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

    def _assert_protocol_attempt_artifacts(
        self, item: Mapping[str, Any], packet: Mapping[str, Any],
        attempts: list[Mapping[str, Any]],
    ) -> None:
        expected_parent = (
            self.output_root / "local-inference-v4-1"
            / str(item.get("task_id"))).resolve()
        seen: set[str] = set()
        for row in attempts:
            try:
                relative = row["attempt_directory"]
                attempt_path = (
                    self.engine.authorization.root / relative).resolve()
                if (attempt_path.parent != expected_parent
                        or relative in seen):
                    raise V3IntegrityStop(
                        "V4.1 response-error artifact path differs")
                seen.add(relative)
                saved_packet = json.loads((
                    attempt_path / "packet.json").read_text(encoding="utf-8"))
                process = json.loads((
                    attempt_path / "process.json").read_text(encoding="utf-8"))
                saved_error = json.loads((
                    attempt_path / "response-error.json").read_text(
                        encoding="utf-8"))
                error_without_journal = {
                    key: value for key, value in row.items()
                    if key not in {
                        "model_call_number", "call_id",
                        "response_error_sha256"}
                }
                if (saved_packet != dict(packet)
                        or saved_error != error_without_journal
                        or sha256_file(attempt_path / "stdout.bin")
                        != row.get("raw_stdout_sha256")
                        or sha256_file(attempt_path / "process.json")
                        != row.get("process_sha256")
                        or sha256_file(attempt_path / "response-error.json")
                        != row.get("response_error_sha256")
                        or process.get("exit_code") != 0
                        or process.get("timed_out") is not False
                        or process.get("output_limit_exceeded") is not False
                        or process.get("cleanup_failed") is not False
                        or process.get("reader_errors") != []
                        or row.get("task_id") != item.get("task_id")
                        or row.get("packet_sha256") != canonical_hash(packet)
                        or row.get("model_response_accepted") is not False
                        or row.get("scientific_evidence_created") is not False):
                    raise V3IntegrityStop(
                        "V4.1 response-error artifact content differs")
            except V3IntegrityStop:
                raise
            except (KeyError, OSError, UnicodeError, json.JSONDecodeError,
                    TypeError, ValueError) as exc:
                raise V3IntegrityStop(
                    "V4.1 response-error artifact is unavailable") from exc

    def _validated_future_nomination_failure(
        self, item: Mapping[str, Any], plan: ResearchPlanV4,
        digest_sha256: str, packet: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        rows = self.coverage.setdefault("v4_1_model_nomination_failures", [])
        matches = [row for row in rows if isinstance(row, Mapping)
                   and row.get("task_id") == item.get("task_id")]
        if not matches:
            return None
        if len(matches) != 1:
            raise V3IntegrityStop("Duplicate V4.1 nomination failure record")
        value = dict(matches[0])
        claimed = value.pop("record_sha256", None)
        attempts = value.get("attempts")
        expected_attempts = 1 + self.engine.budget.maximum_transient_retries_per_task
        attempt_numbers = (
            [row.get("model_call_number") for row in attempts]
            if isinstance(attempts, list) else [])
        journal_by_number = {
            row.get("model_call_number"): row
            for row in self.coverage.get("call_journal", [])
            if isinstance(row, Mapping)
        }
        seen_attempt_directories: set[str] = set()
        artifact_bindings = True
        if isinstance(attempts, list):
            expected_parent = (
                self.output_root / "local-inference-v4-1"
                / str(item.get("task_id"))).resolve()
            for row in attempts:
                try:
                    relative = row["attempt_directory"]
                    attempt_path = (
                        self.engine.authorization.root / relative).resolve()
                    if (attempt_path.parent != expected_parent
                            or relative in seen_attempt_directories):
                        artifact_bindings = False
                        break
                    seen_attempt_directories.add(relative)
                    saved_packet = json.loads((
                        attempt_path / "packet.json").read_text(
                            encoding="utf-8"))
                    process = json.loads((
                        attempt_path / "process.json").read_text(
                            encoding="utf-8"))
                    saved_error = json.loads((
                        attempt_path / "response-error.json").read_text(
                            encoding="utf-8"))
                    if (saved_packet != dict(packet)
                            or saved_error != {
                                key: value for key, value in row.items()
                                if key not in {
                                    "model_call_number", "call_id",
                                    "response_error_sha256"}
                            }
                            or sha256_file(attempt_path / "stdout.bin")
                            != row.get("raw_stdout_sha256")
                            or sha256_file(attempt_path / "process.json")
                            != row.get("process_sha256")
                            or sha256_file(attempt_path / "response-error.json")
                            != row.get("response_error_sha256")
                            or process.get("exit_code") != 0
                            or process.get("timed_out") is not False
                            or process.get("output_limit_exceeded") is not False
                            or process.get("cleanup_failed") is not False
                            or process.get("reader_errors") != []):
                        artifact_bindings = False
                        break
                except (KeyError, OSError, UnicodeError, json.JSONDecodeError,
                        TypeError, ValueError):
                    artifact_bindings = False
                    break
        attempt_bindings = (
            isinstance(attempts, list)
            and artifact_bindings
            and all(
                row.get("task_id") == item.get("task_id")
                and row.get("packet_sha256") == canonical_hash(packet)
                and row.get("model_response_accepted") is False
                and isinstance(row.get("raw_stdout_sha256"), str)
                and len(row["raw_stdout_sha256"]) == 64
                and journal_by_number.get(row.get("model_call_number"), {}).get(
                    "failure_class") == "MODEL_NOMINATION_PROTOCOL_ERROR"
                for row in attempts))
        if (claimed != canonical_hash(value)
                or value.get("failure_version")
                != "klax-v4.1-model-nomination-failure-v1"
                or value.get("status") != "MODEL_NOMINATION_FAILED"
                or value.get("task_id") != item.get("task_id")
                or value.get("plan_sha256") != plan.identity
                or value.get("digest_sha256") != digest_sha256
                or not isinstance(attempts, list)
                or len(attempts) != expected_attempts
                or not attempt_bindings
                or value.get("model_call_numbers")
                != attempt_numbers
                or attempt_numbers != list(range(
                    attempt_numbers[0], attempt_numbers[0] + expected_attempts))
                or value.get("fallback") != {
                    "status": "HOST_EXACT_PLAN_FALLBACK",
                    "model_calls_charged": 0,
                }
                or value.get("scientific_evidence_created") is not False):
            raise V3IntegrityStop("V4.1 nomination failure record differs")
        return {**value, "record_sha256": claimed}

    def _make_nomination_failure(
        self, item: Mapping[str, Any], plan: ResearchPlanV4,
        digest_sha256: str, attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        body = {
            "failure_version": "klax-v4.1-model-nomination-failure-v1",
            "status": "MODEL_NOMINATION_FAILED",
            "task_id": item["task_id"],
            "plan_sha256": plan.identity,
            "digest_sha256": digest_sha256,
            "model_call_numbers": [
                row["model_call_number"] for row in attempts],
            "attempts": attempts,
            "fallback": {
                "status": "HOST_EXACT_PLAN_FALLBACK",
                "model_calls_charged": 0,
            },
            "scientific_evidence_created": False,
            "protected_final_read": False,
            "actual_orders_placed": False,
        }
        return {**body, "record_sha256": canonical_hash(body)}

    def _resume_packet_and_rows(
        self, item: Mapping[str, Any], plan: ResearchPlanV4,
        generated_packet: Mapping[str, Any],
    ) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
        rows = [
            row for row in self.coverage.get("call_journal", [])
            if isinstance(row, Mapping)
            and row.get("task_id") == item["task_id"]
            and row.get("kind") == "candidate_nomination"]
        if not rows:
            return dict(generated_packet), []
        first = min(rows, key=lambda row: row.get("attempt", -1))
        try:
            relative = first["nomination_packet_path"]
            packet_path = (self.engine.authorization.root / relative).resolve()
            saved = validate_v4_worker_packet(json.loads((
                packet_path).read_text(encoding="utf-8")))
            if (packet_path != (
                    self.output_root / "tasks" / str(item["task_id"])
                    / "nomination-packet.json").resolve()
                    or first.get("nomination_packet_file_sha256")
                    != sha256_file(packet_path)
                    or first.get("nomination_packet_sha256")
                    != canonical_hash(saved)):
                raise V3IntegrityStop("V4.1 saved nomination packet differs")
        except (KeyError, OSError, UnicodeError, json.JSONDecodeError,
                V4WorkerProtocolError, V3IntegrityStop) as exc:
            raise V3IntegrityStop(
                "V4.1 saved retry packet is unavailable or invalid") from exc
        generated_static = {
            key: value for key, value in generated_packet.items()
            if key != "budget_remaining"}
        saved_static = {
            key: value for key, value in saved.items()
            if key != "budget_remaining"}
        budget = saved.get("budget_remaining")
        first_call = first.get("model_call_number")
        expected_model_remaining = (
            self.engine.budget.maximum_local_model_calls - (first_call - 1)
            if type(first_call) is int else None)
        expected_token_remaining = (
            self.engine.budget.local_reserved_context_tokens
            - ((first_call - 1) * CONTEXT_TOKENS_PER_MODEL_CALL)
            if type(first_call) is int else None)
        comparable_budget_keys = {
            "epoch", "epochs_remaining", "candidate_slots_remaining",
            "epoch_candidate_slots_remaining", "paid_api_dollars_remaining",
        }
        generated_budget = generated_packet.get("budget_remaining", {})
        if (saved_static != generated_static
                or len(saved.get("seed_plans", [])) != 1
                or ResearchPlanV4.from_dict(saved["seed_plans"][0]).identity
                != plan.identity
                or not isinstance(budget, Mapping)
                or budget.get("model_calls_remaining")
                != expected_model_remaining
                or budget.get("reserved_context_tokens_remaining")
                != expected_token_remaining
                or any(budget.get(key) != generated_budget.get(key)
                       for key in comparable_budget_keys)
                or type(budget.get("wall_seconds_remaining")) is not int
                or not 0 <= budget["wall_seconds_remaining"] <= (
                    self.engine.budget.maximum_wall_seconds)):
            raise V3IntegrityStop("V4.1 saved retry packet lineage differs")
        return saved, rows

    def _reconcile_prior_nomination_rows(
        self, item: Mapping[str, Any], packet: Mapping[str, Any],
        rows: list[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], Mapping[str, Any] | None]:
        checked_rows = [dict(row) for row in rows]
        checked_rows.sort(key=lambda row: row.get("attempt", -1))
        if [row.get("attempt") for row in checked_rows] != list(
                range(1, len(checked_rows) + 1)):
            raise V3IntegrityStop("V4.1 prior nomination attempt sequence differs")
        changed = False
        protocol_attempts: list[dict[str, Any]] = []
        last_response: Mapping[str, Any] | None = None
        for row in checked_rows:
            attempt_path = (
                self.output_root / "local-inference-v4-1"
                / str(item["task_id"]) / f"attempt-{row['attempt']}").resolve()
            if row.get("status") == "RESERVED":
                process_ok = False
                try:
                    process = json.loads((attempt_path / "process.json").read_text(
                        encoding="utf-8"))
                    process_ok = (
                        process.get("exit_code") == 0
                        and process.get("timed_out") is False
                        and process.get("output_limit_exceeded") is False
                        and process.get("cleanup_failed") is False
                        and process.get("reader_errors") == [])
                except (OSError, UnicodeError, json.JSONDecodeError):
                    process = None
                if process_ok and (attempt_path / "response-error.json").is_file():
                    audit = json.loads((
                        attempt_path / "response-error.json").read_text(
                            encoding="utf-8"))
                    audit["response_error_sha256"] = sha256_file(
                        attempt_path / "response-error.json")
                    row.update({
                        "status": "FAILED", "completed_at_utc": _now(),
                        "failure_class": "MODEL_NOMINATION_PROTOCOL_ERROR",
                        "response_error": audit,
                        "crash_reconciled": True,
                    })
                elif process_ok and (attempt_path / "proposal.json").is_file():
                    proposal = json.loads((attempt_path / "proposal.json").read_text(
                        encoding="utf-8"))
                    response = parse_v4_1_worker_response(
                        json.dumps(proposal.get("response"), sort_keys=True,
                                   separators=(",", ":")), packet)
                    row.update({
                        "status": "COMPLETED", "completed_at_utc": _now(),
                        "response_artifact_path": (
                            attempt_path / "proposal.json").relative_to(
                                self.engine.authorization.root).as_posix(),
                        "response_artifact_sha256": sha256_file(
                            attempt_path / "proposal.json"),
                        "response_sha256": canonical_hash(response),
                        "crash_reconciled": True,
                    })
                else:
                    row.update({
                        "status": "FAILED", "completed_at_utc": _now(),
                        "failure_class": (
                            "PROCESS_OUTCOME_UNAVAILABLE_AFTER_CRASH"),
                        "crash_reconciled": True,
                    })
                changed = True
            if row.get("status") == "COMPLETED":
                try:
                    proposal_path = (
                        self.engine.authorization.root
                        / row["response_artifact_path"]).resolve()
                    if (proposal_path != attempt_path / "proposal.json"
                            or row.get("response_artifact_sha256")
                            != sha256_file(proposal_path)):
                        raise V3IntegrityStop(
                            "V4.1 completed response artifact differs")
                    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
                    last_response = parse_v4_1_worker_response(
                        json.dumps(proposal.get("response"), sort_keys=True,
                                   separators=(",", ":")), packet)
                    if row.get("response_sha256") != canonical_hash(last_response):
                        raise V3IntegrityStop(
                            "V4.1 completed response hash differs")
                except (KeyError, OSError, UnicodeError, json.JSONDecodeError,
                        V4WorkerProtocolError) as exc:
                    raise V3IntegrityStop(
                        "V4.1 completed response cannot be recovered") from exc
            elif row.get("failure_class") == "MODEL_NOMINATION_PROTOCOL_ERROR":
                audit = row.get("response_error")
                if not isinstance(audit, Mapping):
                    raise V3IntegrityStop(
                        "V4.1 parser failure lacks response evidence")
                protocol_attempts.append({
                    **dict(audit),
                    "model_call_number": row.get("model_call_number"),
                    "call_id": row.get("call_id"),
                })
                last_response = None
            else:
                last_response = None
        if changed:
            replacements = {
                row["model_call_number"]: row for row in checked_rows}
            self.coverage["call_journal"] = [
                replacements.get(row.get("model_call_number"), row)
                if isinstance(row, Mapping) else row
                for row in self.coverage["call_journal"]]
            self.save_recovery()
        if protocol_attempts:
            self._assert_protocol_attempt_artifacts(
                item, packet, protocol_attempts)
        return protocol_attempts, last_response

    def _process_item(self, item: dict[str, Any]) -> None:
        """Use a zero-call exact-plan fallback only for exhausted parse errors."""
        plan = ResearchPlanV4.from_dict(item["plan"])
        colony = self.coverage["colonies"][plan.colony]
        if item["status"] == "PENDING":
            generated_packet = self._packet_with_digest(item)
            packet, prior_rows = self._resume_packet_and_rows(
                item, plan, generated_packet)
            digest_sha256 = self.coverage["digests"][-1]["digest_sha256"]
            failure = self._validated_future_nomination_failure(
                item, plan, digest_sha256, packet)
            response: Mapping[str, Any] | None = None
            role = item["role"]
            worker_id = (
                f"{role}-{plan.colony}-e{self.engine.current_epoch:03d}"
                f"-s{item['task_id'][-2:]}")
            if failure is None:
                maximum_attempts = (
                    1 + self.engine.budget.maximum_transient_retries_per_task)
                protocol_attempts, response = (
                    self._reconcile_prior_nomination_rows(
                        item, packet, prior_rows))
                start_attempt = len(prior_rows)
                if not (isinstance(response, Mapping)
                        and response.get("action") == "propose"):
                    expected_retries = min(start_attempt, maximum_attempts - 1)
                    saved_retries = self.engine.transient_retries.get(
                        item["task_id"], 0)
                    if saved_retries > expected_retries:
                        raise V3IntegrityStop(
                            "V4.1 saved retry accounting exceeds attempts")
                    while saved_retries < expected_retries:
                        self.engine.register_transient_failure(item["task_id"])
                        saved_retries += 1
                    if saved_retries:
                        self.save_recovery()
                if start_attempt > maximum_attempts:
                    raise V3IntegrityStop(
                        "V4.1 nomination-attempt budget was exceeded")
                if start_attempt == maximum_attempts:
                    if len(protocol_attempts) == maximum_attempts:
                        failure = self._make_nomination_failure(
                            item, plan, digest_sha256, protocol_attempts)
                        self.coverage[
                            "v4_1_model_nomination_failures"].append(failure)
                        write_json(
                            self.output_root / "tasks" / item["task_id"]
                            / "nomination-failure.json", failure)
                        self.save_recovery()
                    elif not (isinstance(response, Mapping)
                              and response.get("action") in {
                                  "propose", "reject", "abstain"}):
                        raise V3IntegrityStop(
                            "V4.1 exhausted nomination has no recoverable response")
                if (start_attempt < maximum_attempts
                        and not (isinstance(response, Mapping)
                                 and response.get("action") == "propose")):
                    if self.worker_factory is None:
                        raise V3IntegrityStop(
                            "V4 production worker factory is missing")
                    worker = self.worker_factory(worker_id, role)
                    if worker.worker_id != worker_id:
                        raise V3IntegrityStop("V4 worker identity differs")
                packet_path = (
                    self.output_root / "tasks" / item["task_id"]
                    / "nomination-packet.json")
                if not packet_path.exists():
                    write_json(packet_path, packet)
                if json.loads(packet_path.read_text(encoding="utf-8")) != packet:
                    raise V3IntegrityStop("V4.1 nomination packet artifact differs")
                packet_relative = packet_path.relative_to(
                    self.engine.authorization.root).as_posix()
                packet_file_sha256 = sha256_file(packet_path)
                for attempt in range(start_attempt, maximum_attempts):
                    if (isinstance(response, Mapping)
                            and response.get("action") == "propose"):
                        break
                    journal = {
                        "task_id": item["task_id"],
                        "kind": "candidate_nomination",
                        "call_type": "candidate" if attempt == 0 else "retry",
                        "worker_id": worker_id,
                        "status": "PENDING",
                        "attempt": attempt + 1,
                        "digest_sha256": digest_sha256,
                        "nomination_packet_path": packet_relative,
                        "nomination_packet_file_sha256": packet_file_sha256,
                        "nomination_packet_sha256": canonical_hash(packet),
                    }
                    try:
                        with self.engine.model_call(worker_id):
                            journal["status"] = "RESERVED"
                            journal["model_call_number"] = self.engine.model_calls
                            journal["call_id"] = (
                                f"{journal['call_type']}-"
                                f"{self.engine.model_calls:04d}-{item['task_id']}")
                            journal["started_at_utc"] = _now()
                            self.coverage["call_journal"].append(journal)
                            self.save_recovery()
                            response = worker.respond(packet)
                        proposal_path = (
                            self.output_root / "local-inference-v4-1"
                            / str(item["task_id"])
                            / f"attempt-{attempt + 1}" / "proposal.json")
                        if not proposal_path.is_file():
                            raise V3IntegrityStop(
                                "V4.1 successful response artifact is missing")
                        journal["status"] = "COMPLETED"
                        journal["completed_at_utc"] = _now()
                        journal["response_artifact_path"] = (
                            proposal_path.relative_to(
                                self.engine.authorization.root).as_posix())
                        journal["response_artifact_sha256"] = sha256_file(
                            proposal_path)
                        journal["response_sha256"] = canonical_hash(response)
                        if response.get("action") == "propose":
                            self.save_recovery()
                            break
                        if attempt + 1 < maximum_attempts:
                            self.engine.register_transient_failure(
                                item["task_id"])
                            self.save_recovery()
                            continue
                        self.save_recovery()
                        break
                    except V4_1CandidateResponseProtocolError as exc:
                        journal["status"] = "FAILED"
                        journal["completed_at_utc"] = _now()
                        journal["failure_class"] = "MODEL_NOMINATION_PROTOCOL_ERROR"
                        audit = dict(exc.audit)
                        attempt_path = (
                            self.engine.authorization.root
                            / audit["attempt_directory"]).resolve()
                        audit["response_error_sha256"] = sha256_file(
                            attempt_path / "response-error.json")
                        journal["response_error"] = audit
                        protocol_attempts.append({
                            **audit,
                            "model_call_number": journal["model_call_number"],
                            "call_id": journal["call_id"],
                        })
                        if attempt + 1 < maximum_attempts:
                            self.engine.register_transient_failure(item["task_id"])
                            self.save_recovery()
                            continue
                        self.save_recovery()
                        if len(protocol_attempts) != maximum_attempts:
                            raise V3IntegrityStop(
                                "V4.1 parser fallback requires every attempt "
                                "to fail response parsing") from exc
                        failure = self._make_nomination_failure(
                            item, plan, digest_sha256, protocol_attempts)
                        self.coverage[
                            "v4_1_model_nomination_failures"].append(failure)
                        write_json(
                            self.output_root / "tasks" / item["task_id"]
                            / "nomination-failure.json", failure)
                        self.save_recovery()
                    except (OSError, RuntimeError, V4WorkerProtocolError):
                        journal["status"] = "FAILED"
                        journal["completed_at_utc"] = _now()
                        journal["failure_class"] = "MODEL_RUNTIME_OR_BOUNDARY_FAILURE"
                        if attempt + 1 >= maximum_attempts:
                            self.save_recovery()
                            raise
                        self.engine.register_transient_failure(item["task_id"])
                        self.save_recovery()
                if failure is not None:
                    failure = self._validated_future_nomination_failure(
                        item, plan, digest_sha256, packet)
                if response is None and failure is None:
                    raise V3IntegrityStop(
                        "V4.1 nomination ended without response or failure record")
            fallback = failure is not None
            proposed = (isinstance(response, Mapping)
                        and response.get("action") == "propose"
                        and response.get("seed_index") == 0)
            valid_nonproposal = (isinstance(response, Mapping)
                        and response.get("action") in {"reject", "abstain"})
            if not fallback and not proposed and not valid_nonproposal:
                raise V3IntegrityStop(
                    "V4.1 bounded nomination returned an invalid selection")
            calls_before_admission = self.engine.model_calls
            result = self.engine.admit_registered_plan_v4(
                plan, worker_id=worker_id, task_id=str(item["task_id"]))
            if self.engine.model_calls != calls_before_admission:
                raise V3IntegrityStop("V4.1 host fallback charged a fourth call")
            result["nomination_fallback_used"] = bool(
                fallback or valid_nonproposal)
            saved_response = {
                "worker_response": (
                    None if response is None else dict(response)),
                "model_nomination_failure": failure,
                "host_disposition": (
                    "ADMIT_EXACT_REGISTERED_PLAN_AFTER_PROTOCOL_EXHAUSTION"
                    if fallback else
                    "ADMIT_EXACT_REGISTERED_PLAN_AFTER_CHARGED_NONPROPOSAL"
                    if valid_nonproposal else
                    "ADMIT_WORKER_NOMINATED_EXACT_REGISTERED_PLAN"),
                "plan_sha256": plan.identity,
                "model_response_fabricated": False,
            }
            self._save_task(item, packet, saved_response, result)
            if result.get("status") != "ADMITTED":
                raise V3IntegrityStop(
                    "V4.1 fixed-quota task did not admit its registered plan")
            if not fallback:
                colony["proposed"] += 1
            colony["admitted"] += 1
            item["candidate_id"] = result["candidate_id"]
            history = self.coverage["allocation_history"][-1]
            history["allocations"][self.next_queue_index]["candidate_id"] = (
                result["candidate_id"])
            item["status"] = "ADMITTED"
            self.save_recovery()
        if item["status"] in {"ADMITTED", "EVALUATED"}:
            self._execute_and_review(item)

    def apply_incident_fallback_v4_1(self) -> dict[str, Any]:
        """Admit/review only the failed preallocated plan without a model call."""
        prior = self.coverage.get("v4_1_incident_reconciliation")
        if isinstance(prior, Mapping):
            if prior.get("status") != "HOST_EXACT_PLAN_FALLBACK":
                raise V3IntegrityStop("V4.1 fallback checkpoint is inconsistent")
            item = self.queue[10]
            if (item.get("task_id") != FAILED_TASK_ID
                    or item.get("candidate_id") is None
                    or item.get("status") not in {"ADMITTED", "EVALUATED", "REVIEWED"}
                    or self.engine.model_calls < CONSUMED_MODEL_CALLS):
                raise V3IntegrityStop("V4.1 fallback checkpoint is inconsistent")
            if item["status"] in {"ADMITTED", "EVALUATED"}:
                calls_before = self.engine.model_calls
                self._execute_and_review(item)
                if self.engine.model_calls != calls_before:
                    raise V3IntegrityStop("V4.1 fallback recovery charged a model call")
            self.next_queue_index = max(self.next_queue_index, 11)
            epoch = self.coverage["epochs"][str(CURRENT_EPOCH)]
            epoch["reviewed"] = sum(
                row["status"] == "REVIEWED" for row in self.queue)
            epoch["admitted"] = sum(
                row["status"] in {"ADMITTED", "EVALUATED", "REVIEWED"}
                for row in self.queue)
            self.save_recovery()
            return dict(prior)
        if (self.next_queue_index != 10 or self.engine.model_calls
                != CONSUMED_MODEL_CALLS):
            raise V3IntegrityStop("V4.1 fallback position or call floor differs")
        item = self.queue[10]
        plan = ResearchPlanV4.from_dict(item["plan"])
        if (item.get("task_id") != FAILED_TASK_ID
                or item.get("status") != "PENDING"
                or plan.identity != FAILED_PLAN_SHA256):
            raise V3IntegrityStop("V4.1 fallback task or plan differs")
        reconciliation = reconcile_failed_nomination_v4_1(
            self.engine.authorization.root, self.continuation_record)
        calls_before = self.engine.model_calls
        worker_id = "critic-adversarial_alternatives-e007-s11"
        result = self.engine.admit_registered_plan_v4(
            plan, worker_id=worker_id, task_id=FAILED_TASK_ID)
        if (result.get("status") != "ADMITTED"
                or self.engine.model_calls != calls_before):
            raise V3IntegrityStop("V4.1 exact-plan fallback charged or failed")
        item["candidate_id"] = result["candidate_id"]
        item["status"] = "ADMITTED"
        allocation = self.coverage["allocation_history"][-1]["allocations"][10]
        if allocation.get("plan_sha256") != FAILED_PLAN_SHA256:
            raise V3IntegrityStop("V4.1 fallback allocation differs")
        allocation["candidate_id"] = result["candidate_id"]
        colony = self.coverage["colonies"][plan.colony]
        colony["admitted"] += 1
        self.coverage["v4_1_incident_reconciliation"] = reconciliation
        write_json(self.output_root / "incident-reconciliation.json", reconciliation)
        write_json(self.output_root / "continuation-import-reference.json", {
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "continuation_import_sha256": canonical_hash(
                self.continuation_record),
            "candidate_imports_sha256": self.continuation_record[
                "candidate_imports_sha256"],
            "protected_final_read": False,
            "actual_orders_placed": False,
        })
        self.save_recovery()
        self._execute_and_review(item)
        self.next_queue_index = 11
        epoch = self.coverage["epochs"][str(CURRENT_EPOCH)]
        epoch["reviewed"] = sum(row["status"] == "REVIEWED" for row in self.queue)
        epoch["admitted"] = sum(
            row["status"] in {"ADMITTED", "EVALUATED", "REVIEWED"}
            for row in self.queue)
        self.save_recovery()
        return reconciliation

    def _finalize_v4(self) -> dict[str, Any]:
        """Stage base output and publish the V4.1 summary as the final commit."""
        output_root = Path(self.output_root).resolve()
        if (output_root / "summary.json").exists():
            if _valid_terminal_commit_v4_1(output_root):
                return {
                    "path": str(output_root),
                    **json.loads((output_root / "summary.json").read_text(
                        encoding="utf-8")),
                }
            raise V3IntegrityStop(
                "V4.1 has an invalid partial terminal summary; repair refused")
        staging_root = (
            output_root.parent / f".{output_root.name}.v4-1-finalization-stage"
        ).resolve()
        if staging_root.parent != output_root.parent:
            raise V3IntegrityStop("V4.1 finalization staging path escaped")
        if staging_root.exists():
            shutil.rmtree(staging_root)
        staging_root.mkdir(parents=True)
        self.output_root = staging_root
        try:
            result = super()._finalize_v4()
        finally:
            self.output_root = output_root
        scheduler_path = staging_root / "scheduler-state.json"
        summary_path = staging_root / "summary.json"
        scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))
        report = json.loads(summary_path.read_text(encoding="utf-8"))
        scheduler["state_version"] = "klax-v4.1-orchestrator-state-v1"
        scheduler["campaign_id"] = self.engine.authorization.campaign_id
        scheduler["started_at_utc"] = SOURCE_STARTED_AT_UTC
        scheduler["deadline_utc"] = ABSOLUTE_DEADLINE_AT_UTC
        scheduler["continuation_v4_1"] = {
            "continuation_version": "4.1",
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "source_recovery_state_sha256": self.continuation_record[
                "source_recovery_state_sha256"],
            "continuation_import_sha256": canonical_hash(
                self.continuation_record),
            "candidate_imports_sha256": self.continuation_record[
                "candidate_imports_sha256"],
            "incident_reconciliation": self.coverage.get(
                "v4_1_incident_reconciliation"),
            "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
            "consumed_candidates_floor": CONSUMED_CANDIDATES,
            "consumed_model_calls_floor": CONSUMED_MODEL_CALLS,
            "consumed_context_tokens_floor": CONSUMED_CONTEXT_TOKENS,
            "consumed_wall_seconds_floor": CONSUMED_WALL_SECONDS,
            "protected_final_read": False,
            "actual_orders_placed": False,
        }
        scheduler["model_calls_reserved"] = self.engine.model_calls
        scheduler["model_context_tokens_reserved"] = (
            self.engine.model_context_tokens_reserved)
        scheduler["model_nomination_failures_v4_1"] = deepcopy(
            self.coverage.get("v4_1_model_nomination_failures", []))
        scheduler.pop("state_sha256", None)
        scheduler["state_sha256"] = canonical_hash(scheduler)
        verification = verify_scheduler_state_v4_1(
            self.engine.authorization.root, scheduler,
            self.continuation_record)
        if verification["status"] != "PASS":
            report["scientific_conclusion"] = "INSUFFICIENT_EVIDENCE"
            report["stopped_reason"] = "v4_1_scheduler_verification_failed"
        report["report_version"] = "klax-v4.1-offline-campaign-report-v1"
        report["source_campaign_id"] = SOURCE_CAMPAIGN_ID
        report["continuation_import_sha256"] = canonical_hash(
            self.continuation_record)
        report["scheduler_verification_v4_1"] = verification["status"]
        write_json(scheduler_path, scheduler)
        write_json(staging_root / "search-coverage.json", scheduler)
        write_json(staging_root / "scheduler-verification.json", verification)
        write_json(summary_path, report)
        (staging_root / "report.md").write_text(
            _render_report_markdown_v4(report), encoding="utf-8")
        self.phase = "COMPLETE"
        self.save_recovery()
        for name in TERMINAL_ARTIFACT_NAMES:
            source = staging_root / name
            if not source.is_file():
                raise V3IntegrityStop(
                    f"V4.1 staged terminal artifact is missing: {name}")
            _atomic_publish_file(source, output_root / name)
        inventory = []
        for path in sorted(output_root.rglob("*")):
            if (path.is_file()
                    and path.name not in {"campaign-artifacts.json",
                                          ".campaign-execution.lock",
                                          "summary.json"}
                    and not path.name.endswith(".v4-1-pending")):
                inventory.append({
                    "path": path.relative_to(output_root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                })
        inventory.append({
            "path": "summary.json",
            "bytes": summary_path.stat().st_size,
            "sha256": sha256_file(summary_path),
        })
        inventory.sort(key=lambda row: row["path"])
        manifest_body = {
            "manifest_version": "klax-v4.1-campaign-artifacts-v1",
            "campaign_id": report["campaign_id"],
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "continuation_import_sha256": canonical_hash(
                self.continuation_record),
            "artifacts": inventory,
            "scheduler_verification_status": verification["status"],
            "scientific_conclusion": report["scientific_conclusion"],
            "protected_final_read": False,
            "actual_orders_placed": False,
        }
        staged_manifest = staging_root / "campaign-artifacts.json"
        write_json(staged_manifest, {
            **manifest_body,
            "manifest_sha256": canonical_hash(manifest_body),
        })
        _atomic_publish_file(
            staged_manifest, output_root / "campaign-artifacts.json")
        # summary.json is the commit marker and is deliberately exposed last.
        _atomic_publish_file(summary_path, output_root / "summary.json")
        if not _valid_terminal_commit_v4_1(output_root):
            raise V3IntegrityStop("V4.1 terminal commit failed validation")
        shutil.rmtree(staging_root)
        return {**result, **report, "path": str(output_root)}

    @classmethod
    def recover_v4_1(
        cls, *, engine: V4_1CampaignEngine,
        primary_evaluator: OfflineCandidateEvaluatorV4,
        replication_evaluator: OfflineCandidateEvaluatorV4,
        output_root: Path, continuation_record: dict[str, Any],
        worker_factory: Any,
    ) -> "ProductionV4_1Orchestrator":
        value = json.loads(
            (Path(output_root) / "recovery-state.json").read_text(encoding="utf-8"))
        body = {key: item for key, item in value.items() if key != "state_sha256"}
        if (value.get("state_sha256") != canonical_hash(body)
                or value.get("recovery_version") != RECOVERY_VERSION_V4_1
                or value.get("orchestrator_version") != RUNNER_VERSION_V4_1
                or value.get("campaign_id") != engine.authorization.campaign_id
                or value.get("source_campaign_id") != SOURCE_CAMPAIGN_ID
                or value.get("continuation_import_sha256")
                != canonical_hash(continuation_record)
                or value.get("readiness_sha256")
                != engine.authorization.readiness_sha256
                or value.get("ticket_sha256") != engine.authorization.ticket_sha256
                or value.get("started_at_utc") != SOURCE_STARTED_AT_UTC
                or value.get("absolute_deadline_at_utc")
                != ABSOLUTE_DEADLINE_AT_UTC
                or value.get("protected_final_evaluated") is not False
                or value.get("actual_orders_placed") is not False):
            raise V3IntegrityStop("V4.1 recovery state is modified or differently bound")
        state = value.get("engine")
        if not isinstance(state, dict):
            raise V3IntegrityStop("V4.1 recovery engine state is missing")
        elapsed = max(
            int(state.get("elapsed_seconds", -1)), engine.cumulative_wall_seconds())
        if elapsed < CONSUMED_WALL_SECONDS:
            raise V3IntegrityStop("V4.1 recovery refunded wall time")
        engine.started_at = engine.clock() - elapsed
        for name in ("current_epoch", "model_calls", "model_context_tokens_reserved",
                     "admitted_candidates", "executed_candidates", "empty_epochs"):
            raw = state.get(name)
            if type(raw) is not int or raw < 0:
                raise V3IntegrityStop("V4.1 recovery counter is invalid")
            setattr(engine, name, raw)
        if (engine.model_calls < CONSUMED_MODEL_CALLS
                or engine.model_context_tokens_reserved
                < CONSUMED_CONTEXT_TOKENS
                or engine.model_context_tokens_reserved
                != engine.model_calls * CONTEXT_TOKENS_PER_MODEL_CALL
                or engine.admitted_candidates < CONSUMED_CANDIDATES
                or engine.executed_candidates < CONSUMED_CANDIDATES
                or engine.current_epoch < CURRENT_EPOCH):
            raise V3IntegrityStop("V4.1 recovery refunded a cumulative counter")
        engine.stopped_reason = state.get("stopped_reason")
        engine.champion_candidate_id = state.get("champion_candidate_id")
        engine.final_authorization = state.get("final_authorization")
        engine.novelty_index = dict(state.get("novelty_index", {}))
        engine.duplicate_proposals = list(state.get("duplicate_proposals", []))
        engine.nonproposal_responses = list(state.get("nonproposal_responses", []))
        engine.epoch_admissions = {
            int(key): int(item) for key, item in state.get(
                "epoch_admissions", {}).items()}
        engine.transient_retries = dict(state.get("transient_retries", {}))
        engine.candidates = {}
        for candidate_id, raw in state.get("candidates", {}).items():
            plan = ResearchPlanV4.from_dict(raw["plan"])
            if candidate_id != "v4-candidate-" + plan.identity[:20]:
                raise V3IntegrityStop("V4.1 recovery candidate identity differs")
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
                    row.evaluation is not None for row in engine.candidates.values())):
            raise V3IntegrityStop("V4.1 recovery candidate accounting differs")
        return cls(
            engine, primary_evaluator, replication_evaluator, Path(output_root),
            worker_factory=worker_factory, phase=value["phase"],
            queue=list(value["queue"]), next_queue_index=value["next_queue_index"],
            coverage=dict(value["coverage"]),
            started_at_utc=SOURCE_STARTED_AT_UTC,
            completed_at_utc=value.get("completed_at_utc"),
            continuation_record=continuation_record,
            source_output_root=(engine.authorization.root / SOURCE_ROOT),
            absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
        )


def _load_authorization_v4_1(
    root: Path, readiness_path: Path, ticket_path: Path, *, resume: bool,
) -> Any:
    try:
        from .readiness_v4_1 import load_v4_1_campaign_authorization
    except ImportError as exc:
        raise V3ReadinessRefusal("V4.1 readiness loader is unavailable") from exc
    wrapper = load_v4_1_campaign_authorization(
        root, readiness_path, ticket_path, allow_existing_claim=resume)
    required = {
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "original_started_at_utc": SOURCE_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
    }
    if any(getattr(wrapper, key, None) != value for key, value in required.items()):
        raise V3ReadinessRefusal("V4.1 continuation authorization differs")
    authorization = getattr(wrapper, "authorization", None)
    if authorization is None:
        raise V3ReadinessRefusal("V4.1 underlying campaign authorization is missing")
    return wrapper


def _paths(root: Path, campaign_id: str) -> Path:
    return (root / OUTPUT_PARENT_V4_1 / campaign_id).resolve()


def _build_runtime_v4_1(
    root: Path, readiness_path: Path, ticket_path: Path, *, resume: bool,
) -> ProductionV4_1Orchestrator:
    wrapper = _load_authorization_v4_1(
        root, readiness_path, ticket_path, resume=resume)
    authorization = wrapper.authorization
    continuation_record = _load_import_manifest(root)
    if getattr(wrapper, "source_recovery_state_sha256", None) != (
            continuation_record["source_recovery_state_sha256"]):
        raise V3ReadinessRefusal("V4.1 authorization checkpoint differs")
    output = _paths(root, authorization.campaign_id)
    primary, replication = _production_evaluators_v4(authorization, output)
    workers = _v4_1_worker_factory(authorization, output)
    engine = V4_1CampaignEngine(
        authorization,
        absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
        claim_ticket=not resume)
    if resume:
        return ProductionV4_1Orchestrator.recover_v4_1(
            engine=engine, primary_evaluator=primary,
            replication_evaluator=replication, output_root=output,
            continuation_record=continuation_record, worker_factory=workers)
    source, rebuilt_record = _reconciled_source_state(root)
    if rebuilt_record != continuation_record:
        raise V3ReadinessRefusal("V4.1 source changed after readiness")
    _hydrate_engine_from_source(engine, source)
    coverage = deepcopy(source["coverage"])
    candidate_calls = sum(
        row.get("call_type") == "candidate" for row in coverage["call_journal"])
    synthesis_calls = sum(
        row.get("call_type") == "synthesis" for row in coverage["call_journal"])
    retry_calls = sum(
        row.get("call_type") == "retry" for row in coverage["call_journal"])
    coverage["resume_history"].append({
        "resumed_at_utc": _iso(_utc_now()),
        "candidate_calls_used": candidate_calls,
        "synthesis_calls_used": synthesis_calls,
        "transient_retry_calls_used": retry_calls,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "continuation_version": "4.1",
    })
    runner = ProductionV4_1Orchestrator(
        engine, primary, replication, output, worker_factory=workers,
        phase=source["phase"], queue=deepcopy(source["queue"]),
        next_queue_index=source["next_queue_index"], coverage=coverage,
        started_at_utc=SOURCE_STARTED_AT_UTC,
        completed_at_utc=None, continuation_record=continuation_record,
        source_output_root=root / SOURCE_ROOT,
        absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC)
    runner.save_recovery()
    return runner


def start_v4_1_campaign(
    root: Path | str, readiness_path: Path | str, ticket_path: Path | str,
) -> dict[str, Any]:
    root = Path(root).resolve()
    runner = _build_runtime_v4_1(
        root, Path(readiness_path), Path(ticket_path), resume=False)
    if (runner.output_root / "summary.json").exists():
        label = ("already completed" if _valid_terminal_commit_v4_1(
            runner.output_root) else "has an invalid partial terminal commit")
        raise V3ReadinessRefusal(f"V4.1 campaign {label}")
    with _CampaignExecutionLease(runner.output_root):
        runner.apply_incident_fallback_v4_1()
        return runner.run()


def resume_v4_1_campaign(
    root: Path | str, readiness_path: Path | str, ticket_path: Path | str,
) -> dict[str, Any]:
    root = Path(root).resolve()
    runner = _build_runtime_v4_1(
        root, Path(readiness_path), Path(ticket_path), resume=True)
    if (runner.output_root / "summary.json").exists():
        label = ("is already complete" if _valid_terminal_commit_v4_1(
            runner.output_root) else "has an invalid partial terminal commit")
        raise V3ReadinessRefusal(f"V4.1 campaign {label}")
    with _CampaignExecutionLease(runner.output_root):
        runner.coverage["resume_history"].append({
            "resumed_at_utc": _iso(_utc_now()),
            "candidate_calls_used": sum(
                row.get("call_type") == "candidate"
                for row in runner.coverage["call_journal"]),
            "synthesis_calls_used": sum(
                row.get("call_type") == "synthesis"
                for row in runner.coverage["call_journal"]),
            "transient_retry_calls_used": sum(
                row.get("call_type") == "retry"
                for row in runner.coverage["call_journal"]),
            "source_campaign_id": SOURCE_CAMPAIGN_ID,
            "continuation_version": "4.1",
        })
        runner.apply_incident_fallback_v4_1()
        runner.save_recovery()
        return runner.run()


def campaign_status_v4_1(
    root: Path | str, ticket_path: Path | str,
) -> dict[str, Any]:
    root = Path(root).resolve()
    ticket_file = Path(ticket_path)
    ticket_file = (ticket_file.resolve() if ticket_file.is_absolute()
                   else (root / ticket_file).resolve())
    ticket = json.loads(ticket_file.read_text(encoding="utf-8"))
    campaign_id = ticket.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id.startswith(
            "v4-1-offline-"):
        raise V3ReadinessRefusal("V4.1 ticket campaign identity is missing")
    output = _paths(root, campaign_id)
    summary = output / "summary.json"
    recovery = output / "recovery-state.json"
    terminal = summary.is_file() and _valid_terminal_commit_v4_1(output)
    return {
        "campaign_id": campaign_id,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "status": ("COMPLETE" if terminal else
                   "INCOMPLETE_EXPLICIT_RESUME_REQUIRED" if recovery.is_file()
                   else "TICKET_READY_NOT_STARTED"),
        "summary_path": str(summary),
        "recovery_path": str(recovery),
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }


def _future_fallback_behavior_self_test(root: Path) -> dict[str, bool]:
    """Exercise candidate fallback and negative boundaries through production methods."""
    source = json.loads((root / SOURCE_RECOVERY_PATH).read_text(encoding="utf-8"))
    plan = ResearchPlanV4.from_dict(source["queue"][11]["plan"])
    digest_sha256 = source["coverage"]["digests"][-1]["digest_sha256"]

    class EngineFixture:
        def __init__(self, *, epoch: int = CURRENT_EPOCH) -> None:
            self.authorization = SimpleNamespace(
                root=root, campaign_id="v4-1-offline-self-test",
                readiness_sha256="1" * 64, config_sha256="2" * 64,
                schema_sha256="3" * 64,
                partition_contract_sha256="4" * 64,
                data_bundle_version="self-test-bundle",
                data_bundle_sha256="5" * 64)
            self.budget = SimpleNamespace(
                maximum_transient_retries_per_task=2)
            self.current_epoch = epoch
            self.model_calls = CONSUMED_MODEL_CALLS
            self.model_context_tokens_reserved = CONSUMED_CONTEXT_TOKENS
            self.transient_retries: dict[str, int] = {}
            self.admissions = 0

        @contextmanager
        def model_call(self, _worker_id: str):
            self.model_calls += 1
            self.model_context_tokens_reserved += CONTEXT_TOKENS_PER_MODEL_CALL
            yield

        def register_transient_failure(self, task_id: str) -> None:
            self.transient_retries[task_id] = (
                self.transient_retries.get(task_id, 0) + 1)

        def admit_registered_plan_v4(
            self, admitted: ResearchPlanV4, **_kwargs: Any,
        ) -> dict[str, Any]:
            self.admissions += 1
            return {
                "status": "ADMITTED",
                "candidate_id": "v4-candidate-" + admitted.identity[:20],
            }

        def budget_remaining(self) -> dict[str, int]:
            return {
                "epoch": self.current_epoch,
                "epochs_remaining": 249,
                "candidate_slots_remaining": 2_989,
                "epoch_candidate_slots_remaining": 1,
                "model_calls_remaining": 3_300 - self.model_calls,
                "reserved_context_tokens_remaining": (
                    54_067_200 - self.model_context_tokens_reserved),
                "paid_api_dollars_remaining": 0,
                "wall_seconds_remaining": 1,
            }

    def runner_fixture(output: Path, engine: EngineFixture):
        runner = object.__new__(ProductionV4_1Orchestrator)
        runner.engine = engine
        runner.output_root = output
        runner.next_queue_index = 0
        runner.coverage = {
            "colonies": {plan.colony: {
                "proposed": 0, "admitted": 0, "executed": 0,
                "rejected": 0, "champions": 0}},
            "digests": [{"digest_sha256": digest_sha256}],
            "call_journal": [],
            "allocation_history": [{"allocations": [{
                "task_id": "v4-self-test-fallback",
                "plan_sha256": plan.identity,
                "candidate_id": None}], "completed": False}],
            "v4_1_model_nomination_failures": [],
        }
        packet = {
            "mode": "candidate_nomination",
            "task_id": "v4-self-test-fallback",
            "digest_sha256": digest_sha256,
            "seed_plans": [plan.to_dict()],
        }
        runner._packet_with_digest = lambda _item: deepcopy(packet)
        runner.save_recovery = lambda: {}
        runner._save_task = lambda *_args: None
        runner._execute_and_review = lambda item: item.update(status="REVIEWED")
        return runner, packet

    class ParserFailureWorker:
        def __init__(self, worker_id: str, output: Path) -> None:
            self.worker_id = worker_id
            self.output = output
            self.calls = 0

        def respond(self, packet: Mapping[str, Any]) -> dict[str, Any]:
            self.calls += 1
            attempt = (self.output / "local-inference-v4-1"
                       / str(packet["task_id"]) / f"attempt-{self.calls}")
            attempt.mkdir(parents=True)
            write_json(attempt / "packet.json", dict(packet))
            (attempt / "stdout.bin").write_bytes(
                f"malformed-{self.calls}".encode("ascii"))
            process = {
                "exit_code": 0, "timed_out": False,
                "output_limit_exceeded": False, "cleanup_failed": False,
                "reader_errors": [], "elapsed_seconds": 0.01,
            }
            write_json(attempt / "process.json", process)
            audit = {
                "error_version": "klax-v4.1-candidate-response-error-v1",
                "task_id": packet["task_id"],
                "packet_sha256": canonical_hash(packet),
                "attempt_directory": attempt.relative_to(root).as_posix(),
                "raw_stdout_sha256": sha256_file(attempt / "stdout.bin"),
                "completion_body_sha256": None,
                "process_sha256": sha256_file(attempt / "process.json"),
                "error_class": "V4WorkerProtocolError",
                "error_message": "synthetic malformed response",
                "model_response_accepted": False,
                "scientific_evidence_created": False,
                "protected_final_read": False,
            }
            write_json(attempt / "response-error.json", audit)
            raise V4_1CandidateResponseProtocolError(
                "synthetic malformed response", audit)

    class RuntimeFailureWorker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id
            self.calls = 0

        def respond(self, _packet: Mapping[str, Any]) -> dict[str, Any]:
            self.calls += 1
            raise OSError("synthetic runtime failure")

    checks: dict[str, bool] = {}
    parent = root / OUTPUT_PARENT_V4_1
    parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".v4-1-self-test-", dir=parent) as raw:
        output = Path(raw)
        engine = EngineFixture()
        runner, _packet = runner_fixture(output, engine)
        box: dict[str, ParserFailureWorker] = {}

        def parser_factory(worker_id: str, _role: str):
            worker = ParserFailureWorker(worker_id, output)
            box["worker"] = worker
            return worker

        runner.worker_factory = parser_factory
        item = {
            "task_id": "v4-self-test-fallback", "role": "critic",
            "plan": plan.to_dict(), "status": "PENDING", "candidate_id": None,
        }
        runner._process_item(item)
        failure = runner.coverage["v4_1_model_nomination_failures"][-1]
        checks["future_candidate_parser_exhaustion_zero_call_fallback"] = (
            item["status"] == "REVIEWED" and engine.admissions == 1
            and failure["fallback"]["model_calls_charged"] == 0)
        checks["future_candidate_parser_exhaustion_no_fourth_call"] = (
            box["worker"].calls == 3
            and engine.model_calls == CONSUMED_MODEL_CALLS + 3)

    with TemporaryDirectory(prefix=".v4-1-self-test-", dir=parent) as raw:
        output = Path(raw)
        engine = EngineFixture()
        runner, _packet = runner_fixture(output, engine)
        box: dict[str, RuntimeFailureWorker] = {}

        def runtime_factory(worker_id: str, _role: str):
            worker = RuntimeFailureWorker(worker_id)
            box["worker"] = worker
            return worker

        runner.worker_factory = runtime_factory
        item = {
            "task_id": "v4-self-test-fallback", "role": "critic",
            "plan": plan.to_dict(), "status": "PENDING", "candidate_id": None,
        }
        try:
            runner._process_item(item)
        except OSError:
            runtime_stopped = True
        else:
            runtime_stopped = False
        checks["runtime_failure_remains_hard_stop"] = (
            runtime_stopped and box["worker"].calls == 3
            and engine.admissions == 0)

    with TemporaryDirectory(prefix=".v4-1-self-test-", dir=parent) as raw:
        output = Path(raw)
        engine = EngineFixture(epoch=4)
        runner, _packet = runner_fixture(output, engine)
        runner.queue = [{"status": "REVIEWED"}]
        runner.coverage["allocation_history"] = [{
            "completed": False, "synthesis_artifact": None,
            "allocations": []}]
        runner.coverage["synthesis_artifacts"] = []
        synthesis = RuntimeFailureWorker("synthesizer-cross-pollination-e004")
        runner.worker_factory = lambda *_args: synthesis
        try:
            ProductionV4Orchestrator._synthesize_if_due(runner)
        except OSError:
            synthesis_stopped = True
        else:
            synthesis_stopped = False
        checks["synthesis_failure_remains_hard_stop"] = (
            synthesis_stopped and synthesis.calls == 3)
    return checks


def run_v4_1_production_integration_self_test(root: Path | str) -> dict[str, Any]:
    """Exercise the immutable import, reconciliation, and deadline floors."""
    root = Path(root).resolve()
    record = build_continuation_import_record_v4_1(root)
    reconciliation = reconcile_failed_nomination_v4_1(root, record)
    source = json.loads((root / SOURCE_RECOVERY_PATH).read_text(encoding="utf-8"))
    original_sha = sha256_file(root / SOURCE_RECOVERY_PATH)
    checks = {
        "source_campaign_hash_unchanged": original_sha
        == record["source_records"]["recovery"]["sha256"],
        "eighty_two_candidates_imported": len(record["candidate_imports"])
        == CONSUMED_CANDIDATES,
        "call_86_reconciled_failed": record["incident"]["status"]
        == "MODEL_NOMINATION_FAILED",
        "zero_call_exact_plan_fallback": (
            reconciliation["model_calls_before"] == CONSUMED_MODEL_CALLS
            and reconciliation["model_calls_charged"] == 0
            and reconciliation["model_calls_after"] == CONSUMED_MODEL_CALLS
            and reconciliation["plan_sha256"] == FAILED_PLAN_SHA256),
        "original_absolute_deadline_preserved": (
            record["absolute_deadline_at_utc"] == ABSOLUTE_DEADLINE_AT_UTC
            and record["original_started_at_utc"] == SOURCE_STARTED_AT_UTC),
        "cumulative_budget_floors_preserved": (
            record["cumulative_budget"]["consumed_candidates"]
            == CONSUMED_CANDIDATES
            and record["cumulative_budget"]["consumed_model_calls"]
            == CONSUMED_MODEL_CALLS
            and record["cumulative_budget"]["consumed_context_tokens"]
            == CONSUMED_CONTEXT_TOKENS
            and record["cumulative_budget"]["consumed_wall_seconds_floor"]
            == CONSUMED_WALL_SECONDS),
        "source_safety_preserved": (
            source["coverage"]["protected_final_read"] is False
            and source["coverage"]["actual_orders_placed"] is False),
    }
    checks.update(_future_fallback_behavior_self_test(root))
    body = {
        "self_test_version": "klax-v4.1-production-integration-self-test-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "continuation_record_sha256": canonical_hash(record),
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "self_test_sha256": canonical_hash(body)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "resume", "status"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--readiness", type=Path)
    parser.add_argument("--ticket", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "status":
        result = campaign_status_v4_1(args.root, args.ticket)
    else:
        if args.readiness is None:
            parser.error("start/resume require --readiness")
        action = start_v4_1_campaign if args.command == "start" else resume_v4_1_campaign
        result = action(args.root, args.readiness, args.ticket)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
