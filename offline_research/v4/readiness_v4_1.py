"""Fail-closed authorization for the V4.1 continuation campaign.

V4.1 is a new campaign identity which imports one exact, incomplete V4
checkpoint.  It does not grant a fresh budget.  The source checkpoint,
authorization, failed third attempt, counters, original start time, and
absolute twelve-hour deadline are immutable inputs.  This module may validate
or issue a new one-use authorization; it never starts a campaign.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import importlib
import inspect
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from klax_lab.campaign_v3 import V3CampaignAuthorization, V3CampaignBudget
from klax_lab.orchestrator_v3 import _CampaignExecutionLease
from klax_lab.provenance import canonical_hash, sha256_file

from v4 import readiness_v4 as source_readiness


READINESS_VERSION = "klax-v4-1-continuation-readiness-v1"
TICKET_VERSION = "klax-v4-1-continuation-ticket-v1"
IMPORT_MANIFEST_VERSION = "klax-v4-1-continuation-import-manifest-v1"
READY_STATUS = "READY_FOR_V4_1_CONTINUATION"

READINESS_PATH = Path("data/manifests/v4_1_readiness.json")
TICKET_PATH = Path("runs/v4_1_offline_campaign_ticket.json")
IMPORT_MANIFEST_PATH = Path("data/manifests/v4_1_continuation_import.json")
AMENDMENT_PATH = Path("v4/config/v4_1_continuation_amendment.json")
CONTRACT_PATH = Path("docs/V4_1_CONTINUATION_AMENDMENT.md")
OUTPUT_PARENT = Path("runs/campaigns_v4_1")

SOURCE_CAMPAIGN_ID = "v4-offline-20260926T212122609Z"
SOURCE_CAMPAIGN_PATH = Path("runs/campaigns_v4") / SOURCE_CAMPAIGN_ID
SOURCE_READINESS_PATH = Path("data/manifests/v4_readiness.json")
SOURCE_TICKET_PATH = Path("runs/v4_offline_campaign_ticket.json")
SOURCE_CLAIM_PATH = Path("runs/v4_offline_campaign_ticket.json.claimed.json")
SOURCE_RECOVERY_PATH = SOURCE_CAMPAIGN_PATH / "recovery-state.json"
SOURCE_STDERR_PATH = Path("runs/v4_campaign.stderr.log")
SOURCE_PROCESS_PATH = Path("runs/v4_campaign.process.json")

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

ORIGINAL_STARTED_AT_UTC = "2026-09-26T21:23:25.261434+00:00"
ABSOLUTE_DEADLINE_AT_UTC = "2026-09-27T09:23:25.261434+00:00"
ORIGINAL_MAXIMUM_WALL_SECONDS = 43_200
EXHAUSTED_TASK_ID = "v4-e007-s11"
EXHAUSTED_TASK_QUEUE_INDEX = 10
RECONCILED_CALL_NUMBER = 86
RECONCILED_COMPLETED_AT_UTC = "2026-09-26T21:51:14.126716+00:00"

COUNTER_FLOORS = {
    "model_calls": 86,
    "model_context_tokens_reserved": 1_409_024,
    "admitted_candidates": 82,
    "executed_candidates": 82,
    "current_epoch": 7,
    "completed_epochs": 6,
    "current_epoch_admissions": 10,
    "empty_epochs": 0,
    "next_queue_index_after_zero_call_fallback": 11,
    "transient_retries_for_exhausted_task": 2,
    "consumed_wall_seconds_floor": 1_669,
}
REMAINING_CEILINGS = {
    "model_calls": 3_214,
    "model_context_tokens": 52_658_176,
    "distinct_candidates": 2_990,
    "current_epoch_candidate_slots": 2,
}

# Filled only after the immutable import manifest has been published.  Keeping
# the exact file digest in code prevents a replacement manifest from becoming
# authoritative merely because it is internally self-consistent.
EXPECTED_IMPORT_MANIFEST_FILE_SHA256 = (
    "65cdba9f61fe883ff934c50052c7acb88ce339310af20013d546a78012427f41")

_SHA = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")


class V41ReadinessError(ValueError):
    """The V4.1 continuation is absent, mutated, expired, or unsafe."""


@dataclass(frozen=True)
class V41ContinuationAuthorization:
    """V3-engine-compatible authorization plus immutable import boundaries."""

    authorization: V3CampaignAuthorization
    source_campaign_id: str
    import_manifest_path: Path
    import_manifest_sha256: str
    source_recovery_path: Path
    source_recovery_file_sha256: str
    source_recovery_state_sha256: str
    original_started_at_utc: str
    absolute_deadline_at_utc: str
    counter_floors: dict[str, int]
    remaining_ceilings: dict[str, int]
    exhausted_task_id: str
    reconciled_call_number: int
    reconciled_completed_at_utc: str


def _read(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                V41ReadinessError(f"Non-finite JSON in {label}: {token}")),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V41ReadinessError(f"Missing or invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V41ReadinessError(f"{label} must contain one object")
    return value


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise V41ReadinessError(f"{label} fields differ")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise V41ReadinessError(f"Invalid {label} SHA-256")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise V41ReadinessError(f"Invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V41ReadinessError(f"Invalid {label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V41ReadinessError(f"{label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _relative(root: Path, value: Any, label: str) -> tuple[str, Path]:
    if (not isinstance(value, str) or not value or "\\" in value
            or ":" in value):
        raise V41ReadinessError(f"Invalid {label} path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise V41ReadinessError(f"{label} path escapes the project")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise V41ReadinessError(f"{label} path escapes the project") from exc
    lowered = {part.casefold().replace("-", "_") for part in path.parts}
    if {"protected_final", "holdout"} & lowered:
        raise V41ReadinessError(f"{label} enters protected storage")
    return relative.as_posix(), path


def _file_record(root: Path, value: Any, label: str,
                 expected_path: Path | None = None) -> Path:
    record = _exact(value, {"path", "bytes", "sha256"}, label)
    relative, path = _relative(root, record["path"], label)
    if expected_path is not None and relative != expected_path.as_posix():
        raise V41ReadinessError(f"{label} path differs")
    if (type(record["bytes"]) is not int or record["bytes"] < 0
            or not path.is_file()
            or path.stat().st_size != record["bytes"]
            or sha256_file(path) != _sha(record["sha256"], label)):
        raise V41ReadinessError(f"{label} changed")
    return path


def _record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.resolve().relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _inventory(root: Path) -> list[dict[str, Any]]:
    campaign = (root / SOURCE_CAMPAIGN_PATH).resolve()
    rows = []
    for path in sorted(
            (item for item in campaign.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(campaign).as_posix()):
        rows.append(_record(path, root))
    return rows


def _verify_source_recovery(root: Path) -> dict[str, Any]:
    path = root / SOURCE_RECOVERY_PATH
    if not path.is_file() or sha256_file(path) != SOURCE_RECOVERY_FILE_SHA256:
        raise V41ReadinessError("Source V4 recovery file changed")
    value = _read(path, "source V4 recovery")
    body = {key: item for key, item in value.items() if key != "state_sha256"}
    engine = value.get("engine")
    coverage = value.get("coverage")
    queue = value.get("queue")
    if (value.get("state_sha256") != SOURCE_RECOVERY_STATE_SHA256
            or canonical_hash(body) != SOURCE_RECOVERY_STATE_SHA256
            or value.get("campaign_id") != SOURCE_CAMPAIGN_ID
            or value.get("phase") != "EXECUTING_EPOCH"
            or value.get("started_at_utc") != ORIGINAL_STARTED_AT_UTC
            or value.get("completed_at_utc") is not None
            or value.get("next_queue_index") != 10
            or value.get("protected_final_evaluated") is not False
            or value.get("actual_orders_placed") is not False
            or not isinstance(engine, dict)
            or not isinstance(coverage, dict)
            or not isinstance(queue, list)):
        raise V41ReadinessError("Source V4 recovery identity or safety differs")
    expected = {
        "model_calls": 86, "model_context_tokens_reserved": 1_409_024,
        "admitted_candidates": 82, "executed_candidates": 82,
        "current_epoch": 7, "empty_epochs": 0,
    }
    if any(engine.get(key) != item for key, item in expected.items()):
        raise V41ReadinessError("Source V4 counters differ")
    if (engine.get("epoch_admissions", {}).get("7") != 10
            or engine.get("transient_retries") != {EXHAUSTED_TASK_ID: 2}
            or len(engine.get("candidates", {})) != 82
            or any(row.get("evaluation") is None or row.get("promotion") is None
                   for row in engine["candidates"].values())):
        raise V41ReadinessError("Source V4 candidate or retry state differs")
    if (len(queue) != 12
            or [row.get("status") for row in queue[:10]] != ["REVIEWED"] * 10
            or [row.get("status") for row in queue[10:]] != ["PENDING"] * 2
            or queue[10].get("task_id") != EXHAUSTED_TASK_ID):
        raise V41ReadinessError("Source V4 active queue differs")
    journal = coverage.get("call_journal")
    if (not isinstance(journal, list) or len(journal) != 86
            or [row.get("model_call_number") for row in journal] != list(range(1, 87))
            or journal[-1].get("task_id") != EXHAUSTED_TASK_ID
            or journal[-1].get("attempt") != 3
            or journal[-1].get("status") != "RESERVED"
            or journal[-1].get("call_type") != "retry"
            or coverage.get("protected_final_read") is not False
            or coverage.get("actual_orders_placed") is not False
            or coverage.get("integrity_failures") != []):
        raise V41ReadinessError("Source V4 call journal or safety state differs")
    epochs = coverage.get("epochs", {})
    if (set(epochs) != {str(index) for index in range(1, 8)}
            or any(epochs[str(index)].get("reviewed") != 12
                   for index in range(1, 7))
            or epochs["7"].get("reviewed") != 10
            or epochs["7"].get("admitted") != 10):
        raise V41ReadinessError("Source V4 begun-epoch accounting differs")
    return value


def _source_authorization_bindings(root: Path) -> dict[str, Any]:
    readiness_path = root / SOURCE_READINESS_PATH
    ticket_path = root / SOURCE_TICKET_PATH
    claim_path = root / SOURCE_CLAIM_PATH
    expected_files = (
        (readiness_path, SOURCE_READINESS_SHA256, "source readiness"),
        (ticket_path, SOURCE_TICKET_SHA256, "source ticket"),
        (claim_path, SOURCE_CLAIM_SHA256, "source claim"),
    )
    for path, expected, label in expected_files:
        if not path.is_file() or sha256_file(path) != expected:
            raise V41ReadinessError(f"{label} changed")
    readiness = _read(readiness_path, "source readiness")
    ticket = _read(ticket_path, "source ticket")
    claim = _read(claim_path, "source claim")
    if (readiness.get("campaign_id") != SOURCE_CAMPAIGN_ID
            or readiness.get("protected_final_access_authorized") is not False
            or readiness.get("actual_orders_authorized") is not False
            or ticket.get("campaign_id") != SOURCE_CAMPAIGN_ID
            or ticket.get("readiness_sha256") != SOURCE_READINESS_SHA256
            or ticket.get("maximum_wall_seconds") != ORIGINAL_MAXIMUM_WALL_SECONDS
            or ticket.get("one_use") is not True
            or ticket.get("protected_final_authorized") is not False
            or ticket.get("actual_orders_authorized") is not False
            or claim != {
                "claim_version": "klax-v3-ticket-claim-v1",
                "campaign_id": SOURCE_CAMPAIGN_ID,
                "ticket_sha256": SOURCE_TICKET_SHA256,
                "readiness_sha256": SOURCE_READINESS_SHA256,
                "synthetic": False,
            }):
        raise V41ReadinessError("Source V4 authorization differs")

    # The source readiness froze the exact V4 files which existed at issue
    # time.  New V4.1 modules are legitimate descendants and must not make the
    # ancestry unverifiable, so recompute the saved inventory rather than the
    # current v4/*.py superset.
    validated = source_readiness.validate_v4_preregistration(root)
    current = source_readiness._collect_bindings(root, validated)
    saved = readiness.get("bindings")
    if not isinstance(saved, dict):
        raise V41ReadinessError("Source V4 readiness bindings are missing")
    saved_code = saved.get("v4_code_inventory")
    if not isinstance(saved_code, list):
        raise V41ReadinessError("Source V4 code inventory is missing")
    recomputed = []
    for row in saved_code:
        record = _exact(row, {"path", "bytes", "sha256"}, "source V4 code")
        relative, path = _relative(root, record["path"], "source V4 code")
        actual = _record(path, root)
        if actual != record or relative != record["path"]:
            raise V41ReadinessError(f"Source V4 code changed: {relative}")
        recomputed.append(actual)
    current["v4_code_inventory"] = recomputed
    current["v4_code_sha256"] = canonical_hash(recomputed)
    if current != saved:
        raise V41ReadinessError("Source V4 readiness-bound artifact changed")
    return {"readiness": readiness, "ticket": ticket, "claim": claim}


def _load_published_import(root: Path) -> tuple[dict[str, Any], str]:
    path = root / IMPORT_MANIFEST_PATH
    if not path.is_file():
        raise V41ReadinessError("Published V4.1 import manifest is missing")
    file_sha = sha256_file(path)
    if (EXPECTED_IMPORT_MANIFEST_FILE_SHA256 != "TO_BE_FROZEN"
            and file_sha != EXPECTED_IMPORT_MANIFEST_FILE_SHA256):
        raise V41ReadinessError("Published V4.1 import manifest file changed")
    value = _read(path, "V4.1 import manifest")
    manifest_version = value.pop("manifest_version", None)
    claimed = value.pop("import_sha256", None)
    if (manifest_version != IMPORT_MANIFEST_VERSION
            or not isinstance(claimed, str)
            or claimed != canonical_hash(value)):
        raise V41ReadinessError("V4.1 import manifest envelope differs")
    module = importlib.import_module("v4.continuation_v4_1")
    builder = getattr(module, "build_continuation_import_record_v4_1", None)
    if not callable(builder):
        raise V41ReadinessError("V4.1 import record builder is unavailable")
    actual = builder(root)
    if not isinstance(actual, dict) or actual != value:
        raise V41ReadinessError("Published V4.1 import record differs from source")
    return {**value, "manifest_version": manifest_version,
            "import_sha256": claimed}, file_sha


def _verify_import_against_fixed_source(root: Path,
                                        manifest: Mapping[str, Any]) -> None:
    # These checks are intentionally independent of the execution module's
    # builder so that a coordinated code+manifest mutation still fails.
    _source_authorization_bindings(root)
    source_state = _verify_source_recovery(root)
    budget = manifest.get("cumulative_budget")
    position = manifest.get("continuation_position")
    safety = manifest.get("safety")
    incident = manifest.get("incident")
    if (manifest.get("source_campaign_id") != SOURCE_CAMPAIGN_ID
            or manifest.get("original_started_at_utc") != ORIGINAL_STARTED_AT_UTC
            or manifest.get("absolute_deadline_at_utc")
            != ABSOLUTE_DEADLINE_AT_UTC
            or manifest.get("source_recovery_state_sha256")
            != SOURCE_RECOVERY_STATE_SHA256
            or not isinstance(budget, Mapping)
            or budget.get("consumed_model_calls") != 86
            or budget.get("consumed_context_tokens") != 1_409_024
            or budget.get("consumed_candidates") != 82
            or budget.get("current_epoch") != 7
            or budget.get("completed_epochs") != 6
            or budget.get("consumed_retry_calls") != 2
            or budget.get("consumed_wall_seconds_floor") != 1_669
            or budget.get("maximum_wall_seconds") != 43_200
            or budget.get("remaining_model_calls") != 3_214
            or budget.get("remaining_context_tokens") != 52_658_176
            or budget.get("remaining_candidates") != 2_990
            or budget.get("remaining_nomination_call_reservation") != 2_989
            or budget.get("remaining_synthesis_call_reservation") != 63
            or budget.get("remaining_retry_call_reservation") != 162
            or not isinstance(position, Mapping)
            or position.get("epoch") != 7
            or position.get("next_queue_index") != 11
            or position.get("next_task_id") != "v4-e007-s12"
            or position.get("failed_task_fallback_completed") is not True
            or position.get("imported_candidate_count") != 82
            or not isinstance(safety, Mapping)
            or safety.get("protected_final_read") is not False
            or safety.get("actual_orders_placed") is not False
            or safety.get("network_permitted") is not False
            or safety.get("source_campaign_mutation_permitted") is not False
            or safety.get("promotion_gates_changed") is not False
            or not isinstance(incident, Mapping)
            or incident.get("task_id") != EXHAUSTED_TASK_ID
            or incident.get("attempts_charged") != 3
            or incident.get("model_call_numbers") != [84, 85, 86]
            or incident.get("status") != "MODEL_NOMINATION_FAILED"
            or incident.get("raw_action") != "abstain"
            or incident.get("raw_seed_index") != 0
            or incident.get("model_nomination_succeeded") is not False
            or incident.get("protected_final_read") is not False
            or incident.get("actual_orders_placed") is not False):
        raise V41ReadinessError("V4.1 import invariant differs")
    reconciled = json.loads(json.dumps(source_state["coverage"]["call_journal"]))
    reconciled[-1]["status"] = "FAILED"
    reconciled[-1]["completed_at_utc"] = RECONCILED_COMPLETED_AT_UTC
    reconciled[-1]["failure_class"] = "MODEL_NOMINATION_FAILED"
    reconciled[-1]["failure_reason"] = "schema_invalid_singleton_nonproposal"
    reconciled_sha = canonical_hash(reconciled)
    scheduler = manifest.get("scheduler_lineage")
    attempts = incident.get("attempts")
    if (reconciled_sha
            != "bc82f53ba0e5db648f556e22bf29645add3dc397447d87a41a4551dae043703c"
            or incident.get("reconciled_call_journal_sha256") != reconciled_sha
            or not isinstance(scheduler, Mapping)
            or scheduler.get("reconciled_call_journal_sha256") != reconciled_sha
            or scheduler.get("current_epoch") != 7
            or scheduler.get("completed_epochs") != 6
            or scheduler.get("next_queue_index_after_fallback") != 11
            or not isinstance(attempts, list) or len(attempts) != 3):
        raise V41ReadinessError("V4.1 failed-call reconciliation differs")
    expected_process_sha = {
        1: "02efaea5ce4885aa8a3552c21cf42f14e745cd75a791920c085d83d566d721a4",
        2: "cd0f7e3eac26991315f5e0f125cbc69383131247a18f4b40438edad7c9001eb4",
        3: "10378c14c0df8261f7e2b3835ca978025e7a8f4fe7b5ad3398e6728be81ddf6b",
    }
    for index, attempt in enumerate(attempts, 1):
        files = attempt.get("files") if isinstance(attempt, Mapping) else None
        by_name = ({Path(row["path"]).name: row for row in files}
                   if isinstance(files, list) else {})
        if (attempt.get("attempt") != index
                or attempt.get("model_call_number") != 83 + index
                or attempt.get("process_status")
                != "COMPLETED_WITH_SCHEMA_INVALID_OUTPUT"
                or attempt.get("raw_response_sha256")
                != "a573f1adf1ac3f348051957fdc161dcdb2077eadbcb7fb4e4bc167ec5978dc71"
                or by_name.get("stdout.bin", {}).get("sha256")
                != "d3ecbc80e9887626fb366e25c6f94daae7173e0921ae6f80d1e129e08e44a9b5"
                or by_name.get("packet.json", {}).get("sha256")
                != "cf9c667dd71c0393ed54570305153ee429afb0b1ec6ff1a6f0db7248499ce737"
                or by_name.get("response-schema.json", {}).get("sha256")
                != "d3e0a291a755eff97f7fa6bb8f8e389cdd61da7181bb7766a2dfc7f40f744f54"
                or by_name.get("process.json", {}).get("sha256")
                != expected_process_sha[index]):
            raise V41ReadinessError("V4.1 raw failed-attempt evidence differs")
    inventory = manifest.get("source_artifact_inventory")
    if (inventory != _inventory(root)
            or manifest.get("source_artifact_inventory_sha256")
            != canonical_hash(inventory)):
        raise V41ReadinessError("V4.1 source campaign inventory changed")
    candidates = manifest.get("candidate_imports")
    if (not isinstance(candidates, list) or len(candidates) != 82
            or manifest.get("candidate_imports_sha256")
            != canonical_hash(candidates)):
        raise V41ReadinessError("V4.1 candidate import inventory changed")
    source_records = manifest.get("source_records")
    if not isinstance(source_records, Mapping):
        raise V41ReadinessError("V4.1 source records are missing")
    fixed_records = {
        "readiness": (SOURCE_READINESS_PATH, SOURCE_READINESS_SHA256),
        "ticket": (SOURCE_TICKET_PATH, SOURCE_TICKET_SHA256),
        "claim": (SOURCE_CLAIM_PATH, SOURCE_CLAIM_SHA256),
        "recovery": (SOURCE_RECOVERY_PATH, SOURCE_RECOVERY_FILE_SHA256),
        "stderr": (SOURCE_STDERR_PATH, SOURCE_STDERR_SHA256),
        "controller_process": (SOURCE_PROCESS_PATH, SOURCE_PROCESS_SHA256),
    }
    for key, (path, digest) in fixed_records.items():
        record = source_records.get(key)
        target = _file_record(root, record, key, path)
        if sha256_file(target) != digest:
            raise V41ReadinessError(f"{key} is not the frozen source file")


def _v4_1_code_inventory(root: Path) -> list[dict[str, Any]]:
    required = {
        "v4/continuation_v4_1.py", "v4/local_worker_v4_1.py",
        "v4/orchestrator_v4_1.py", "v4/readiness_v4_1.py",
    }
    files = sorted((root / "v4").glob("*v4_1.py"))
    found = {path.relative_to(root).as_posix() for path in files}
    if not required <= found:
        raise V41ReadinessError("Required V4.1 production modules are missing")
    controller = root / "scripts/control_v4_1_campaign.ps1"
    if not controller.is_file():
        raise V41ReadinessError("Required V4.1 controller is missing")
    return [_record(path, root) for path in [*files, controller]]


def _runtime_self_tests(root: Path) -> dict[str, Any]:
    worker_module = importlib.import_module("v4.local_worker_v4_1")
    runner_module = importlib.import_module("v4.orchestrator_v4_1")
    worker_test = getattr(worker_module, "run_v4_1_worker_protocol_self_test", None)
    integration_test = getattr(
        runner_module, "run_v4_1_production_integration_self_test", None)
    required = {
        "start_v4_1_campaign": ("root", "readiness_path", "ticket_path"),
        "resume_v4_1_campaign": ("root", "readiness_path", "ticket_path"),
        "campaign_status_v4_1": ("root", "ticket_path"),
    }
    signatures = {}
    for name, expected in required.items():
        function = getattr(runner_module, name, None)
        if not callable(function):
            raise V41ReadinessError(f"V4.1 runner interface is missing: {name}")
        parameters = tuple(inspect.signature(function).parameters)
        if parameters != expected:
            raise V41ReadinessError(f"V4.1 runner signature differs: {name}")
        signatures[name] = list(parameters)
    if not callable(worker_test) or not callable(integration_test):
        raise V41ReadinessError("V4.1 runtime self-test is missing")
    worker = worker_test(root)
    integration = integration_test(root)
    for label, value in (("worker", worker), ("integration", integration)):
        if (not isinstance(value, dict) or value.get("status") != "PASS"
                or value.get("protected_final_read") not in (None, False)
                or value.get("actual_orders_placed") not in (None, False)):
            raise V41ReadinessError(f"V4.1 {label} self-test failed")
        checks = value.get("checks")
        if isinstance(checks, dict) and (not checks or not all(checks.values())):
            raise V41ReadinessError(f"V4.1 {label} self-test checks failed")
    required_integration_checks = {
        "future_candidate_parser_exhaustion_zero_call_fallback",
        "future_candidate_parser_exhaustion_no_fourth_call",
        "runtime_failure_remains_hard_stop",
        "synthesis_failure_remains_hard_stop",
    }
    integration_checks = integration.get("checks")
    if (not isinstance(integration_checks, dict)
            or not required_integration_checks <= set(integration_checks)
            or not all(integration_checks[name]
                       for name in required_integration_checks)):
        raise V41ReadinessError(
            "V4.1 production self-test does not prove the general "
            "parser-exhaustion fallback and required hard stops")
    return {"runner_signatures": signatures, "worker_self_test": worker,
            "production_integration_self_test": integration}


def _reverify_imported_candidates(root: Path) -> dict[str, Any]:
    module = importlib.import_module("v4.continuation_v4_1")
    function = getattr(module, "reverify_imported_candidates_v4_1", None)
    if not callable(function):
        raise V41ReadinessError("V4.1 imported-candidate reverifier is missing")
    value = function(root)
    if (not isinstance(value, dict) or value.get("status") != "PASS"
            or value.get("candidate_count") != 82
            or value.get("protected_final_read") is not False
            or value.get("actual_orders_placed") is not False
            or _SHA.fullmatch(str(value.get("reverification_sha256"))) is None):
        raise V41ReadinessError("V4.1 imported-candidate reverification failed")
    return value


def _amendment_bindings(root: Path) -> dict[str, Any]:
    records = {}
    for label, relative in (("amendment", AMENDMENT_PATH),
                            ("contract", CONTRACT_PATH)):
        path = root / relative
        if not path.is_file():
            raise V41ReadinessError(f"V4.1 {label} is missing")
        records[label] = _record(path, root)
    amendment = _read(root / AMENDMENT_PATH, "V4.1 continuation amendment")
    text = (root / CONTRACT_PATH).read_text(encoding="utf-8")
    required_literals = (
        SOURCE_CAMPAIGN_ID, SOURCE_RECOVERY_FILE_SHA256,
        SOURCE_RECOVERY_STATE_SHA256, SOURCE_PROCESS_SHA256,
        ORIGINAL_STARTED_AT_UTC,
        ABSOLUTE_DEADLINE_AT_UTC, EXHAUSTED_TASK_ID,
    )
    if any(token not in json.dumps(amendment, sort_keys=True) + text
           for token in required_literals):
        raise V41ReadinessError("V4.1 amendment omits a frozen source invariant")
    return records


def _deadline_unexpired(now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise V41ReadinessError("V4.1 clock must be timezone-aware")
    return current.astimezone(UTC) < _timestamp(
        ABSOLUTE_DEADLINE_AT_UTC, "absolute deadline")


def validate_v4_1_continuation_preregistration(
        root: Path | str, *, require_unexpired: bool = False,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate the exact source import and all V4.1 executable boundaries."""
    root = Path(root).resolve()
    manifest, manifest_file_sha = _load_published_import(root)
    _verify_import_against_fixed_source(root, manifest)
    amendment = _amendment_bindings(root)
    code = _v4_1_code_inventory(root)
    candidate_reverification = _reverify_imported_candidates(root)
    runtime = _runtime_self_tests(root)
    deadline_unexpired = _deadline_unexpired(now)
    if require_unexpired and not deadline_unexpired:
        raise V41ReadinessError("The original V4 twelve-hour deadline expired")
    return {
        "status": "PASS",
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "import_manifest": {
            "path": IMPORT_MANIFEST_PATH.as_posix(),
            "sha256": manifest_file_sha,
            "import_sha256": manifest["import_sha256"],
        },
        "amendment": amendment,
        "v4_1_code_inventory": code,
        "v4_1_code_sha256": canonical_hash(code),
        "candidate_reverification": candidate_reverification,
        "runtime": runtime,
        "original_started_at_utc": ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "deadline_unexpired": deadline_unexpired,
        "counter_floors": dict(COUNTER_FLOORS),
        "remaining_ceilings": dict(REMAINING_CEILINGS),
        "protected_final_read": False,
        "actual_orders_placed": False,
    }


def _authorization_path(root: Path, supplied: Path | str | None,
                        expected: Path, label: str) -> Path:
    relative = expected if supplied is None else Path(supplied)
    path = relative.resolve() if relative.is_absolute() else (root / relative).resolve()
    try:
        actual = path.relative_to(root)
    except ValueError as exc:
        raise V41ReadinessError(f"V4.1 {label} path escapes the project") from exc
    if actual != expected:
        raise V41ReadinessError(f"V4.1 {label} path differs from the fixed path")
    return path


def _write_exclusive(path: Path, value: Mapping[str, Any], label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise V41ReadinessError(f"V4.1 {label} already exists") from exc


def _compatibility_authorization(
        root: Path, campaign_id: str, readiness_path: Path, ticket_path: Path,
        readiness: Mapping[str, Any], ticket: Mapping[str, Any],
        bindings: Mapping[str, Any]) -> V3CampaignAuthorization:
    source = _read(root / SOURCE_READINESS_PATH, "source V4 readiness")
    budget = V3CampaignBudget(**source["compatibility_v3_budget"])
    source_bindings = source["bindings"]
    return V3CampaignAuthorization(
        root=root, campaign_id=campaign_id,
        readiness_path=readiness_path, ticket_path=ticket_path,
        readiness_sha256=sha256_file(readiness_path),
        ticket_sha256=sha256_file(ticket_path),
        config_sha256=source_bindings["v3_goal"]["sha256"],
        schema_sha256=source_bindings["v3_schema"]["sha256"],
        data_bundle_version=source_bindings["data_bundle"]["version"],
        data_bundle_sha256=source_bindings["data_bundle"]["sha256"],
        code_sha256=source["compatibility_code_sha256"],
        evaluation_policy_sha256=source["evaluation_policy_sha256"],
        promotion_gates_sha256=source["promotion_gates_sha256"],
        campaign_budget_sha256=source["compatibility_v3_budget_sha256"],
        partition_contract_sha256=source["partition_contract_sha256"],
        partition_contract=dict(source["partition_contract"]),
        champion_ranking_rule=source["champion_ranking_rule"],
        champion_ranking_rule_sha256=source["champion_ranking_rule_sha256"],
        budget=budget,
        protected_final_roots=tuple(
            (root / item).resolve() for item in source["protected_final_roots"]),
        synthetic=False,
        incident_amendment_path=root / AMENDMENT_PATH,
        incident_amendment_sha256=bindings["amendment"]["amendment"]["sha256"],
        incident_amendment_id="v4-1-continuation-amendment",
        continuation_import_state_path=root / IMPORT_MANIFEST_PATH,
        continuation_import_state_sha256=bindings["import_manifest"]["sha256"],
        source_campaign_id=SOURCE_CAMPAIGN_ID,
    )


def issue_v4_1_continuation_readiness(
        root: Path | str, campaign_id: str, *,
        readiness_path: Path | str | None = None,
        ticket_path: Path | str | None = None,
        now: Callable[[], datetime] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Issue the sole fresh V4.1 readiness and one-use continuation ticket."""
    root = Path(root).resolve()
    if (not isinstance(campaign_id, str)
            or _IDENTIFIER.fullmatch(campaign_id) is None
            or not campaign_id.startswith("v4-1-offline-")
            or campaign_id == SOURCE_CAMPAIGN_ID):
        raise V41ReadinessError("Invalid new V4.1 continuation campaign_id")
    ready_path = _authorization_path(root, readiness_path, READINESS_PATH, "readiness")
    ticket_file = _authorization_path(root, ticket_path, TICKET_PATH, "ticket")
    claim = ticket_file.with_name(ticket_file.name + ".claimed.json")
    output = (root / OUTPUT_PARENT / campaign_id).resolve()
    if (ready_path.exists() or ticket_file.exists() or claim.exists()
            or output.exists()):
        raise V41ReadinessError(
            "V4.1 authorization identity or output was already used")
    current = (now or (lambda: datetime.now(UTC)))()
    validated = validate_v4_1_continuation_preregistration(
        root, require_unexpired=True, now=current)
    # The source process must be gone and its OS lease available.  The lock
    # file itself is deliberately excluded from the immutable evidence tree.
    with _CampaignExecutionLease(root / SOURCE_CAMPAIGN_PATH):
        pass
    created = current.astimezone(UTC).isoformat()
    source = _read(root / SOURCE_READINESS_PATH, "source V4 readiness")
    bindings = {
        "source_readiness": {
            "path": SOURCE_READINESS_PATH.as_posix(),
            "sha256": SOURCE_READINESS_SHA256},
        "source_ticket": {"path": SOURCE_TICKET_PATH.as_posix(),
                          "sha256": SOURCE_TICKET_SHA256},
        "source_claim": {"path": SOURCE_CLAIM_PATH.as_posix(),
                         "sha256": SOURCE_CLAIM_SHA256},
        "source_recovery": {"path": SOURCE_RECOVERY_PATH.as_posix(),
                            "sha256": SOURCE_RECOVERY_FILE_SHA256,
                            "state_sha256": SOURCE_RECOVERY_STATE_SHA256},
        "import_manifest": validated["import_manifest"],
        "amendment": validated["amendment"],
        "v4_1_code_inventory": validated["v4_1_code_inventory"],
        "v4_1_code_sha256": validated["v4_1_code_sha256"],
        "candidate_reverification": validated["candidate_reverification"],
        "runtime": validated["runtime"],
    }
    readiness = {
        "readiness_version": READINESS_VERSION, "status": READY_STATUS,
        "synthetic": False, "campaign_id": campaign_id,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "new_campaign_identity": True, "continuation_only": True,
        "one_use_ticket_required": True, "historical_only": True,
        "network_permitted": False, "protected_final_access_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False, "actual_orders_placed": False,
        "budget_reset_permitted": False, "counter_reset_permitted": False,
        "retry_reset_permitted": False, "deadline_reset_permitted": False,
        "original_started_at_utc": ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "original_maximum_wall_seconds": ORIGINAL_MAXIMUM_WALL_SECONDS,
        "counter_floors": dict(COUNTER_FLOORS),
        "remaining_ceilings": dict(REMAINING_CEILINGS),
        "exhausted_task_id": EXHAUSTED_TASK_ID,
        "reconciled_call_number": RECONCILED_CALL_NUMBER,
        "reconciled_completed_at_utc": RECONCILED_COMPLETED_AT_UTC,
        "source_scientific_contract_sha256": canonical_hash({
            "evaluation_policy_sha256": source["evaluation_policy_sha256"],
            "promotion_gates_sha256": source["promotion_gates_sha256"],
            "partition_contract_sha256": source["partition_contract_sha256"],
            "champion_ranking_rule_sha256": source[
                "champion_ranking_rule_sha256"],
            "campaign_budget_sha256": source[
                "registered_campaign_budget_sha256"],
        }),
        "bindings": bindings, "created_at_utc": created,
    }
    _write_exclusive(ready_path, readiness, "readiness")
    ticket = {
        "ticket_version": TICKET_VERSION, "status": "ACTIVE",
        "synthetic": False, "one_use": True,
        "campaign_id": campaign_id, "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "readiness_path": READINESS_PATH.as_posix(),
        "readiness_sha256": sha256_file(ready_path),
        "import_manifest_sha256": validated["import_manifest"]["sha256"],
        "import_record_sha256": validated["import_manifest"]["import_sha256"],
        "v4_1_code_sha256": validated["v4_1_code_sha256"],
        "candidate_reverification_sha256": validated[
            "candidate_reverification"]["reverification_sha256"],
        "amendment_sha256": validated["amendment"]["amendment"]["sha256"],
        "contract_sha256": validated["amendment"]["contract"]["sha256"],
        "source_recovery_file_sha256": SOURCE_RECOVERY_FILE_SHA256,
        "source_recovery_state_sha256": SOURCE_RECOVERY_STATE_SHA256,
        "original_started_at_utc": ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "original_maximum_wall_seconds": ORIGINAL_MAXIMUM_WALL_SECONDS,
        "counter_floors_sha256": canonical_hash(COUNTER_FLOORS),
        "remaining_ceilings_sha256": canonical_hash(REMAINING_CEILINGS),
        "budget_reset_permitted": False, "counter_reset_permitted": False,
        "retry_reset_permitted": False, "deadline_reset_permitted": False,
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False, "actual_orders_placed": False,
        "issued_at_utc": created,
    }
    try:
        _write_exclusive(ticket_file, ticket, "ticket")
    except BaseException:
        ready_path.unlink(missing_ok=True)
        raise
    return readiness, ticket


def load_v4_1_campaign_authorization(
        root: Path | str, readiness: Path | str, ticket: Path | str,
        allow_existing_claim: bool = False) -> V41ContinuationAuthorization:
    """Revalidate and load a fresh or already-claimed V4.1 continuation."""
    root = Path(root).resolve()
    ready_path = _authorization_path(root, readiness, READINESS_PATH, "readiness")
    ticket_path = _authorization_path(root, ticket, TICKET_PATH, "ticket")
    ready = _read(ready_path, "V4.1 readiness")
    ticket_value = _read(ticket_path, "V4.1 ticket")
    validated = validate_v4_1_continuation_preregistration(
        root, require_unexpired=True)
    campaign_id = ready.get("campaign_id")
    if (not isinstance(campaign_id, str)
            or not campaign_id.startswith("v4-1-offline-")
            or campaign_id == SOURCE_CAMPAIGN_ID):
        raise V41ReadinessError("V4.1 readiness campaign identity differs")
    claim_path = ticket_path.with_name(ticket_path.name + ".claimed.json")
    if claim_path.exists() and not allow_existing_claim:
        raise V41ReadinessError("V4.1 one-use ticket has already been claimed")
    if allow_existing_claim and not claim_path.is_file():
        raise V41ReadinessError("V4.1 resume requires its existing claim")

    source = _read(root / SOURCE_READINESS_PATH, "source V4 readiness")
    expected_bindings = {
        "source_readiness": {"path": SOURCE_READINESS_PATH.as_posix(),
                             "sha256": SOURCE_READINESS_SHA256},
        "source_ticket": {"path": SOURCE_TICKET_PATH.as_posix(),
                          "sha256": SOURCE_TICKET_SHA256},
        "source_claim": {"path": SOURCE_CLAIM_PATH.as_posix(),
                         "sha256": SOURCE_CLAIM_SHA256},
        "source_recovery": {"path": SOURCE_RECOVERY_PATH.as_posix(),
                            "sha256": SOURCE_RECOVERY_FILE_SHA256,
                            "state_sha256": SOURCE_RECOVERY_STATE_SHA256},
        "import_manifest": validated["import_manifest"],
        "amendment": validated["amendment"],
        "v4_1_code_inventory": validated["v4_1_code_inventory"],
        "v4_1_code_sha256": validated["v4_1_code_sha256"],
        "candidate_reverification": validated["candidate_reverification"],
        "runtime": validated["runtime"],
    }
    ready_expected = {
        "readiness_version": READINESS_VERSION, "status": READY_STATUS,
        "synthetic": False, "campaign_id": campaign_id,
        "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "new_campaign_identity": True, "continuation_only": True,
        "one_use_ticket_required": True, "historical_only": True,
        "network_permitted": False, "protected_final_access_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False, "actual_orders_placed": False,
        "budget_reset_permitted": False, "counter_reset_permitted": False,
        "retry_reset_permitted": False, "deadline_reset_permitted": False,
        "original_started_at_utc": ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "original_maximum_wall_seconds": ORIGINAL_MAXIMUM_WALL_SECONDS,
        "counter_floors": dict(COUNTER_FLOORS),
        "remaining_ceilings": dict(REMAINING_CEILINGS),
        "exhausted_task_id": EXHAUSTED_TASK_ID,
        "reconciled_call_number": RECONCILED_CALL_NUMBER,
        "reconciled_completed_at_utc": RECONCILED_COMPLETED_AT_UTC,
        "source_scientific_contract_sha256": canonical_hash({
            "evaluation_policy_sha256": source["evaluation_policy_sha256"],
            "promotion_gates_sha256": source["promotion_gates_sha256"],
            "partition_contract_sha256": source["partition_contract_sha256"],
            "champion_ranking_rule_sha256": source[
                "champion_ranking_rule_sha256"],
            "campaign_budget_sha256": source[
                "registered_campaign_budget_sha256"],
        }),
        "bindings": expected_bindings,
        "created_at_utc": ready.get("created_at_utc"),
    }
    _timestamp(ready.get("created_at_utc"), "V4.1 readiness creation")
    if ready != ready_expected:
        raise V41ReadinessError("V4.1 readiness fields or bindings differ")
    ticket_expected = {
        "ticket_version": TICKET_VERSION, "status": "ACTIVE",
        "synthetic": False, "one_use": True,
        "campaign_id": campaign_id, "source_campaign_id": SOURCE_CAMPAIGN_ID,
        "readiness_path": READINESS_PATH.as_posix(),
        "readiness_sha256": sha256_file(ready_path),
        "import_manifest_sha256": validated["import_manifest"]["sha256"],
        "import_record_sha256": validated["import_manifest"]["import_sha256"],
        "v4_1_code_sha256": validated["v4_1_code_sha256"],
        "candidate_reverification_sha256": validated[
            "candidate_reverification"]["reverification_sha256"],
        "amendment_sha256": validated["amendment"]["amendment"]["sha256"],
        "contract_sha256": validated["amendment"]["contract"]["sha256"],
        "source_recovery_file_sha256": SOURCE_RECOVERY_FILE_SHA256,
        "source_recovery_state_sha256": SOURCE_RECOVERY_STATE_SHA256,
        "original_started_at_utc": ORIGINAL_STARTED_AT_UTC,
        "absolute_deadline_at_utc": ABSOLUTE_DEADLINE_AT_UTC,
        "original_maximum_wall_seconds": ORIGINAL_MAXIMUM_WALL_SECONDS,
        "counter_floors_sha256": canonical_hash(COUNTER_FLOORS),
        "remaining_ceilings_sha256": canonical_hash(REMAINING_CEILINGS),
        "budget_reset_permitted": False, "counter_reset_permitted": False,
        "retry_reset_permitted": False, "deadline_reset_permitted": False,
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 0,
        "actual_orders_authorized": False, "actual_orders_placed": False,
        "issued_at_utc": ready["created_at_utc"],
    }
    if ticket_value != ticket_expected:
        raise V41ReadinessError("V4.1 ticket differs from readiness")
    if allow_existing_claim:
        claim = _read(claim_path, "V4.1 ticket claim")
        if claim != {
                "claim_version": "klax-v3-ticket-claim-v1",
                "campaign_id": campaign_id,
                "ticket_sha256": sha256_file(ticket_path),
                "readiness_sha256": sha256_file(ready_path),
                "synthetic": False}:
            raise V41ReadinessError("V4.1 ticket claim differs")
    authorization = _compatibility_authorization(
        root, campaign_id, ready_path, ticket_path, ready, ticket_value,
        expected_bindings)
    return V41ContinuationAuthorization(
        authorization=authorization,
        source_campaign_id=SOURCE_CAMPAIGN_ID,
        import_manifest_path=root / IMPORT_MANIFEST_PATH,
        import_manifest_sha256=validated["import_manifest"]["sha256"],
        source_recovery_path=root / SOURCE_RECOVERY_PATH,
        source_recovery_file_sha256=SOURCE_RECOVERY_FILE_SHA256,
        source_recovery_state_sha256=SOURCE_RECOVERY_STATE_SHA256,
        original_started_at_utc=ORIGINAL_STARTED_AT_UTC,
        absolute_deadline_at_utc=ABSOLUTE_DEADLINE_AT_UTC,
        counter_floors=dict(COUNTER_FLOORS),
        remaining_ceilings=dict(REMAINING_CEILINGS),
        exhausted_task_id=EXHAUSTED_TASK_ID,
        reconciled_call_number=RECONCILED_CALL_NUMBER,
        reconciled_completed_at_utc=RECONCILED_COMPLETED_AT_UTC,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "issue", "load"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id")
    parser.add_argument("--readiness-path", type=Path)
    parser.add_argument("--ticket-path", type=Path)
    parser.add_argument("--allow-existing-claim", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        value = validate_v4_1_continuation_preregistration(args.root)
    elif args.command == "issue":
        if not args.campaign_id:
            raise V41ReadinessError("--campaign-id is required for issue")
        readiness, ticket = issue_v4_1_continuation_readiness(
            args.root, args.campaign_id, readiness_path=args.readiness_path,
            ticket_path=args.ticket_path)
        value = {"readiness": readiness, "ticket": ticket}
    else:
        value = load_v4_1_campaign_authorization(
            args.root, args.readiness_path or READINESS_PATH,
            args.ticket_path or TICKET_PATH,
            allow_existing_claim=args.allow_existing_claim)
        value = {
            "campaign_id": value.authorization.campaign_id,
            "source_campaign_id": value.source_campaign_id,
            "absolute_deadline_at_utc": value.absolute_deadline_at_utc,
            "counter_floors": value.counter_floors,
            "protected_final_authorized": False,
            "actual_orders_authorized": False,
        }
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
