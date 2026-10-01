"""Immutable forensic import and incident reconciliation for V4.1.

This module never mutates the failed V4 source campaign.  It reconstructs a
JSON-native, hash-bound continuation record from the exact source checkpoint,
reconciles the final persisted RESERVED call with its completed process
artifact, and authorizes one zero-call host fallback for the exact preallocated
plan whose three charged model nominations all failed the V4 parser.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from klax_lab.provenance import canonical_hash, sha256_file

from .local_worker_v4 import V4WorkerProtocolError, parse_v4_worker_response
from .local_worker_v4_1 import (
    NORMALIZATION_RULE, REPAIR_VERSION, extract_archived_completion_body_v4_1,
    normalize_singleton_nonproposal_v4_1, parse_v4_1_worker_response,
)
from .research_plan_v4 import ResearchPlanV4
from .verifier_v4 import verify_candidate_v4


CONTINUATION_RECORD_VERSION = "klax-v4.1-continuation-import-record-v1"
INCIDENT_VERSION = "klax-v4.1-model-nomination-incident-v1"
SOURCE_CAMPAIGN_ID = "v4-offline-20260926T212122609Z"
SOURCE_ROOT = Path("runs/campaigns_v4") / SOURCE_CAMPAIGN_ID
SOURCE_READINESS_PATH = Path("data/manifests/v4_readiness.json")
SOURCE_TICKET_PATH = Path("runs/v4_offline_campaign_ticket.json")
SOURCE_CLAIM_PATH = Path("runs/v4_offline_campaign_ticket.json.claimed.json")
SOURCE_STDERR_PATH = Path("runs/v4_campaign.stderr.log")
SOURCE_PROCESS_PATH = Path("runs/v4_campaign.process.json")
SOURCE_RECOVERY_PATH = SOURCE_ROOT / "recovery-state.json"
SOURCE_READINESS_SHA256 = (
    "d8c2410517c4931db413e1a7b2fee80f284fb0b678101fd16e1c247b031fa5ad")
SOURCE_TICKET_SHA256 = (
    "df259ed5d11ca6846b9f9a6dd765f0594f734c879ec4dd619268931f0f4d1cb5")
SOURCE_CLAIM_SHA256 = (
    "4a68c462dbdb16225aa3f7ecef72a0c5921607523ef87af7d8d7306bf2d1603c")
SOURCE_RECOVERY_FILE_SHA256 = (
    "d631a281a9004c1ded5fb3ac7b504a16224307492ea276d0dcc516efd41f89d0")
SOURCE_RECOVERY_STATE_SHA256 = (
    "b4e1576fbb9ba5822182f877239e8657636c3f49e07f8e4b8005185ee2fa987b")
SOURCE_STDERR_SHA256 = (
    "d39743ed7793f8e9d9e678541fb0096f128c192c288c45ce0720da9f0092747f")
SOURCE_PROCESS_SHA256 = (
    "5c031a53b6bf19a3e8cc66a9079c8ecf28a747c8fecad0894c6ba0a01bad7a47")
SOURCE_STARTED_AT_UTC = "2026-09-26T21:23:25.261434+00:00"
ABSOLUTE_DEADLINE_AT_UTC = "2026-09-27T09:23:25.261434+00:00"
FAILED_TASK_ID = "v4-e007-s11"
FAILED_PLAN_SHA256 = (
    "568bf0bb0164850f246724f70f4df899848435dff2ebfa782ce114df5733b744")
FAILED_STDOUT_SHA256 = (
    "d3ecbc80e9887626fb366e25c6f94daae7173e0921ae6f80d1e129e08e44a9b5")
RECONCILED_CALL_86_COMPLETED_AT_UTC = "2026-09-26T21:51:14.126716+00:00"

MAXIMUM_CANDIDATES = 3_072
MAXIMUM_MODEL_CALLS = 3_300
MAXIMUM_CONTEXT_TOKENS = 54_067_200
MAXIMUM_EPOCHS = 256
MAXIMUM_WALL_SECONDS = 43_200
CONTEXT_TOKENS_PER_CALL = 16_384
CONSUMED_CANDIDATES = 82
CONSUMED_MODEL_CALLS = 86
CONSUMED_CONTEXT_TOKENS = 1_409_024
CONSUMED_WALL_SECONDS = 1_669
CONSUMED_RETRIES = 2
CURRENT_EPOCH = 7
COMPLETED_EPOCHS = 6
CURRENT_EPOCH_COMPLETED_CANDIDATES = 10


class V4_1ContinuationError(ValueError):
    """The immutable V4 source cannot support a V4.1 continuation."""


def _path(root: Path, relative: Path) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise V4_1ContinuationError("Continuation path escapes project root") from exc
    return target


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V4_1ContinuationError(f"Invalid {label}: {path}") from exc


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise V4_1ContinuationError(f"Required source artifact is missing: {path}")
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise V4_1ContinuationError("Source artifact escapes project root") from exc
    return {"path": relative, "bytes": path.stat().st_size,
            "sha256": sha256_file(path)}


def _require_sha(record: Mapping[str, Any], expected: str, label: str) -> None:
    if record.get("sha256") != expected:
        raise V4_1ContinuationError(f"{label} SHA-256 differs")


def _source_artifact_inventory(root: Path, source_root: Path) -> list[dict[str, Any]]:
    records = [_file_record(root, path) for path in sorted(source_root.rglob("*"))
               if path.is_file()]
    if not records:
        raise V4_1ContinuationError("V4 source artifact inventory is empty")
    return records


def _candidate_imports(
    root: Path, source_root: Path, state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    engine = state["engine"]
    candidates = engine.get("candidates")
    if not isinstance(candidates, dict) or len(candidates) != CONSUMED_CANDIDATES:
        raise V4_1ContinuationError("V4 source candidate count differs")
    imported: list[dict[str, Any]] = []
    for candidate_id, raw in sorted(candidates.items()):
        if not isinstance(raw, dict):
            raise V4_1ContinuationError("Malformed V4 source candidate")
        plan = ResearchPlanV4.from_dict(raw["plan"])
        if candidate_id != "v4-candidate-" + plan.identity[:20]:
            raise V4_1ContinuationError("V4 source candidate identity differs")
        if raw.get("promotion") is None or raw.get("evaluation") is None:
            raise V4_1ContinuationError("Imported V4 candidate is incomplete")
        primary_dir = source_root / "candidates" / "primary" / plan.identity
        replication_dir = source_root / "candidates" / "replication" / plan.identity
        review_dir = source_root / "reviews" / candidate_id
        primary = [_file_record(root, path) for path in sorted(primary_dir.glob("*.json"))]
        replication = [
            _file_record(root, path) for path in sorted(replication_dir.glob("*.json"))]
        reviews = [_file_record(root, path) for path in sorted(review_dir.glob("*.json"))]
        if len(primary) != 5 or len(replication) != 1 or len(reviews) != 5:
            raise V4_1ContinuationError("Imported V4 candidate artifact set differs")
        by_name = {Path(row["path"]).name: row["sha256"] for row in primary}
        expected_artifacts = raw.get("artifact_sha256s")
        if not isinstance(expected_artifacts, dict) or any(
                by_name.get(name) != digest for name, digest in {
                    "compiled_manifest.json": expected_artifacts.get(
                        "compiled_manifest_sha256"),
                    "evaluation.json": expected_artifacts.get("evaluation_sha256"),
                    "fold_metrics.json": expected_artifacts.get("fold_metrics_sha256"),
                    "ledger.json": expected_artifacts.get("ledger_sha256"),
                    "predictions.json": expected_artifacts.get("predictions_sha256"),
                }.items()):
            raise V4_1ContinuationError("Imported primary artifact hash differs")
        saved_promotion = _read_json(review_dir / "promotion.json", "promotion review")
        if saved_promotion != raw.get("promotion"):
            raise V4_1ContinuationError("Imported promotion record differs")
        independent = _read_json(
            review_dir / "independent-v4-verification.json",
            "independent V4 verification")
        imported.append({
            "candidate_id": candidate_id,
            "research_plan_sha256": plan.identity,
            "novelty_fingerprint": plan.novelty_fingerprint,
            "epoch": raw.get("epoch"),
            "discovery_worker_id": raw.get("discovery_worker_id"),
            "evaluation_sha256": canonical_hash(raw["evaluation"]),
            "promotion_sha256": canonical_hash(raw["promotion"]),
            "replication_sha256": canonical_hash(raw.get("replication")),
            "critic_sha256": canonical_hash(raw.get("critic")),
            "independent_verifier_status": independent.get("status"),
            "independent_promotion_eligible": independent.get(
                "promotion_eligible"),
            "primary_artifacts": primary,
            "replication_artifacts": replication,
            "review_artifacts": reviews,
        })
    identities = [row["research_plan_sha256"] for row in imported]
    if len(set(identities)) != CONSUMED_CANDIDATES:
        raise V4_1ContinuationError("Imported V4 plan identities are not unique")
    return imported


def _incident_record(
    root: Path, source_root: Path, state: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    queue = state.get("queue")
    if not isinstance(queue, list) or len(queue) != 12:
        raise V4_1ContinuationError("Source epoch-seven queue differs")
    item = next((row for row in queue if row.get("task_id") == FAILED_TASK_ID), None)
    if not isinstance(item, dict) or item.get("status") != "PENDING":
        raise V4_1ContinuationError("Failed nomination task is not pending")
    plan = ResearchPlanV4.from_dict(item["plan"])
    if plan.identity != FAILED_PLAN_SHA256:
        raise V4_1ContinuationError("Failed nomination plan identity differs")
    attempts: list[dict[str, Any]] = []
    raw_values: list[dict[str, Any]] = []
    for attempt in range(1, 4):
        directory = (source_root / "local-inference-v4" / FAILED_TASK_ID
                     / f"attempt-{attempt}")
        required = ("packet.json", "prompt.txt", "response-schema.json", "stdout.bin",
                    "stderr.bin", "process.json")
        files = [_file_record(root, directory / name) for name in required]
        if (directory / "proposal.json").exists():
            raise V4_1ContinuationError("Failed V4 attempt unexpectedly has a proposal")
        stdout = (directory / "stdout.bin").read_bytes()
        if hashlib.sha256(stdout).hexdigest() != FAILED_STDOUT_SHA256:
            raise V4_1ContinuationError("Failed V4 raw response differs")
        process = _read_json(directory / "process.json", "V4 process record")
        if (process.get("exit_code") != 0 or process.get("timed_out") is not False
                or process.get("output_limit_exceeded") is not False
                or process.get("cleanup_failed") is not False
                or process.get("reader_errors") != []):
            raise V4_1ContinuationError("Failed V4 attempt violated process bounds")
        packet = _read_json(directory / "packet.json", "V4 incident packet")
        body = extract_archived_completion_body_v4_1(stdout)
        raw_value = json.loads(body.decode("utf-8"))
        try:
            parse_v4_worker_response(body, packet)
        except V4WorkerProtocolError as exc:
            frozen_error = str(exc)
        else:
            raise V4_1ContinuationError("Frozen V4 parser unexpectedly accepts incident")
        normalized, normalization = normalize_singleton_nonproposal_v4_1(
            raw_value, packet)
        parsed = parse_v4_1_worker_response(body, packet)
        if (parsed != normalized or parsed.get("action") != "abstain"
                or parsed.get("seed_index") is not None
                or normalization is None):
            raise V4_1ContinuationError("V4.1 incident normalization differs")
        raw_values.append(raw_value)
        attempts.append({
            "attempt": attempt,
            "model_call_number": 83 + attempt,
            "files": files,
            "process_elapsed_seconds": process["elapsed_seconds"],
            "process_status": "COMPLETED_WITH_SCHEMA_INVALID_OUTPUT",
            "frozen_parser_error": frozen_error,
            "raw_response_sha256": canonical_hash(raw_value),
            "normalization": normalization,
            "normalized_response_sha256": canonical_hash(parsed),
        })
    if any(value != raw_values[0] for value in raw_values[1:]):
        raise V4_1ContinuationError("Incident responses are not byte-semantically equal")
    journal = deepcopy(state["coverage"]["call_journal"])
    if len(journal) != CONSUMED_MODEL_CALLS:
        raise V4_1ContinuationError("Source call journal length differs")
    last = journal[-1]
    if (last.get("model_call_number") != 86 or last.get("task_id") != FAILED_TASK_ID
            or last.get("status") != "RESERVED"):
        raise V4_1ContinuationError("Source call 86 is not the expected checkpoint")
    last["status"] = "FAILED"
    last["completed_at_utc"] = RECONCILED_CALL_86_COMPLETED_AT_UTC
    last["failure_class"] = "MODEL_NOMINATION_FAILED"
    last["failure_reason"] = "schema_invalid_singleton_nonproposal"
    incident_body = {
        "incident_version": INCIDENT_VERSION,
        "status": "MODEL_NOMINATION_FAILED",
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "task_id": FAILED_TASK_ID,
        "plan_sha256": FAILED_PLAN_SHA256,
        "attempts_charged": 3,
        "model_call_numbers": [84, 85, 86],
        "attempts": attempts,
        "raw_responses_identical": True,
        "raw_action": "abstain",
        "raw_seed_index": 0,
        "repair_version": REPAIR_VERSION,
        "normalization_rule": NORMALIZATION_RULE,
        "model_nomination_succeeded": False,
        "scientific_evidence_created": False,
        "reconciled_call_journal_sha256": canonical_hash(journal),
        "fallback": {
            "disposition": "HOST_EXACT_PLAN_FALLBACK",
            "authorized_plan_sha256": FAILED_PLAN_SHA256,
            "authorization_basis": (
                "exact preallocated singleton after primary plus two charged "
                "schema-invalid nonproposal retries"),
            "model_calls_charged": 0,
            "model_response_fabricated": False,
            "candidate_result_claimed": False,
        },
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**incident_body, "incident_sha256": canonical_hash(incident_body)}, journal


def audit_source_campaign_v4_1(root: Path | str) -> dict[str, Any]:
    """Fail-closed read-only audit of the exact failed V4 campaign."""
    root = Path(root).resolve()
    source_root = _path(root, SOURCE_ROOT)
    records = {
        "readiness": _file_record(root, _path(root, SOURCE_READINESS_PATH)),
        "ticket": _file_record(root, _path(root, SOURCE_TICKET_PATH)),
        "claim": _file_record(root, _path(root, SOURCE_CLAIM_PATH)),
        "recovery": _file_record(root, _path(root, SOURCE_RECOVERY_PATH)),
        "stderr": _file_record(root, _path(root, SOURCE_STDERR_PATH)),
        "controller_process": _file_record(
            root, _path(root, SOURCE_PROCESS_PATH)),
    }
    for label, expected in {
        "readiness": SOURCE_READINESS_SHA256,
        "ticket": SOURCE_TICKET_SHA256,
        "claim": SOURCE_CLAIM_SHA256,
        "recovery": SOURCE_RECOVERY_FILE_SHA256,
        "stderr": SOURCE_STDERR_SHA256,
        "controller_process": SOURCE_PROCESS_SHA256,
    }.items():
        _require_sha(records[label], expected, f"source {label}")
    state = _read_json(_path(root, SOURCE_RECOVERY_PATH), "V4 recovery state")
    state_body = {key: value for key, value in state.items() if key != "state_sha256"}
    if (state.get("state_sha256") != SOURCE_RECOVERY_STATE_SHA256
            or canonical_hash(state_body) != SOURCE_RECOVERY_STATE_SHA256):
        raise V4_1ContinuationError("V4 recovery state hash differs")
    engine, coverage = state.get("engine"), state.get("coverage")
    if not isinstance(engine, dict) or not isinstance(coverage, dict):
        raise V4_1ContinuationError("V4 recovery state is incomplete")
    expected_counters = {
        "current_epoch": CURRENT_EPOCH,
        "model_calls": CONSUMED_MODEL_CALLS,
        "model_context_tokens_reserved": CONSUMED_CONTEXT_TOKENS,
        "admitted_candidates": CONSUMED_CANDIDATES,
        "executed_candidates": CONSUMED_CANDIDATES,
    }
    if any(engine.get(key) != value for key, value in expected_counters.items()):
        raise V4_1ContinuationError("V4 consumed counter differs")
    if (engine.get("transient_retries") != {FAILED_TASK_ID: CONSUMED_RETRIES}
            or state.get("started_at_utc") != SOURCE_STARTED_AT_UTC
            or state.get("completed_at_utc") is not None
            or state.get("phase") != "EXECUTING_EPOCH"
            or state.get("next_queue_index") != 10
            or coverage.get("protected_final_read") is not False
            or coverage.get("actual_orders_placed") is not False
            or coverage.get("robust_positive_candidate_id") is not None
            or engine.get("champion_candidate_id") is not None):
        raise V4_1ContinuationError("V4 source boundary or position differs")
    incident, reconciled_journal = _incident_record(root, source_root, state)
    candidate_imports = _candidate_imports(root, source_root, state)
    source_inventory = _source_artifact_inventory(root, source_root)
    body = {
        "audit_version": "klax-v4.1-source-campaign-audit-v1",
        "status": "PASS",
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "source_records": records,
        "source_recovery_state_sha256": SOURCE_RECOVERY_STATE_SHA256,
        "source_artifact_count": len(source_inventory),
        "source_artifact_bytes": sum(row["bytes"] for row in source_inventory),
        "source_artifact_inventory_sha256": canonical_hash(source_inventory),
        "candidate_import_count": len(candidate_imports),
        "candidate_imports_sha256": canonical_hash(candidate_imports),
        "original_call_journal_sha256": canonical_hash(
            coverage["call_journal"]),
        "reconciled_call_journal_sha256": canonical_hash(reconciled_journal),
        "incident_sha256": incident["incident_sha256"],
        "original_started_at_utc": SOURCE_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "audit_sha256": canonical_hash(body)}


def build_continuation_import_record_v4_1(root: Path | str) -> dict[str, Any]:
    """Build the stable JSON-native record published by V4.1 readiness."""
    root = Path(root).resolve()
    source_root = _path(root, SOURCE_ROOT)
    state = _read_json(_path(root, SOURCE_RECOVERY_PATH), "V4 recovery state")
    audit = audit_source_campaign_v4_1(root)
    readiness = _read_json(_path(root, SOURCE_READINESS_PATH), "V4 readiness")
    incident, reconciled_journal = _incident_record(root, source_root, state)
    candidate_imports = _candidate_imports(root, source_root, state)
    source_inventory = _source_artifact_inventory(root, source_root)
    engine, coverage = state["engine"], state["coverage"]
    record = {
        "record_version": CONTINUATION_RECORD_VERSION,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "source_records": audit["source_records"],
        "source_recovery_state_sha256": SOURCE_RECOVERY_STATE_SHA256,
        "source_v4_code_inventory": readiness["bindings"]["v4_code_inventory"],
        "source_v4_code_sha256": readiness["bindings"]["v4_code_sha256"],
        "source_artifact_inventory": source_inventory,
        "source_artifact_inventory_sha256": canonical_hash(source_inventory),
        "candidate_imports": candidate_imports,
        "candidate_imports_sha256": canonical_hash(candidate_imports),
        "scheduler_lineage": {
            "epochs_sha256": canonical_hash(coverage["epochs"]),
            "digests_sha256": canonical_hash(coverage["digests"]),
            "allocation_decisions_sha256": canonical_hash(
                coverage["allocation_decisions"]),
            "allocation_history_sha256": canonical_hash(
                coverage["allocation_history"]),
            "synthesis_artifacts_sha256": canonical_hash(
                coverage["synthesis_artifacts"]),
            "original_call_journal_sha256": canonical_hash(
                coverage["call_journal"]),
            "reconciled_call_journal_sha256": canonical_hash(
                reconciled_journal),
            "digest_count": len(coverage["digests"]),
            "completed_synthesis_calls": 1,
            "completed_epochs": COMPLETED_EPOCHS,
            "current_epoch": CURRENT_EPOCH,
            "current_epoch_completed_candidates": (
                CURRENT_EPOCH_COMPLETED_CANDIDATES),
            "next_queue_index_after_fallback": 11,
        },
        "incident": incident,
        "cumulative_budget": {
            "maximum_candidates": MAXIMUM_CANDIDATES,
            "consumed_candidates": CONSUMED_CANDIDATES,
            "remaining_candidates": MAXIMUM_CANDIDATES - CONSUMED_CANDIDATES,
            "maximum_model_calls": MAXIMUM_MODEL_CALLS,
            "consumed_model_calls": CONSUMED_MODEL_CALLS,
            "remaining_model_calls": MAXIMUM_MODEL_CALLS - CONSUMED_MODEL_CALLS,
            "maximum_context_tokens": MAXIMUM_CONTEXT_TOKENS,
            "consumed_context_tokens": CONSUMED_CONTEXT_TOKENS,
            "remaining_context_tokens": (
                MAXIMUM_CONTEXT_TOKENS - CONSUMED_CONTEXT_TOKENS),
            "maximum_wall_seconds": MAXIMUM_WALL_SECONDS,
            "consumed_wall_seconds_floor": CONSUMED_WALL_SECONDS,
            "maximum_epochs": MAXIMUM_EPOCHS,
            "current_epoch": CURRENT_EPOCH,
            "completed_epochs": COMPLETED_EPOCHS,
            "consumed_retry_calls": CONSUMED_RETRIES,
            "remaining_nomination_call_reservation": 2_989,
            "remaining_synthesis_call_reservation": 63,
            "remaining_retry_call_reservation": 162,
        },
        "original_started_at_utc": SOURCE_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "continuation_position": {
            "phase": "EXECUTING_EPOCH",
            "epoch": CURRENT_EPOCH,
            "next_task_id": "v4-e007-s12",
            "next_queue_index": 11,
            "failed_task_fallback_completed": True,
            "imported_candidate_count": CONSUMED_CANDIDATES,
        },
        "safety": {
            "source_campaign_mutation_permitted": False,
            "protected_final_read": False,
            "actual_orders_placed": False,
            "network_permitted": False,
            "model_nomination_failure_is_scientific_evidence": False,
            "fallback_creates_model_response": False,
            "fixed_universe_changed": False,
            "promotion_gates_changed": False,
        },
        "audit_sha256": audit["audit_sha256"],
    }
    return json.loads(json.dumps(record, sort_keys=True, allow_nan=False))


def reconcile_failed_nomination_v4_1(
    root: Path | str, record: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the authorized zero-call disposition for the exact failed task."""
    expected = build_continuation_import_record_v4_1(root)
    if dict(record) != expected:
        raise V4_1ContinuationError("V4.1 continuation import record differs")
    incident = expected["incident"]
    if (incident["status"] != "MODEL_NOMINATION_FAILED"
            or incident["task_id"] != FAILED_TASK_ID
            or incident["plan_sha256"] != FAILED_PLAN_SHA256
            or incident["attempts_charged"] != 3):
        raise V4_1ContinuationError("V4.1 incident is not fallback eligible")
    fallback = deepcopy(incident["fallback"])
    body = {
        "reconciliation_version": "klax-v4.1-failed-nomination-reconciliation-v1",
        "status": "HOST_EXACT_PLAN_FALLBACK",
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "task_id": FAILED_TASK_ID,
        "plan_sha256": FAILED_PLAN_SHA256,
        "incident_sha256": incident["incident_sha256"],
        "model_calls_before": CONSUMED_MODEL_CALLS,
        "model_calls_charged": 0,
        "model_calls_after": CONSUMED_MODEL_CALLS,
        "model_nomination_succeeded": False,
        "model_response_fabricated": False,
        "scientific_evidence_created": False,
        "fallback": fallback,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return {**body, "reconciliation_sha256": canonical_hash(body)}


def reverify_imported_candidates_v4_1(root: Path | str) -> dict[str, Any]:
    """Independently rerun the frozen V4 verifier for all 82 imports."""
    root = Path(root).resolve()
    state = _read_json(_path(root, SOURCE_RECOVERY_PATH), "V4 recovery state")
    candidate_imports = _candidate_imports(
        root, _path(root, SOURCE_ROOT), state)
    label_path = (root / "data/frozen/v4_development_1330_1500_1800"
                  / "development_evaluation_labels.jsonl")
    labels: list[dict[str, Any]] = []
    try:
        with label_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    labels.append(json.loads(line))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V4_1ContinuationError(
            "V4.1 evaluation labels are unavailable for reverification") from exc
    labels_by_day = {row["climate_date"]: row for row in labels}
    fee_model = _read_json(root / "configs/evaluation.json", "fee config")[
        "fee_scenario"]
    results = []
    by_id = {row["candidate_id"]: row for row in candidate_imports}
    for candidate_id, raw in sorted(state["engine"]["candidates"].items()):
        plan = ResearchPlanV4.from_dict(raw["plan"])
        primary = _path(
            root, SOURCE_ROOT / "candidates" / "primary" / plan.identity)
        predictions_artifact = _read_json(
            primary / "predictions.json", "candidate predictions")
        predictions = []
        for saved in predictions_artifact.get("rows", []):
            row = dict(saved)
            label = labels_by_day.get(row.get("climate_date"), {})
            outcomes = {value.get("ticker"): value.get("yes_outcome")
                        for value in label.get("contracts", [])}
            winners = [index for index, ticker in enumerate(row.get("tickers", []))
                       if outcomes.get(ticker) == 1]
            if len(winners) == 1:
                row["observed_index"] = winners[0]
            predictions.append(row)
        ledger = _read_json(primary / "ledger.json", "candidate ledger")
        ledger["protected_final_read"] = False
        verifier_record = {
            "plan": plan.to_dict(),
            "plan_sha256": plan.identity,
            "ledger": ledger,
            "predictions": predictions,
            "fee_model": fee_model,
            "protected_final_read": False,
            "orders_created": False,
            "artifact_sha256s": dict(raw.get("artifact_sha256s") or {}),
        }
        actual = verify_candidate_v4(root, verifier_record)
        saved = _read_json(
            _path(root, SOURCE_ROOT / "reviews" / candidate_id
                  / "independent-v4-verification.json"),
            "saved independent V4 verification")
        if actual != saved:
            raise V4_1ContinuationError(
                f"Imported candidate reverification differs: {candidate_id}")
        imported = by_id[candidate_id]
        results.append({
            "candidate_id": candidate_id,
            "research_plan_sha256": plan.identity,
            "verifier_status": actual.get("status"),
            "promotion_eligible": actual.get("promotion_eligible"),
            "gate_failures": actual.get("gate_failures"),
            "verification_sha256": actual.get("verification_sha256"),
            "saved_review_artifacts_sha256": canonical_hash(
                imported["review_artifacts"]),
            "exact_match": True,
        })
    body = {
        "reverification_version": "klax-v4.1-imported-candidate-reverification-v1",
        "status": "PASS",
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "candidate_count": len(results),
        "candidate_results": results,
        "candidate_results_sha256": canonical_hash(results),
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    if len(results) != CONSUMED_CANDIDATES:
        raise V4_1ContinuationError("Imported candidate reverification count differs")
    return {**body, "reverification_sha256": canonical_hash(body)}
