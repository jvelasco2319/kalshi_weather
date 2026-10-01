"""Substantive, fail-closed readiness checks for the V3 offline campaign.

The registration progress report in :mod:`klax_lab.v3_readiness` intentionally
checks presence only.  This module performs the separate, expensive checks that
may issue a one-use development-campaign ticket.  It never runs a campaign and
never authorizes or reads the protected final interval.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from .campaign_v3 import (
    CANDIDATE_ARTIFACTS, CHAMPION_RANKING_RULE, CRITIC_CHECKS, CRITIC_VERSION,
    READINESS_VERSION, READY_STATUS, REPLICATION_CHECKS, REPLICATION_VERSION,
    TICKET_VERSION, CandidateRecord, validate_v3_critic, validate_v3_replication,
)
from .component_fixtures_v3 import verify_component_fixtures
from .dataset_v3 import verify_frozen_development_dataset
from .incident_amendment_v3 import (
    V3IncidentAmendmentError,
    verify_campaign1_integrity_repair_amendment,
)
from .incident_packet_amendment_v3 import (
    AMENDMENT_PATH as PACKET_REPAIR_AMENDMENT_PATH,
    V3PacketIncidentAmendmentError,
    verify_campaign2_packet_repair_amendment,
)
from .provenance import canonical_hash, sha256_file, write_json
from .research_plan_v3 import make_plan_v3
from .research_protocol_v3 import (
    PROTOCOL_V3, V3ResearchProtocolError, parse_v3_worker_proposal,
    validate_v3_worker_packet, v3_packet_sha256,
)
from .v3_readiness import REQUIRED_COMPONENT_MANIFESTS, validate_v3_registration
from .weather_normalize_v3 import (
    POLICY_AMENDMENT_PATH,
    POLICY_AMENDMENT_SHA256,
    POLICY_CONFIG_PATH,
    POLICY_CONFIG_SHA256,
    PRE_AMENDMENT_PROGRESS_PATH,
    PRE_AMENDMENT_PROGRESS_SHA256,
    availability_policy_record,
    verify_availability_policy_registration,
)


UTC = timezone.utc
READINESS_PATH = Path("data/manifests/v3_readiness.json")
TICKET_PATH = Path("runs/v3_offline_campaign_ticket.json")
SOURCE_PATH = Path("data/manifests/v3_source_feasibility.json")
BULK_PROGRESS_PATH = Path("data/manifests/v3_weather_bulk_progress.json")
NORMALIZATION_PROGRESS_PATH = Path("data/manifests/v3_weather_normalization_progress.json")
ACQUISITION_ATTEMPTS_PATH = Path("data/manifests/v3_weather_acquisition_attempts.json")
PRE_COMPLETION_CORRECTION_ACQUISITION_ATTEMPTS_PATH = Path(
    "data/manifests/v3_weather_acquisition_attempts.pre-completion-correction.json")
COMPLETION_METRICS_CORRECTION_PATH = Path(
    "data/manifests/v3_weather_acquisition_attempt4_completion_correction.json")
PRE_AMENDMENT_ACQUISITION_ATTEMPTS_PATH = Path(
    "data/manifests/v3_weather_acquisition_attempts.pre-amendment.json")
PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH = Path(
    "data/manifests/v3_weather_acquisition_pretransfer_environment_amendment.json")
REPLICATION_PATH = Path("data/manifests/v3_replication.json")
CRITIC_PATH = Path("data/manifests/v3_critic.json")
WORKER_PATH = Path("data/manifests/v3_worker_probe.json")
PROTECTED_FINAL_ROOTS = ("data/protected_final",)
SOURCE_COMPLETE_STATUS = "COMPLETE_FINITE_BULK_AND_NORMALIZED_COVERAGE_AUDITED"
WEATHER_COMPLETE_STATUS = "COMPLETE_REGISTERED_COVERAGE_WITH_CONSERVATIVE_ASOF_BOUND"
PROBE_STATUS = "OFFLINE_CAPABILITY_PROBE_PASS"
ACTUAL_WORKER_STATUS = "ACTUAL_LOCAL_V3_PROTOCOL_PROBE_PASS"
EXPECTED_WEATHER_DAYS = 543
MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS = 3
SHA = re.compile(r"[0-9a-f]{64}")
COMPLETION_METRICS_CORRECTION_REASON = (
    "The terminal combined progress manifest removed the phase-local top-level "
    "new_transfer_bytes field; the authoritative completion metrics are the sums "
    "of its immutable daily records."
)


class V3SubstantiveReadinessError(ValueError):
    """A real readiness requirement is missing, malformed, or stale."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=lambda item: (_ for _ in ()).throw(
                V3SubstantiveReadinessError(f"Non-finite JSON in {label}: {item}")),
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise V3SubstantiveReadinessError(f"Missing or invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise V3SubstantiveReadinessError(f"{label} must contain one JSON object")
    return value


def _inside(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise V3SubstantiveReadinessError(f"Invalid {label} path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise V3SubstantiveReadinessError(f"{label} path escapes the project")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError(f"{label} path escapes the project") from exc
    if "protected_final" in {part.casefold().replace("-", "_") for part in path.parts}:
        raise V3SubstantiveReadinessError(f"{label} points into protected-final storage")
    return path


def _check_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise V3SubstantiveReadinessError(f"Invalid {label} SHA-256")
    return value


def _utc_timestamp(value: Any, label: str) -> datetime:
    """Parse a persisted UTC timestamp and reject offsets or naive values."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise V3SubstantiveReadinessError(f"Invalid {label} UTC timestamp")
    try:
        stamp = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise V3SubstantiveReadinessError(
            f"Invalid {label} UTC timestamp") from exc
    if stamp.utcoffset() != timedelta(0):
        raise V3SubstantiveReadinessError(f"Invalid {label} UTC timestamp")
    return stamp


def _verify_file_record(root: Path, record: Any, label: str, *, rows: bool = False) -> Path:
    required = {"path", "sha256"} | ({"bytes", "rows"} if rows else set())
    if not isinstance(record, dict) or not required <= set(record):
        raise V3SubstantiveReadinessError(f"Malformed {label} artifact record")
    path = _inside(root, record["path"], label)
    if not path.is_file() or sha256_file(path) != _check_hash(record["sha256"], label):
        raise V3SubstantiveReadinessError(f"{label} artifact is missing or changed")
    if "bytes" in record and record["bytes"] != path.stat().st_size:
        raise V3SubstantiveReadinessError(f"{label} artifact byte count differs")
    if rows and (type(record["rows"]) is not int or record["rows"] < 1):
        raise V3SubstantiveReadinessError(f"{label} artifact row count is invalid")
    return path


def _require_offline_boundary(body: dict[str, Any], label: str) -> None:
    if body.get("protected_final_read") is not False:
        raise V3SubstantiveReadinessError(f"{label} does not prove protected-final denial")
    for key in ("network_used", "network_used_for_normalization",
                "offline_cache_verification_network_used"):
        if key in body and body[key] is not False:
            raise V3SubstantiveReadinessError(f"{label} was not produced offline")
    if body.get("protected_final_access_authorized") is True:
        raise V3SubstantiveReadinessError(f"{label} authorizes protected-final access")


def _expected_dates() -> dict[str, list[str]]:
    def span(start: date, end: date) -> list[str]:
        return [(start + timedelta(days=index)).isoformat()
                for index in range((end - start).days + 1)]
    return {
        "weather_training": span(date(2024, 1, 1), date(2024, 12, 31)),
        "selection": span(date(2025, 1, 5), date(2025, 6, 30)),
    }


def _verify_pretransfer_environment_amendment(
        root: Path, body: dict[str, Any], attempts: list[dict[str, Any]]) -> None:
    """Bind the one environment replacement to evidence saved before launch."""
    policy = body["recovery_policy"]
    if (policy.get("maximum_pretransfer_environment_replacement_launches") != 1
            or policy.get("maximum_total_process_launches")
            != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS + 1):
        raise V3SubstantiveReadinessError(
            "Acquisition environment-replacement policy differs")

    amendment_record = body.get("administrative_replacement_amendment")
    expected_amendment_path = PRETRANSFER_ENVIRONMENT_AMENDMENT_PATH.as_posix()
    if (not isinstance(amendment_record, dict)
            or set(amendment_record) != {"path", "sha256"}
            or amendment_record.get("path") != expected_amendment_path):
        raise V3SubstantiveReadinessError(
            "Acquisition environment amendment identity differs")
    amendment_path = _inside(
        root, amendment_record["path"], "acquisition environment amendment")
    amendment_hash = _check_hash(
        amendment_record["sha256"], "acquisition environment amendment")
    if not amendment_path.is_file() or sha256_file(amendment_path) != amendment_hash:
        raise V3SubstantiveReadinessError(
            "Acquisition environment amendment is missing or changed")
    amendment = _read_json(amendment_path, "acquisition environment amendment")
    expected_amendment_keys = {
        "schema_version", "component", "status", "registered_at_utc",
        "failed_attempt", "failed_pid", "failure_class", "failure_context",
        "pre_amendment_journal_sha256", "failed_stdout_sha256",
        "failed_stderr_sha256", "eligibility", "maximum_replacement_launches",
        "scope_changed", "byte_cap_changed", "protected_final_read",
        "scientific_or_evaluation_policy_changed", "rationale",
    }
    if (set(amendment) != expected_amendment_keys
            or amendment.get("schema_version") != 1
            or amendment.get("component")
            != "v3_weather_acquisition_pretransfer_environment_amendment"
            or amendment.get("status") != "REGISTERED_BEFORE_REPLACEMENT_LAUNCH"
            or amendment.get("failed_attempt") != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS
            or amendment.get("failure_class") != "PermissionError.WinError10013"
            or amendment.get("failure_context") != "local_socket_permission_denied"
            or amendment.get("eligibility") != {
                "network_requests": 0,
                "new_transfer_bytes": 0,
                "failure_before_first_new_network_request": True,
            }
            or amendment.get("maximum_replacement_launches") != 1
            or amendment.get("scope_changed") is not False
            or amendment.get("byte_cap_changed") is not False
            or amendment.get("protected_final_read") is not False
            or amendment.get("scientific_or_evaluation_policy_changed") is not False
            or not isinstance(amendment.get("rationale"), str)
            or not amendment["rationale"].strip()):
        raise V3SubstantiveReadinessError("Acquisition environment amendment differs")

    snapshot_path = root / PRE_AMENDMENT_ACQUISITION_ATTEMPTS_PATH
    snapshot_hash = _check_hash(
        amendment.get("pre_amendment_journal_sha256"),
        "pre-amendment acquisition journal")
    if not snapshot_path.is_file() or sha256_file(snapshot_path) != snapshot_hash:
        raise V3SubstantiveReadinessError(
            "Pre-amendment acquisition journal is missing or changed")
    snapshot = _read_json(snapshot_path, "pre-amendment acquisition journal")
    snapshot_attempts = snapshot.get("attempts")
    base_policy = {
        key: value for key, value in policy.items()
        if key not in {
            "maximum_pretransfer_environment_replacement_launches",
            "maximum_total_process_launches",
        }
    }
    if (snapshot.get("schema_version") != 2
            or snapshot.get("component") != body.get("component")
            or snapshot.get("registered_scope") != body.get("registered_scope")
            or snapshot.get("protected_final_read") is not False
            or snapshot.get("recovery_policy") != base_policy
            or "administrative_replacement_amendment" in snapshot
            or not isinstance(snapshot_attempts, list)
            or len(snapshot_attempts) != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS
            or any(not isinstance(item, dict) for item in snapshot_attempts)
            or [item.get("attempt") for item in snapshot_attempts]
            != list(range(1, MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS + 1))
            or len({item.get("pid") for item in snapshot_attempts})
            != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS):
        raise V3SubstantiveReadinessError(
            "Pre-amendment acquisition journal provenance differs")

    failed = snapshot_attempts[-1]
    current_failed = attempts[MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS - 1]
    replacement = attempts[MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS]
    if attempts[:MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS - 1] != snapshot_attempts[:-1]:
        raise V3SubstantiveReadinessError(
            "Pre-amendment acquisition attempts changed after registration")
    expected_current_failed = {
        **failed,
        "stdout_path": "runs/weather_v3_bulk.attempt-3.stdout.log",
        "stderr_path": "runs/weather_v3_bulk.attempt-3.stderr.log",
        "network_requests": 0,
        "new_transfer_bytes": 0,
        "administrative_replacement_eligible": True,
    }
    if (failed.get("stdout_path") != "runs/weather_v3_bulk.stdout.log"
            or failed.get("stderr_path") != "runs/weather_v3_bulk.stderr.log"
            or current_failed != expected_current_failed
            or amendment.get("failed_pid") != failed.get("pid")
            or amendment.get("failure_class") != failed.get("failure_class")
            or amendment.get("failure_context") != failed.get("failure_context")
            or amendment.get("failed_stdout_sha256") != failed.get("stdout_sha256")
            or amendment.get("failed_stderr_sha256") != failed.get("stderr_sha256")
            or replacement.get("administrative_replacement") is not True
            or replacement.get("replacement_for_attempt")
            != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS):
        raise V3SubstantiveReadinessError(
            "Acquisition environment replacement provenance differs")

    failed_started = _utc_timestamp(
        failed.get("started_at_utc"), "failed acquisition start")
    failed_ended = _utc_timestamp(
        failed.get("ended_at_utc"), "failed acquisition end")
    registered = _utc_timestamp(
        amendment.get("registered_at_utc"), "environment amendment registration")
    replacement_started = _utc_timestamp(
        replacement.get("started_at_utc"), "replacement acquisition start")
    if not failed_started < failed_ended < registered < replacement_started:
        raise V3SubstantiveReadinessError(
            "Acquisition environment replacement ordering differs")


def _verify_completion_metrics_correction(
        root: Path, body: dict[str, Any], progress: dict[str, Any],
        attempts: list[dict[str, Any]], request_sum: int, byte_sum: int) -> None:
    """Verify the hash-bound repair of the terminal attempt byte total."""
    record = body.get("completion_metrics_correction")
    expected_path = COMPLETION_METRICS_CORRECTION_PATH.as_posix()
    if (not isinstance(record, dict) or set(record) != {"path", "sha256"}
            or record.get("path") != expected_path):
        raise V3SubstantiveReadinessError(
            "Acquisition completion-metrics correction identity differs")
    correction_path = _inside(
        root, record["path"], "acquisition completion-metrics correction")
    correction_hash = _check_hash(
        record["sha256"], "acquisition completion-metrics correction")
    if (not correction_path.is_file()
            or sha256_file(correction_path) != correction_hash):
        raise V3SubstantiveReadinessError(
            "Acquisition completion-metrics correction is missing or changed")
    correction = _read_json(
        correction_path, "acquisition completion-metrics correction")
    expected_keys = {
        "schema_version", "component", "status", "registered_at_utc",
        "attempt", "pid", "pre_correction_journal",
        "authoritative_final_progress", "prior_metrics", "corrected_metrics",
        "daily_records", "reason", "scope_changed",
        "scientific_or_evaluation_policy_changed", "gate_changed",
        "protected_final_read",
    }
    final = attempts[-1]
    corrected = {
        "network_requests": request_sum,
        "new_transfer_bytes": byte_sum,
    }
    prior = {
        "network_requests": request_sum,
        "new_transfer_bytes": 0,
    }
    if (set(correction) != expected_keys
            or correction.get("schema_version") != 1
            or correction.get("component")
            != "v3_weather_acquisition_attempt4_completion_metrics_correction"
            or correction.get("status")
            != "REGISTERED_POST_COMPLETION_FROM_IMMUTABLE_DAILY_PROGRESS"
            or correction.get("attempt") != final.get("attempt")
            or correction.get("pid") != final.get("pid")
            or correction.get("prior_metrics") != prior
            or correction.get("corrected_metrics") != corrected
            or correction.get("daily_records") != {
                "rows": len(progress.get("days", [])),
                "network_requests_sum": request_sum,
                "new_transfer_bytes_sum": byte_sum,
            }
            or correction.get("reason") != COMPLETION_METRICS_CORRECTION_REASON
            or correction.get("scope_changed") is not False
            or correction.get("scientific_or_evaluation_policy_changed") is not False
            or correction.get("gate_changed") is not False
            or correction.get("protected_final_read") is not False
            or final.get("network_requests") != request_sum
            or final.get("new_transfer_bytes") != byte_sum
            or byte_sum <= 0):
        raise V3SubstantiveReadinessError(
            "Acquisition completion-metrics correction provenance differs")

    snapshot_record = correction.get("pre_correction_journal")
    progress_record = correction.get("authoritative_final_progress")
    if (not isinstance(snapshot_record, dict)
            or set(snapshot_record) != {"path", "sha256"}
            or snapshot_record.get("path")
            != PRE_COMPLETION_CORRECTION_ACQUISITION_ATTEMPTS_PATH.as_posix()
            or not isinstance(progress_record, dict)
            or set(progress_record) != {"path", "sha256"}
            or progress_record.get("path") != BULK_PROGRESS_PATH.as_posix()):
        raise V3SubstantiveReadinessError(
            "Acquisition completion-metrics correction evidence identity differs")
    snapshot_path = _inside(
        root, snapshot_record["path"], "pre-correction acquisition journal")
    snapshot_hash = _check_hash(
        snapshot_record["sha256"], "pre-correction acquisition journal")
    if not snapshot_path.is_file() or sha256_file(snapshot_path) != snapshot_hash:
        raise V3SubstantiveReadinessError(
            "Pre-correction acquisition journal is missing or changed")
    progress_path = _inside(
        root, progress_record["path"], "authoritative final progress")
    progress_hash = _check_hash(
        progress_record["sha256"], "authoritative final progress")
    if (not progress_path.is_file() or sha256_file(progress_path) != progress_hash
            or _read_json(progress_path, "authoritative final progress") != progress):
        raise V3SubstantiveReadinessError(
            "Authoritative acquisition progress is missing or changed")

    snapshot = _read_json(snapshot_path, "pre-correction acquisition journal")
    reconstructed = deepcopy(body)
    reconstructed.pop("completion_metrics_correction", None)
    reconstructed_attempts = reconstructed.get("attempts")
    if not isinstance(reconstructed_attempts, list) or not reconstructed_attempts:
        raise V3SubstantiveReadinessError(
            "Pre-correction acquisition journal provenance differs")
    reconstructed_attempts[-1]["network_requests"] = prior["network_requests"]
    reconstructed_attempts[-1]["new_transfer_bytes"] = prior["new_transfer_bytes"]
    if reconstructed != snapshot:
        raise V3SubstantiveReadinessError(
            "Pre-correction acquisition journal provenance differs")

    ended = _utc_timestamp(
        snapshot["attempts"][-1].get("ended_at_utc"),
        "terminal acquisition completion")
    registered = _utc_timestamp(
        correction.get("registered_at_utc"),
        "completion-metrics correction registration")
    if not ended < registered:
        raise V3SubstantiveReadinessError(
            "Acquisition completion-metrics correction ordering differs")


def _verify_acquisition_attempt_journal(
        root: Path, progress: dict[str, Any]) -> dict[str, Any]:
    """Verify bounded process recovery and every terminal attempt log."""
    path = root / ACQUISITION_ATTEMPTS_PATH
    body = _read_json(path, "V3 weather acquisition-attempt journal")
    schema_version = body.get("schema_version")
    if (schema_version not in {2, 3}
            or body.get("component") != "registered_weather_acquisition_attempt_journal"
            or body.get("protected_final_read") is not False
            or body.get("registered_scope") != {
                "start_date": "2024-01-01", "end_date": "2025-06-30",
                "registered_days": EXPECTED_WEATHER_DAYS,
                "max_total_source_bytes": 35_000_000_000,
            }):
        raise V3SubstantiveReadinessError("Acquisition-attempt scope or boundary differs")
    policy = body.get("recovery_policy")
    retryable = {
        "requests.exceptions.ReadTimeout", "requests.exceptions.ConnectTimeout",
        "requests.exceptions.ConnectionError", "requests.exceptions.ChunkedEncodingError",
        "urllib3.exceptions.ReadTimeoutError", "urllib3.exceptions.ProtocolError",
        "urllib3.exceptions.NewConnectionError",
    }
    if (not isinstance(policy, dict)
            or policy.get("maximum_process_attempts") != MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS
            or set(policy.get("restartable_failures", [])) != retryable
            or policy.get("nontransient_failures_are_terminal") is not True
            or policy.get("scope_change_allowed") is not False
            or policy.get("byte_cap_change_allowed") is not False
            or policy.get("protected_final_read_allowed") is not False):
        raise V3SubstantiveReadinessError("Acquisition recovery policy differs")
    maximum_launches = MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS
    if schema_version == 3:
        maximum_launches += 1
    attempts = body.get("attempts")
    if (not isinstance(attempts, list) or not 1 <= len(attempts) <= maximum_launches
            or [item.get("attempt") for item in attempts] != list(range(1, len(attempts) + 1))
            or len({item.get("pid") for item in attempts}) != len(attempts)
            or any(type(item.get("pid")) is not int or item["pid"] <= 0 for item in attempts)):
        raise V3SubstantiveReadinessError("Acquisition process-attempt inventory differs")
    if schema_version == 3 and len(attempts) != maximum_launches:
        raise V3SubstantiveReadinessError(
            "Acquisition environment replacement inventory differs")
    if len(attempts) > MAXIMUM_ACQUISITION_PROCESS_ATTEMPTS:
        if schema_version != 3:
            raise V3SubstantiveReadinessError(
                "Acquisition environment replacement is not eligible")
        _verify_pretransfer_environment_amendment(root, body, attempts)
    days = progress.get("days")
    if not isinstance(days, list) or not days:
        raise V3SubstantiveReadinessError(
            "Acquisition progress completion metrics are missing")
    for day in days:
        if (not isinstance(day, dict)
                or type(day.get("network_requests")) is not int
                or day["network_requests"] < 0
                or type(day.get("new_transfer_bytes")) is not int
                or day["new_transfer_bytes"] < 0):
            raise V3SubstantiveReadinessError(
                "Acquisition progress completion metrics are invalid")
    request_sum = sum(day["network_requests"] for day in days)
    byte_sum = sum(day["new_transfer_bytes"] for day in days)
    for index, attempt in enumerate(attempts):
        final = index == len(attempts) - 1
        terminal = attempt.get("terminal_status")
        if final:
            if (terminal != "COMPLETE_543_DAYS_CACHE_VERIFIED"
                    or attempt.get("committed_days") != EXPECTED_WEATHER_DAYS
                    or attempt.get("last_committed_date") != "2025-06-30"
                    or attempt.get("network_requests") != request_sum
                    or attempt.get("new_transfer_bytes") != byte_sum):
                raise V3SubstantiveReadinessError("Final acquisition process attempt is not complete")
        elif terminal not in {"TRANSIENT_READ_TIMEOUT", "TRANSIENT_NETWORK_FAILURE"}:
            raise V3SubstantiveReadinessError("Nonfinal acquisition process attempt is not a bounded transient failure")
        for key in ("stdout", "stderr"):
            log_path = _inside(root, attempt.get(f"{key}_path"), f"acquisition attempt {key}")
            expected_hash = _check_hash(
                attempt.get(f"{key}_sha256"), f"acquisition attempt {key}")
            if not log_path.is_file() or sha256_file(log_path) != expected_hash:
                raise V3SubstantiveReadinessError("Acquisition process-attempt log is missing or changed")
    if (progress.get("days_complete") != attempts[-1].get("committed_days")
            or progress.get("days", [])[-1].get("date") != attempts[-1].get("last_committed_date")):
        raise V3SubstantiveReadinessError("Acquisition journal and terminal coverage differ")
    if "completion_metrics_correction" in body:
        _verify_completion_metrics_correction(
            root, body, progress, attempts, request_sum, byte_sum)
    return body


def _verify_weather_component(root: Path, component: str) -> dict[str, Any]:
    requirement = next(item for item in REQUIRED_COMPONENT_MANIFESTS
                       if item.component == component)
    path = root / requirement.path
    body = _read_json(path, component)
    _require_offline_boundary(body, component)
    if (body.get("schema_version") != 1 or body.get("component") != component
            or body.get("status") != WEATHER_COMPLETE_STATUS
            or body.get("readiness_component_pass") is not True
            or body.get("registered_days") != EXPECTED_WEATHER_DAYS
            or body.get("historical_publication_time_proven") is not False):
        raise V3SubstantiveReadinessError(f"{component} completion schema differs")
    policy = body.get("availability_policy")
    try:
        registered_policy = verify_availability_policy_registration(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError(
            f"{component} availability registration differs") from exc
    if not isinstance(policy, dict) or policy != registered_policy:
        raise V3SubstantiveReadinessError(f"{component} availability policy differs")
    expected = _expected_dates()
    partitions = body.get("partitions")
    if (not isinstance(partitions, dict) or set(partitions) != set(expected)
            or any(partitions[name] != {"days": len(days), "dates": days}
                   for name, days in expected.items())):
        raise V3SubstantiveReadinessError(f"{component} partition coverage differs")

    output_records: list[dict[str, Any]]
    if component == "hrrr_local_observations":
        hrrr = body.get("hrrr")
        local = body.get("local_observations")
        if (not isinstance(hrrr, dict) or hrrr.get("coverage_complete") is not True
                or hrrr.get("required_rows_per_day") != 24
                or hrrr.get("rows") != EXPECTED_WEATHER_DAYS * 24
                or not isinstance(local, dict)
                or local.get("substantively_complete") is not True
                or local.get("historical_receipt_time_proven") is not False
                or local.get("nearby_stations_optional_and_not_required_for_complete_klax_schedule")
                is not True):
            raise V3SubstantiveReadinessError("HRRR/local-observation coverage differs")
        source = local.get("source_manifest")
        _verify_file_record(root, source, "local observation source manifest")
        for record in local.get("outputs", []):
            _verify_file_record(root, record, "local observation output", rows=True)
        if ({item.get("partition") for item in local.get("outputs", [])} != set(expected)
                or len({item.get("path") for item in local.get("outputs", [])}) != len(expected)):
            raise V3SubstantiveReadinessError("Local observation partition inventory differs")
        output_records = hrrr.get("outputs", [])
        required_model, required_rows = "hrrr", 24
    else:
        if (body.get("coverage_complete") is not True
                or body.get("uncertainty_representation")
                != "archived_GEFS_30_member_mean_and_standard_deviation"
                or body.get("member_level_distribution_retained") is not False
                or body.get("required_rows_per_day") != 16
                or body.get("rows") != EXPECTED_WEATHER_DAYS * 16):
            raise V3SubstantiveReadinessError("GEFS coverage or evidence mode differs")
        output_records = body.get("outputs", [])
        required_model, required_rows = "gefs", 16
    if not isinstance(output_records, list) or len(output_records) != EXPECTED_WEATHER_DAYS:
        raise V3SubstantiveReadinessError(f"{component} output inventory is incomplete")
    expected_outputs = []
    filename = "hrrr_points.parquet" if required_model == "hrrr" else "gefs_summary_points.parquet"
    for partition, dates in expected.items():
        expected_outputs.extend(
            f"data/normalized/v3_weather/{partition}/date={day}/{filename}" for day in dates)
    for record, expected_path in zip(output_records, expected_outputs, strict=True):
        if (record.get("model") != required_model or record.get("rows") != required_rows
                or record.get("path") != expected_path):
            raise V3SubstantiveReadinessError(f"{component} daily output schema differs")
        _verify_file_record(root, record, f"{component} daily output", rows=True)
    return body


def finalize_v3_source_feasibility(root: Path) -> dict[str, Any]:
    """Finalize source feasibility only after all 543 raw and normalized days pass.

    The original planning and pilot evidence is retained.  The function is
    deterministic and idempotent for the same complete source artifacts.
    """
    root = Path(root).resolve()
    source_path = root / SOURCE_PATH
    source = _read_json(source_path, "V3 source-feasibility manifest")
    prior = source.get("completion_audit")
    prior_status = (prior.get("prior_status") if isinstance(prior, dict)
                    else source.get("status"))
    base = deepcopy(source)
    base.pop("completion_audit", None)
    base.pop("readiness_component_pass", None)
    base["status"] = prior_status
    if (base.get("component") != "source_feasibility"
            or base.get("protected_final_read") is not False
            or base.get("revised_bulk_plan", {}).get("training_and_development_days")
            != EXPECTED_WEATHER_DAYS
            or base.get("revised_bulk_plan", {}).get("transfer_cap_bytes") != 35_000_000_000):
        raise V3SubstantiveReadinessError("Registered source-feasibility plan differs")

    progress_path = root / BULK_PROGRESS_PATH
    progress = _read_json(progress_path, "V3 bulk-weather progress")
    expected = _expected_dates()
    expected_flat = expected["weather_training"] + expected["selection"]
    days = progress.get("days")
    if (progress.get("schema_version") != 1
            or progress.get("component") != "revised_weather_raw_history"
            or progress.get("status") != "FULL_RAW_RANGE_ACQUISITION_COMPLETE"
            or progress.get("coverage_complete") is not True
            or progress.get("protected_final_read") is not False
            or progress.get("days_attempted") != EXPECTED_WEATHER_DAYS
            or progress.get("days_complete") != EXPECTED_WEATHER_DAYS
            or progress.get("days_unavailable") != 0
            or progress.get("unregistered_gap_dates_acquired") is not False
            or progress.get("partitions") != {
                "weather_training": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
                "selection": {"start_date": "2025-01-05", "end_date": "2025-06-30"},
            }
            or not isinstance(days, list) or len(days) != EXPECTED_WEATHER_DAYS
            or [item.get("date") for item in days] != expected_flat
            or any(item.get("status") != "RAW_RANGES_CACHE_VERIFIED"
                   or item.get("hrrr_objects") != 4 or item.get("gefs_objects") != 16
                   for item in days)):
        raise V3SubstantiveReadinessError("Raw weather history is not exact complete 543-day coverage")
    total_bytes = progress.get("total_source_bytes_after")
    cap = progress.get("max_total_source_bytes")
    if (type(total_bytes) is not int or total_bytes <= 0 or type(cap) is not int
            or cap != 35_000_000_000 or total_bytes > cap):
        raise V3SubstantiveReadinessError("Raw weather byte cap or actual bytes differ")
    requests = sum(item["network_requests"] for item in days
                   if type(item.get("network_requests")) is int)
    if requests < 0:
        raise V3SubstantiveReadinessError("Raw weather request count is invalid")
    attempt_journal = _verify_acquisition_attempt_journal(root, progress)

    try:
        registered_weather_policy = verify_availability_policy_registration(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError(
            "Weather availability registration is missing or changed") from exc

    normalized_path = root / NORMALIZATION_PROGRESS_PATH
    normalized = _read_json(normalized_path, "V3 weather-normalization progress")
    normalization_days = normalized.get("days")
    migration_identity = ([{
        "date": item.get("date"),
        "migrated_from_v1": item.get("migrated_from_v1"),
        "manifest_path": (item.get("manifest") or {}).get("path"),
        "manifest_sha256": (item.get("manifest") or {}).get("sha256"),
    } for item in normalization_days] if isinstance(normalization_days, list) else [])
    if (normalized.get("status") != "COMPLETE_COMPONENTS_PUBLISHED"
            or normalized.get("coverage_complete") is not True
            or normalized.get("normalized_days") != EXPECTED_WEATHER_DAYS
            or normalized.get("missing_raw_cache_days") != 0
            or normalized.get("integrity_failure_days") != 0
            or normalized.get("network_used") is not False
            or normalized.get("protected_final_read") is not False
            or normalized.get("availability_policy") != registered_weather_policy
            or normalized.get("migrated_verified_v1_days") != 542
            or normalized.get("fresh_v2_normalized_dates") != ["2025-06-28"]
            or len(migration_identity) != EXPECTED_WEATHER_DAYS
            or [item["date"] for item in migration_identity] != expected_flat
            or sum(item["migrated_from_v1"] is True for item in migration_identity) != 542
            or [item["date"] for item in migration_identity
                if item["migrated_from_v1"] is not True] != ["2025-06-28"]
            or normalized.get("readiness_component_manifests_published") is not True
            or normalized.get("published_component_manifests") != [
                "data/manifests/v3_hrrr_local_observations.json",
                "data/manifests/v3_gefs.json",
            ]):
        raise V3SubstantiveReadinessError("Weather normalization is not complete and offline")
    for item in normalization_days:
        expected_manifest = (
            f"data/normalized/v3_weather/{item.get('partition')}/"
            f"date={item.get('date')}/normalization_manifest.json")
        if (not isinstance(item.get("manifest"), dict)
                or item["manifest"].get("path") != expected_manifest):
            raise V3SubstantiveReadinessError(
                "Weather normalization daily manifest inventory differs")
        _verify_file_record(root, item["manifest"], "weather normalization daily manifest")
    hrrr = _verify_weather_component(root, "hrrr_local_observations")
    gefs = _verify_weather_component(root, "gefs")
    coverage_identity = [{
        "date": item["date"], "status": item["status"],
        "hrrr_objects": item["hrrr_objects"], "gefs_objects": item["gefs_objects"],
    } for item in days]
    audit = {
        "prior_status": prior_status,
        "source_plan_sha256": canonical_hash(base),
        "bulk_progress_path": BULK_PROGRESS_PATH.as_posix(),
        "bulk_progress_sha256": sha256_file(progress_path),
        "normalization_progress_path": NORMALIZATION_PROGRESS_PATH.as_posix(),
        "normalization_progress_sha256": sha256_file(normalized_path),
        "acquisition_attempts_path": ACQUISITION_ATTEMPTS_PATH.as_posix(),
        "acquisition_attempts_sha256": sha256_file(root / ACQUISITION_ATTEMPTS_PATH),
        "acquisition_process_attempt_count": len(attempt_journal["attempts"]),
        "acquisition_process_attempt_statuses": [
            item["terminal_status"] for item in attempt_journal["attempts"]],
        "hrrr_component_sha256": sha256_file(
            root / "data/manifests/v3_hrrr_local_observations.json"),
        "gefs_component_sha256": sha256_file(root / "data/manifests/v3_gefs.json"),
        "weather_availability_policy_config_path": POLICY_CONFIG_PATH.as_posix(),
        "weather_availability_policy_config_sha256": POLICY_CONFIG_SHA256,
        "weather_availability_amendment_path": POLICY_AMENDMENT_PATH.as_posix(),
        "weather_availability_amendment_sha256": POLICY_AMENDMENT_SHA256,
        "pre_amendment_normalization_snapshot_path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
        "pre_amendment_normalization_snapshot_sha256": PRE_AMENDMENT_PROGRESS_SHA256,
        "migrated_verified_v1_days": 542,
        "fresh_v2_normalized_dates": ["2025-06-28"],
        "weather_policy_migration_identity_sha256": canonical_hash(migration_identity),
        "coverage_sha256": canonical_hash(coverage_identity),
        "days_complete": EXPECTED_WEATHER_DAYS,
        "days_unavailable": 0,
        "raw_source_bytes": total_bytes,
        "network_requests": requests,
        "offline_normalization_verified": True,
        "protected_final_read": False,
        "hrrr_rows": hrrr["hrrr"]["rows"],
        "gefs_rows": gefs["rows"],
    }
    result = {
        **base,
        "status": SOURCE_COMPLETE_STATUS,
        "readiness_component_pass": True,
        "completion_audit": audit,
    }
    write_json(source_path, result)
    return result


def _probe_plan():
    return make_plan_v3(
        colony="adversarial_alternatives", stage="probability_calibration",
        data_bundle_version="v3-capability-fixture", data_bundle_sha256="a" * 64,
    )


def _probe_record() -> CandidateRecord:
    artifacts = {name: canonical_hash({"fixture_artifact": name})
                 for name in sorted(CANDIDATE_ARTIFACTS)}
    return CandidateRecord(
        candidate_id="v3-capability-fixture", plan=_probe_plan(),
        discovery_worker_id="fixture-discovery", epoch=1,
        artifact_sha256s=artifacts,
    )


def _primary_numerical_probe(rows: Iterable[dict[str, int]]) -> dict[str, Any]:
    values = list(rows)
    outlay = sum(Decimal(row["entry_cents"]) / 100 for row in values)
    fees = sum(Decimal(row["fee_cents"]) / 100 for row in values)
    payout = sum(Decimal(row["payout_cents"]) / 100 for row in values)
    fold_profit: dict[int, Decimal] = {}
    fold_outlay: dict[int, Decimal] = {}
    for row in values:
        fold = row["fold"]
        fold_profit[fold] = fold_profit.get(fold, Decimal(0)) + Decimal(
            row["payout_cents"] - row["entry_cents"] - row["fee_cents"]) / 100
        fold_outlay[fold] = fold_outlay.get(fold, Decimal(0)) + Decimal(row["entry_cents"]) / 100
    return {
        "selected_opportunities": len(values),
        "entry_outlay": str(outlay), "fees": str(fees), "payouts": str(payout),
        "net_profit": str(payout - outlay - fees),
        "capital_weighted_return": str((payout - outlay - fees) / outlay),
        "fold_returns": {str(key): str(fold_profit[key] / fold_outlay[key])
                         for key in sorted(fold_profit)},
    }


def _independent_numerical_probe(rows: Iterable[dict[str, int]]) -> dict[str, Any]:
    values = tuple(rows)
    entry_cents = sum(item["entry_cents"] for item in values)
    fee_cents = sum(item["fee_cents"] for item in values)
    payout_cents = sum(item["payout_cents"] for item in values)
    grouped = {fold: [item for item in values if item["fold"] == fold]
               for fold in sorted({item["fold"] for item in values})}
    return {
        "selected_opportunities": len(values),
        "entry_outlay": str(Decimal(entry_cents) / 100),
        "fees": str(Decimal(fee_cents) / 100),
        "payouts": str(Decimal(payout_cents) / 100),
        "net_profit": str(Decimal(payout_cents - entry_cents - fee_cents) / 100),
        "capital_weighted_return": str(
            Decimal(payout_cents - entry_cents - fee_cents) / Decimal(entry_cents)),
        "fold_returns": {
            str(fold): str(
                Decimal(sum(item["payout_cents"] - item["entry_cents"] - item["fee_cents"]
                            for item in items))
                / Decimal(sum(item["entry_cents"] for item in items)))
            for fold, items in grouped.items()
        },
    }


def build_replication_probe(root: Path) -> dict[str, Any]:
    verifier_path = root / "src/klax_lab/replication_v3.py"
    verifier_source = verifier_path.read_text(encoding="utf-8")
    if ("from .evaluator_v3 import OfflineCandidateEvaluatorV3" in verifier_source
            or ".evaluate(" in verifier_source):
        raise V3SubstantiveReadinessError(
            "Independent V3 verifier may not call the discovery evaluator")
    rows = (
        {"fold": 1, "entry_cents": 35, "fee_cents": 2, "payout_cents": 100},
        {"fold": 1, "entry_cents": 55, "fee_cents": 2, "payout_cents": 0},
        {"fold": 2, "entry_cents": 40, "fee_cents": 1, "payout_cents": 100},
        {"fold": 2, "entry_cents": 65, "fee_cents": 1, "payout_cents": 100},
    )
    primary = _primary_numerical_probe(rows)
    independent = _independent_numerical_probe(rows)
    if primary != independent:
        raise V3SubstantiveReadinessError("Independent numerical probe differs")
    record = _probe_record()
    checks = {name: True for name in REPLICATION_CHECKS}
    contract = {
        "contract_version": REPLICATION_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "verifier_id": "fixture-replicator",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only",
        "protected_final_evaluated": False,
        "source_artifact_sha256s": record.artifact_sha256s,
        "checks": checks, "status": "PASS", "differences": [],
    }
    if validate_v3_replication(record, contract) is not True:
        raise V3SubstantiveReadinessError("Replication contract probe failed")
    body = {
        "schema_version": 1, "component": "independent_replication",
        "status": PROBE_STATUS, "synthetic_engineering_fixture": True,
        "campaign_or_profit_evidence": False, "offline_verified": True,
        "network_used": False, "protected_final_read": False,
        "fixture_sha256": canonical_hash(rows),
        "source_artifact_sha256s": record.artifact_sha256s,
        "primary_result": primary, "independent_result": independent,
        "implementation_boundary": {
            "discovery_entrypoint": "klax_lab.evaluator_v3.OfflineCandidateEvaluatorV3.evaluate",
            "replication_entrypoint": (
                "klax_lab.replication_v3.independently_recompute_candidate"),
            "discovery_evaluator_called_by_replication": False,
            "isolated_replication_artifact": "recomputed-evidence.json",
        },
        "contract": contract,
        "code": _code_records(root, (
            "src/klax_lab/campaign_v3.py",
            "src/klax_lab/candidate_model_v3.py",
            "src/klax_lab/domain.py",
            "src/klax_lab/evaluation.py",
            "src/klax_lab/market_policy_v3.py",
            "src/klax_lab/provenance.py",
            "src/klax_lab/replication_v3.py",
            "src/klax_lab/research_plan_v3.py",
            "src/klax_lab/substantive_readiness_v3.py",
        )),
    }
    return {**body, "evidence_sha256": canonical_hash(body)}


def build_critic_probe(root: Path) -> dict[str, Any]:
    record = _probe_record()
    checks = {name: True for name in CRITIC_CHECKS}
    contract = {
        "contract_version": CRITIC_VERSION,
        "candidate_id": record.candidate_id,
        "candidate_plan_sha256": record.plan.identity,
        "novelty_fingerprint": record.plan.novelty_fingerprint,
        "critic_id": "fixture-critic",
        "discovery_worker_id": record.discovery_worker_id,
        "scope": "development_only", "protected_final_evaluated": False,
        "evidence_sha256s": record.artifact_sha256s,
        "checks": checks, "decision": "NONREJECT", "unresolved_defects": [],
    }
    if validate_v3_critic(record, contract) is not True:
        raise V3SubstantiveReadinessError("Critic contract probe failed")
    body = {
        "schema_version": 1, "component": "critic_contract",
        "status": PROBE_STATUS, "synthetic_engineering_fixture": True,
        "campaign_or_profit_evidence": False, "offline_verified": True,
        "network_used": False, "protected_final_read": False,
        "contract": contract,
        "required_checks": sorted(CRITIC_CHECKS),
        "independent_role_rules": {
            "critic_differs_from_discovery": True,
            "critic_must_differ_from_replicator_during_campaign": True,
        },
        "code": _code_records(root, ("src/klax_lab/campaign_v3.py",)),
    }
    return {**body, "evidence_sha256": canonical_hash(body)}


def build_worker_probe(root: Path) -> dict[str, Any]:
    """Build a deterministic parser fixture for synthetic tests only.

    This record deliberately cannot satisfy real readiness.  A production
    component must be published by :func:`publish_actual_v3_worker_probe` from
    an already completed local GPT-OSS protocol probe.
    """
    plan = _probe_plan()
    packet = {
        "protocol": PROTOCOL_V3, "task_id": "fixture-task",
        "campaign_id": "fixture-campaign", "worker_role": "explorer",
        "scope": "synthetic_only", "synthetic": True,
        "question": "Propose one bounded synthetic protocol fixture.",
        "readiness_sha256": "1" * 64, "config_sha256": "2" * 64,
        "schema_sha256": "3" * 64, "partition_contract_sha256": "4" * 64,
        "data_bundle_version": "v3-capability-fixture",
        "data_bundle_sha256": "a" * 64, "colony": plan.colony, "stage": plan.stage,
        "parent_hypothesis_ids": [], "parent_plan_sha256s": [],
        "evidence": [{
            "evidence_id": "fixture-evidence", "scope": "synthetic",
            "summary": "Synthetic protocol capability evidence only.",
            "artifact_sha256": "5" * 64,
        }],
        "seed_plans": [plan.to_dict()],
        "budget_remaining": {
            "epoch": 1, "epochs_remaining": 5, "candidate_slots_remaining": 60,
            "epoch_candidate_slots_remaining": 10, "model_calls_remaining": 180,
            "reserved_context_tokens_remaining": 2_949_120,
            "paid_api_dollars_remaining": 0, "wall_seconds_remaining": 28_800,
        },
    }
    response = {
        "protocol": PROTOCOL_V3, "task_id": "fixture-task", "action": "propose",
        "seed_index": 0, "rationale": "Bounded synthetic proposal.",
        "evidence_ids": ["fixture-evidence"],
        "limitations": ["Synthetic protocol fixture only."], "requested_checks": [],
    }
    checked = validate_v3_worker_packet(packet)
    proposal = parse_v3_worker_proposal(response, checked)

    def rejects(value: dict[str, Any]) -> bool:
        try:
            validate_v3_worker_packet(value)
        except V3ResearchProtocolError:
            return True
        return False

    negative_checks = {
        "protected_final_scope_rejected": rejects({**packet, "scope": "protected_final"}),
        "unknown_command_field_rejected": rejects({**packet, "command": "open holdout"}),
    }
    if proposal.plan != plan or proposal.action != "propose" or not all(negative_checks.values()):
        raise V3SubstantiveReadinessError("Worker protocol capability probe failed")
    body = {
        "schema_version": 1, "component": "worker_capability",
        "status": PROBE_STATUS, "synthetic_engineering_fixture": True,
        "campaign_or_profit_evidence": False, "local_model_inference_executed": False,
        "offline_verified": True, "network_used": False, "protected_final_read": False,
        "packet_sha256": v3_packet_sha256(packet),
        "proposal_sha256": canonical_hash(proposal.to_dict()),
        "compiled_plan_sha256": plan.identity,
        "negative_boundary_checks": negative_checks,
        "code": _code_records(root, (
            "src/klax_lab/research_plan_v3.py", "src/klax_lab/research_protocol_v3.py")),
    }
    return {**body, "evidence_sha256": canonical_hash(body)}


def _actual_worker_probe_body(
    root: Path,
    runtime_spec_path: Path | str,
    v3_protocol_probe_path: Path | str,
) -> dict[str, Any]:
    """Validate existing local-inference evidence without invoking the model."""
    from .local_backend import BACKEND, load_runtime_spec, verify_runtime

    root = Path(root).resolve()
    if not isinstance(runtime_spec_path, (Path, str)):
        raise V3SubstantiveReadinessError("Invalid runtime spec path")
    if not isinstance(v3_protocol_probe_path, (Path, str)):
        raise V3SubstantiveReadinessError("Invalid V3 protocol probe path")
    runtime_spec = Path(runtime_spec_path)
    runtime_spec = (runtime_spec.resolve() if runtime_spec.is_absolute()
                    else (root / runtime_spec).resolve())
    probe = Path(v3_protocol_probe_path)
    probe = probe.resolve() if probe.is_absolute() else (root / probe).resolve()
    for path, label in ((runtime_spec, "runtime spec"), (probe, "V3 protocol probe")):
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise V3SubstantiveReadinessError(f"{label} escapes the project") from exc
        if "protected_final" in {
                part.casefold().replace("-", "_") for part in relative.parts}:
            raise V3SubstantiveReadinessError(f"{label} enters protected-final storage")
        if not path.is_file():
            raise V3SubstantiveReadinessError(f"Missing {label}: {path}")
    runtime_relative = runtime_spec.relative_to(root)
    probe_relative = probe.relative_to(root)
    if (not runtime_spec.is_relative_to(root / "data/models")
            or runtime_spec.suffix.casefold() != ".json"):
        raise V3SubstantiveReadinessError(
            "V3 runtime specification must be JSON under data/models")
    if (len(probe_relative.parts) != 4
            or probe_relative.parts[:2] != ("runs", "local_worker_probe")
            or probe_relative.name != "v3-protocol-report.json"):
        raise V3SubstantiveReadinessError(
            "V3 protocol probe must be a saved runs/local_worker_probe artifact")

    report = _read_json(probe, "actual local V3 protocol probe")
    backend_path = root / "src/klax_lab/local_backend.py"
    backend_sha = sha256_file(backend_path) if backend_path.is_file() else None
    runtime = verify_runtime(load_runtime_spec(root, runtime_spec))
    runtime_sha = _check_hash(runtime.get("runtime_sha256"), "verified local runtime")
    if (report.get("status") != "PASS"
            or report.get("synthetic") is not True
            or report.get("protocol") != PROTOCOL_V3
            or report.get("backend") != BACKEND
            or report.get("actual_v3_protocol_probe_passed") is not True
            or report.get("runtime_sha256") != runtime_sha
            or report.get("backend_code_sha256") != backend_sha
            or report.get("tool_catalog") != []
            or report.get("error") is not None
            or SHA.fullmatch(str(report.get("validated_research_plan_sha256"))) is None):
        raise V3SubstantiveReadinessError(
            "Actual local GPT-OSS V3 protocol probe did not pass or changed")
    body = {
        "schema_version": 1, "component": "worker_capability",
        "status": ACTUAL_WORKER_STATUS,
        "synthetic_engineering_fixture": False,
        "synthetic_probe_input": True,
        "campaign_or_profit_evidence": False,
        "local_model_inference_executed": True,
        "actual_v3_protocol_probe_passed": True,
        "offline_verified": True, "network_used": False,
        "protected_final_read": False, "tool_catalog": [],
        "backend": BACKEND,
        "runtime_spec_path": runtime_relative.as_posix(),
        "runtime_spec_sha256": sha256_file(runtime_spec),
        "runtime_sha256": runtime_sha,
        "backend_code_sha256": backend_sha,
        "v3_protocol_probe_path": probe_relative.as_posix(),
        "v3_protocol_probe_sha256": sha256_file(probe),
        "validated_research_plan_sha256": report["validated_research_plan_sha256"],
        "code": _code_records(root, (
            "src/klax_lab/local_backend.py",
            "src/klax_lab/research_plan_v3.py",
            "src/klax_lab/research_protocol_v3.py")),
        "limitations": [
            "The probe uses synthetic packet content and proves protocol compatibility, not research quality.",
            "The trusted local runtime boundary is not an operating-system sandbox.",
            "This component is not market-performance or profitability evidence.",
        ],
    }
    return {**body, "evidence_sha256": canonical_hash(body)}


def publish_actual_v3_worker_probe(
    root: Path,
    *,
    runtime_spec_path: Path | str = Path("data/models/runtime_spec.json"),
    v3_protocol_probe_path: Path | str | None = None,
    pointer_path: Path | str = Path("data/manifests/local_worker_probe.json"),
) -> dict[str, Any]:
    """Publish the worker component from an existing successful inference probe.

    If ``v3_protocol_probe_path`` is omitted, the existing probe pointer is
    resolved and hash-checked.  This helper never invokes local inference.
    """
    root = Path(root).resolve()
    if v3_protocol_probe_path is None:
        if not isinstance(pointer_path, (Path, str)):
            raise V3SubstantiveReadinessError("Invalid local worker probe pointer path")
        pointer = Path(pointer_path)
        pointer = pointer.resolve() if pointer.is_absolute() else (root / pointer).resolve()
        try:
            pointer_relative = pointer.relative_to(root)
        except ValueError as exc:
            raise V3SubstantiveReadinessError(
                "Local worker probe pointer escapes the project") from exc
        if "protected_final" in {
                part.casefold().replace("-", "_") for part in pointer_relative.parts}:
            raise V3SubstantiveReadinessError(
                "Local worker probe pointer enters protected-final storage")
        saved = _read_json(pointer, "local worker probe pointer")
        v3_protocol_probe_path = saved.get("v3_protocol_probe_path")
        probe = _inside(root, v3_protocol_probe_path, "V3 protocol probe")
        if sha256_file(probe) != _check_hash(
                saved.get("v3_protocol_probe_sha256"), "V3 protocol probe"):
            raise V3SubstantiveReadinessError("V3 local protocol probe pointer is stale")
    body = _actual_worker_probe_body(root, runtime_spec_path, v3_protocol_probe_path)
    write_json(root / WORKER_PATH, body)
    verify_actual_v3_worker_probe(root)
    return body


def verify_actual_v3_worker_probe(root: Path) -> dict[str, Any]:
    """Revalidate the saved component, runtime, backend, and probe hashes."""
    root = Path(root).resolve()
    saved = _read_json(root / WORKER_PATH, "V3 worker capability component")
    if (saved.get("status") != ACTUAL_WORKER_STATUS
            or saved.get("synthetic_engineering_fixture") is not False
            or saved.get("synthetic_probe_input") is not True
            or saved.get("campaign_or_profit_evidence") is not False
            or saved.get("local_model_inference_executed") is not True
            or saved.get("actual_v3_protocol_probe_passed") is not True
            or saved.get("offline_verified") is not True
            or saved.get("network_used") is not False
            or saved.get("protected_final_read") is not False
            or saved.get("tool_catalog") != []):
        raise V3SubstantiveReadinessError(
            "Real V3 readiness requires an actual local GPT-OSS protocol probe")
    evidence = saved.get("evidence_sha256")
    if canonical_hash({key: value for key, value in saved.items()
                       if key != "evidence_sha256"}) != evidence:
        raise V3SubstantiveReadinessError("V3 worker capability evidence identity changed")
    expected = _actual_worker_probe_body(
        root, saved.get("runtime_spec_path"), saved.get("v3_protocol_probe_path"))
    if saved != expected:
        raise V3SubstantiveReadinessError("V3 worker capability component is stale or modified")
    return saved


def _code_records(root: Path, paths: Iterable[str]) -> list[dict[str, Any]]:
    records = []
    for value in sorted(set(paths)):
        path = _inside(root, value, "code")
        if not path.is_file():
            raise V3SubstantiveReadinessError(f"Missing V3 code dependency: {value}")
        records.append({"path": value, "bytes": path.stat().st_size,
                        "sha256": sha256_file(path)})
    return records


def publish_v3_capability_probes(
    root: Path,
    *,
    include_synthetic_worker_fixture: bool = False,
) -> dict[str, dict[str, Any]]:
    """Publish the deterministic probes and bind the actual worker evidence.

    ``include_synthetic_worker_fixture`` exists only for isolated engineering
    tests.  Its output is deliberately rejected by the default verifier and
    can never satisfy campaign readiness.
    """
    root = Path(root).resolve()
    probes = {
        "independent_replication": (REPLICATION_PATH, build_replication_probe(root)),
        "critic_contract": (CRITIC_PATH, build_critic_probe(root)),
    }
    if include_synthetic_worker_fixture:
        probes["worker_capability"] = (WORKER_PATH, build_worker_probe(root))
    for path, body in probes.values():
        write_json(root / path, body)
    verified = verify_v3_capability_probes(
        root, allow_synthetic_worker=include_synthetic_worker_fixture)
    if not include_synthetic_worker_fixture:
        probes["worker_capability"] = (
            WORKER_PATH, _read_json(root / WORKER_PATH, "V3 worker capability component"))
    if verified.get("status") != "PASS":
        raise V3SubstantiveReadinessError("V3 capability probes did not pass")
    return {name: body for name, (_, body) in probes.items()}


def verify_v3_capability_probes(
    root: Path,
    *,
    allow_synthetic_worker: bool = False,
) -> dict[str, Any]:
    """Re-execute capability checks; real readiness always uses the default."""
    root = Path(root).resolve()
    expected = {
        REPLICATION_PATH: build_replication_probe(root),
        CRITIC_PATH: build_critic_probe(root),
    }
    for path, body in expected.items():
        saved = _read_json(root / path, path.name)
        if saved != body:
            raise V3SubstantiveReadinessError(f"V3 capability probe is stale or modified: {path}")
        _require_offline_boundary(saved, path.name)
    if allow_synthetic_worker:
        worker = _read_json(root / WORKER_PATH, WORKER_PATH.name)
        if worker != build_worker_probe(root):
            raise V3SubstantiveReadinessError(
                f"V3 capability probe is stale or modified: {WORKER_PATH}")
    else:
        worker = verify_actual_v3_worker_probe(root)
    _require_offline_boundary(worker, WORKER_PATH.name)
    return {
        "status": "PASS", "network_used": False, "protected_final_read": False,
        "actual_local_model_probe_required": not allow_synthetic_worker,
        "manifests": {
            path.as_posix(): sha256_file(root / path)
            for path in (*expected, WORKER_PATH)
        },
    }


def _validate_source(root: Path, body: dict[str, Any]) -> None:
    _require_offline_boundary(body, "source feasibility")
    audit = body.get("completion_audit")
    if (body.get("component") != "source_feasibility"
            or body.get("status") != SOURCE_COMPLETE_STATUS
            or body.get("readiness_component_pass") is not True
            or not isinstance(audit, dict)
            or audit.get("days_complete") != EXPECTED_WEATHER_DAYS
            or audit.get("days_unavailable") != 0
            or audit.get("offline_normalization_verified") is not True
            or audit.get("protected_final_read") is not False):
        raise V3SubstantiveReadinessError("Source feasibility is not finalized")
    try:
        verify_availability_policy_registration(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError(
            "Weather availability registration is missing or changed") from exc
    expected_policy_bindings = {
        "weather_availability_policy_config_path": POLICY_CONFIG_PATH.as_posix(),
        "weather_availability_policy_config_sha256": POLICY_CONFIG_SHA256,
        "weather_availability_amendment_path": POLICY_AMENDMENT_PATH.as_posix(),
        "weather_availability_amendment_sha256": POLICY_AMENDMENT_SHA256,
        "pre_amendment_normalization_snapshot_path": PRE_AMENDMENT_PROGRESS_PATH.as_posix(),
        "pre_amendment_normalization_snapshot_sha256": PRE_AMENDMENT_PROGRESS_SHA256,
    }
    if any(audit.get(key) != value for key, value in expected_policy_bindings.items()):
        raise V3SubstantiveReadinessError(
            "Source completion weather availability bindings changed")
    for path_key, sha_key in (
        ("bulk_progress_path", "bulk_progress_sha256"),
        ("normalization_progress_path", "normalization_progress_sha256"),
        ("acquisition_attempts_path", "acquisition_attempts_sha256"),
    ):
        path = _inside(root, audit.get(path_key), "source audit")
        if not path.is_file() or sha256_file(path) != _check_hash(audit.get(sha_key), sha_key):
            raise V3SubstantiveReadinessError("Source completion evidence changed")
    base = deepcopy(body)
    base.pop("completion_audit", None)
    base.pop("readiness_component_pass", None)
    base["status"] = audit.get("prior_status")
    if canonical_hash(base) != audit.get("source_plan_sha256"):
        raise V3SubstantiveReadinessError("Source feasibility plan changed after completion")
    progress = _read_json(root / BULK_PROGRESS_PATH, "V3 bulk-weather progress")
    attempt_journal = _verify_acquisition_attempt_journal(root, progress)
    if (len(attempt_journal["attempts"]) != audit.get("acquisition_process_attempt_count")
            or [item["terminal_status"] for item in attempt_journal["attempts"]]
            != audit.get("acquisition_process_attempt_statuses")):
        raise V3SubstantiveReadinessError("Acquisition process-attempt audit changed")
    coverage_identity = [{
        "date": item.get("date"), "status": item.get("status"),
        "hrrr_objects": item.get("hrrr_objects"), "gefs_objects": item.get("gefs_objects"),
    } for item in progress.get("days", [])]
    if canonical_hash(coverage_identity) != audit.get("coverage_sha256"):
        raise V3SubstantiveReadinessError("Source coverage identity changed")
    normalized = _read_json(root / NORMALIZATION_PROGRESS_PATH,
                            "V3 weather-normalization progress")
    migration_identity = [{
        "date": item.get("date"), "migrated_from_v1": item.get("migrated_from_v1"),
        "manifest_path": (item.get("manifest") or {}).get("path"),
        "manifest_sha256": (item.get("manifest") or {}).get("sha256"),
    } for item in normalized.get("days", [])]
    for item in normalized.get("days", []):
        expected_manifest = (
            f"data/normalized/v3_weather/{item.get('partition')}/"
            f"date={item.get('date')}/normalization_manifest.json")
        if (not isinstance(item.get("manifest"), dict)
                or item["manifest"].get("path") != expected_manifest):
            raise V3SubstantiveReadinessError(
                "Weather normalization daily manifest inventory differs")
        _verify_file_record(root, item.get("manifest"),
                            "weather normalization daily manifest")
    expected_dates = _expected_dates()
    expected_flat = expected_dates["weather_training"] + expected_dates["selection"]
    if (normalized.get("migrated_verified_v1_days") != 542
            or normalized.get("fresh_v2_normalized_dates") != ["2025-06-28"]
            or len(migration_identity) != EXPECTED_WEATHER_DAYS
            or [item["date"] for item in migration_identity] != expected_flat
            or sum(item["migrated_from_v1"] is True for item in migration_identity) != 542
            or [item["date"] for item in migration_identity
                if item["migrated_from_v1"] is not True] != ["2025-06-28"]
            or audit.get("migrated_verified_v1_days") != 542
            or audit.get("fresh_v2_normalized_dates") != ["2025-06-28"]
            or canonical_hash(migration_identity)
            != audit.get("weather_policy_migration_identity_sha256")):
        raise V3SubstantiveReadinessError(
            "Weather policy migration lineage changed")
    for path_value, key in (
        ("data/manifests/v3_hrrr_local_observations.json", "hrrr_component_sha256"),
        ("data/manifests/v3_gefs.json", "gefs_component_sha256"),
    ):
        path = root / path_value
        if not path.is_file() or sha256_file(path) != audit.get(key):
            raise V3SubstantiveReadinessError("Completed weather component changed")
    _verify_weather_component(root, "hrrr_local_observations")
    _verify_weather_component(root, "gefs")


def _validate_settlement(root: Path, body: dict[str, Any]) -> None:
    _require_offline_boundary(body, "settlement reconciliation")
    if (body.get("schema_version") != 1 or body.get("component") != "settlement_reconciliation"
            or body.get("status") != "DEVELOPMENT_COMPLETE"
            or type(body.get("eligible_target_count")) is not int
            or body["eligible_target_count"] < 30
            or not isinstance(body.get("rules"), list) or len(body["rules"]) < 3):
        raise V3SubstantiveReadinessError("Settlement reconciliation schema differs")
    for record in body.get("inputs", []):
        _verify_file_record(root, record, "settlement input")
    if not body.get("inputs"):
        raise V3SubstantiveReadinessError("Settlement source inventory is empty")
    for partition, record in body.get("partitions", {}).items():
        if partition not in {"weather_training", "selection"} or not isinstance(record, dict):
            raise V3SubstantiveReadinessError("Settlement partition inventory differs")
        for prefix in ("target", "exclusions"):
            _verify_file_record(root, {
                "path": record.get(f"{prefix}_path"),
                "sha256": record.get(f"{prefix}_sha256"),
            }, f"settlement {partition} {prefix}")


def _validate_minute_market(root: Path, body: dict[str, Any]) -> None:
    _require_offline_boundary(body, "minute Kalshi")
    if (body.get("schema_version") != 1 or body.get("component") != "minute_kalshi"
            or body.get("status") != "DEVELOPMENT_COMPLETE"
            or body.get("start_date") != "2025-01-05"
            or body.get("end_date") != "2025-06-30"
            or body.get("historical_depth_available") is not False
            or any(type(body.get(key)) is not int or body[key] < 1 for key in (
                "eligible_contracts", "one_minute_candle_rows", "public_trade_rows",
                "verified_raw_source_files"))):
        raise V3SubstantiveReadinessError("Minute Kalshi schema or coverage differs")
    records = body.get("candle_batches", []) + body.get("trade_batches", [])
    if not records:
        raise V3SubstantiveReadinessError("Minute Kalshi batch inventory is empty")
    for record in records:
        _verify_file_record(root, record, "minute Kalshi batch manifest")
    fixed_sources = (
        ("data/manifests/kalshi_coverage.json", "coverage_manifest_sha256"),
        ("data/manifests/kalshi_downloads.json", "download_manifest_sha256"),
    )
    for path_value, hash_key in fixed_sources:
        path = _inside(root, path_value, "minute Kalshi source inventory")
        if (not path.is_file()
                or sha256_file(path) != _check_hash(body.get(hash_key), hash_key)):
            raise V3SubstantiveReadinessError(
                f"Minute Kalshi source inventory changed: {path_value}")


def _validate_frozen_dataset(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    _require_offline_boundary(body, "frozen dataset")
    if (body.get("schema_version") != 1 or body.get("component") != "frozen_dataset"
            or body.get("status") != "TRAINING_CALIBRATION_EVALUATION_FROZEN"
            or body.get("ready_for_v3_campaign") is not False):
        raise V3SubstantiveReadinessError("Frozen dataset component schema differs")
    manifest = _verify_file_record(root, {
        "path": body.get("dataset_manifest_path"),
        "sha256": body.get("dataset_manifest_sha256"),
    }, "frozen dataset manifest")
    verified = verify_frozen_development_dataset(manifest.parent)
    if verified.get("dataset_id") != body.get("dataset_id"):
        raise V3SubstantiveReadinessError("Frozen dataset component identity differs")
    if (body.get("development_calibration", {}).get("scored") is not False
            or body.get("development_evaluation", {}).get("scored") is not True):
        raise V3SubstantiveReadinessError("Calibration/evaluation roles differ")
    return verified


def _validate_folds(root: Path, body: dict[str, Any], dataset: dict[str, Any]) -> None:
    _require_offline_boundary(body, "five-fold split")
    if (body.get("schema_version") != 1 or body.get("component") != "five_fold_split"
            or body.get("status") != "EVALUATION_FOLDS_FROZEN"
            or body.get("ready_for_v3_campaign") is not False
            or body.get("dataset_id") != dataset["dataset_id"]
            or body.get("folds_id") != dataset["folds_id"]
            or len(body.get("folds", [])) != 5
            or len(body.get("calibration_dates", [])) != 30):
        raise V3SubstantiveReadinessError("Five-fold component schema or identity differs")
    _verify_file_record(root, {
        "path": body.get("fold_path"), "sha256": body.get("fold_sha256")
    }, "frozen fold artifact")
    flattened = [day for fold in body["folds"] for day in fold.get("dates", [])]
    if flattened != body.get("evaluation_dates") or len(flattened) != len(set(flattened)):
        raise V3SubstantiveReadinessError("Five folds do not exactly partition evaluation dates")


def _validate_fixture_component(root: Path, body: dict[str, Any], component: str) -> None:
    _require_offline_boundary(body, component)
    if (body.get("status") != "ENGINEERING_FIXTURES_PASS"
            or body.get("synthetic_engineering_fixtures") is not True
            or body.get("campaign_or_profit_evidence") is not False
            or not isinstance(body.get("invariants"), dict)
            or not body["invariants"] or not all(value is True for value in body["invariants"].values())
            or canonical_hash({key: value for key, value in body.items()
                               if key != "evidence_sha256"}) != body.get("evidence_sha256")):
        raise V3SubstantiveReadinessError(f"{component} fixture schema differs")


def validate_all_v3_components(root: Path) -> list[dict[str, str]]:
    """Substantively validate all twelve required component manifests."""
    root = Path(root).resolve()
    values = {item.component: _read_json(root / item.path, item.component)
              for item in REQUIRED_COMPONENT_MANIFESTS}
    _validate_source(root, values["source_feasibility"])
    _validate_settlement(root, values["settlement_reconciliation"])
    _validate_minute_market(root, values["minute_kalshi"])
    _verify_weather_component(root, "hrrr_local_observations")
    _verify_weather_component(root, "gefs")
    dataset = _validate_frozen_dataset(root, values["frozen_dataset"])
    _validate_folds(root, values["five_fold_split"], dataset)
    _validate_fixture_component(root, values["probability_and_regime_models"],
                                "probability_and_regime_models")
    _validate_fixture_component(root, values["market_residual_and_abstention"],
                                "market_residual_and_abstention")
    try:
        fixture = verify_component_fixtures(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError("Deterministic component fixtures changed") from exc
    if fixture.get("status") != "PASS":
        raise V3SubstantiveReadinessError("Deterministic component fixtures did not re-execute")
    verify_v3_capability_probes(root)
    return [{
        "component": item.component, "path": item.path,
        "sha256": sha256_file(root / item.path), "status": "PASS",
    } for item in REQUIRED_COMPONENT_MANIFESTS]


def _all_code_inventory(root: Path) -> list[dict[str, Any]]:
    paths = sorted((root / "src/klax_lab").glob("*.py"))
    if not paths:
        raise V3SubstantiveReadinessError("V3 source package is missing")
    return [{
        "path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path in paths]


def _data_bundle(root: Path) -> dict[str, Any]:
    dataset_path = root / "data/manifests/v3_dataset.json"
    folds_path = root / "data/manifests/v3_five_fold_split.json"
    dataset = _read_json(dataset_path, "frozen dataset component")
    folds = _read_json(folds_path, "five-fold component")
    frozen_manifest = _inside(
        root, dataset.get("dataset_manifest_path"), "frozen dataset manifest")
    frozen_fold = _inside(root, folds.get("fold_path"), "frozen fold artifact")
    body = {
        "schema_version": "klax-v3-data-bundle-v1",
        "scope": "weather_training_calibration_and_scored_development_only",
        "dataset_id": dataset["dataset_id"], "folds_id": folds["folds_id"],
        "dataset_component": {
            "path": "data/manifests/v3_dataset.json", "sha256": sha256_file(dataset_path)},
        "fold_component": {
            "path": "data/manifests/v3_five_fold_split.json", "sha256": sha256_file(folds_path)},
        "frozen_dataset_manifest": {
            "path": frozen_manifest.relative_to(root).as_posix(),
            "sha256": sha256_file(frozen_manifest),
        },
        "frozen_fold_artifact": {
            "path": frozen_fold.relative_to(root).as_posix(), "sha256": sha256_file(frozen_fold)},
        "network_used": False, "protected_final_read": False,
    }
    bundle = {**body, "bundle_sha256": canonical_hash(body)}
    bundle_path = root / "data/manifests/v3_data_bundle.json"
    write_json(bundle_path, bundle)
    return {
        "version": dataset["dataset_id"], "sha256": bundle["bundle_sha256"],
        "manifest_path": "data/manifests/v3_data_bundle.json",
        "manifest_sha256": sha256_file(bundle_path), "scope": body["scope"],
    }


def issue_v3_campaign_readiness(
    root: Path,
    campaign_id: str,
    *,
    readiness_path: Path = READINESS_PATH,
    ticket_path: Path = TICKET_PATH,
    now: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Issue hash-bound readiness and one-use offline ticket after all checks pass."""
    root = Path(root).resolve()
    if not isinstance(campaign_id, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", campaign_id) is None:
        raise V3SubstantiveReadinessError("Invalid V3 campaign_id")
    registration = validate_v3_registration(root)
    goal = _read_json(root / registration["config_path"], "V3 goal")
    components = validate_all_v3_components(root)
    code_inventory = _all_code_inventory(root)
    try:
        from .evaluator_v3 import evaluation_policy_sha256
        policy_sha = evaluation_policy_sha256()
    except (ImportError, AttributeError, ValueError) as exc:
        raise V3SubstantiveReadinessError("V3 evaluator policy is unavailable") from exc
    _check_hash(policy_sha, "evaluation policy")
    created = (now or (lambda: datetime.now(UTC)))()
    if created.tzinfo is None or created.utcoffset() is None:
        raise V3SubstantiveReadinessError("Readiness clock must be timezone-aware")
    ranking_sha = canonical_hash({"rule": CHAMPION_RANKING_RULE})
    # The failed campaign's active bundle was archived with its authorization.
    # Reconstruct this deterministic nonprotected binding before the amendment
    # compares the unchanged scientific contract to the archived bundle.
    data_bundle = _data_bundle(root)
    packet_repair_path = root / PACKET_REPAIR_AMENDMENT_PATH
    try:
        if packet_repair_path.is_file():
            incident = verify_campaign2_packet_repair_amendment(
                root,
                expected_champion_ranking_rule_sha256=ranking_sha,
                pre_issuance=True,
            )
            incident_binding = {
                key: incident[key] for key in (
                    "path", "sha256", "amendment_id", "source_campaign_id",
                    "replacement_campaign_id",
                    "continuation_import_state_sha256")
            }
        else:
            incident = verify_campaign1_integrity_repair_amendment(
                root,
                expected_champion_ranking_rule_sha256=ranking_sha,
                pre_issuance=True,
            )
            incident_binding = {
                key: incident[key] for key in (
                    "path", "sha256", "amendment_id", "failed_campaign_id",
                    "replacement_campaign_id")
            }
    except (V3IncidentAmendmentError,
            V3PacketIncidentAmendmentError) as exc:
        raise V3SubstantiveReadinessError(
            "V3 campaign incident amendment is unavailable or invalid") from exc
    registered_at = datetime.fromisoformat(incident["registered_at_utc"])
    if created.astimezone(UTC) <= registered_at:
        raise V3SubstantiveReadinessError(
            "Readiness must be issued after the incident amendment registration")
    readiness_file = _new_project_file(root, readiness_path, "readiness")
    ticket_file = _new_project_file(root, ticket_path, "ticket")
    if (campaign_id != incident["replacement_campaign_id"]
            or readiness_file.relative_to(root).as_posix()
            != incident["replacement_readiness_path"]
            or ticket_file.relative_to(root).as_posix()
            != incident["replacement_ticket_path"]
            or ticket_file.relative_to(root).as_posix() + ".claimed.json"
            != incident["replacement_claim_path"]):
        raise V3SubstantiveReadinessError(
            "Replacement campaign identity or authorization paths differ from amendment")
    readiness = {
        "readiness_version": READINESS_VERSION, "status": READY_STATUS,
        "architecture_version": 3, "synthetic": False,
        "ready_for_v3_campaign": True, "v3_campaign_authorized": True,
        "protected_final_access_authorized": False, "offline_verified": True,
        "holdout_access_denied": True, "historical_only": True,
        "config_path": registration["config_path"],
        "config_sha256": registration["config_sha256"],
        "schema_path": registration["schema_path"],
        "schema_sha256": registration["schema_sha256"],
        "data_bundle": data_bundle,
        "code_inventory": code_inventory, "code_sha256": canonical_hash(code_inventory),
        "evaluation_policy_sha256": policy_sha,
        "promotion_gates_sha256": canonical_hash(goal["development_promotion_gates"]),
        "campaign_budget_sha256": canonical_hash(goal["campaign_budget"]),
        "campaign_budget": goal["campaign_budget"],
        "partition_contract": goal["partitions"],
        "partition_contract_sha256": canonical_hash(goal["partitions"]),
        "validated_component_manifests": components,
        "protected_final_roots": list(PROTECTED_FINAL_ROOTS),
        "champion_ranking_rule": CHAMPION_RANKING_RULE,
        "champion_ranking_rule_sha256": ranking_sha,
        "incident_amendment": incident_binding,
        "created_at_utc": created.astimezone(UTC).isoformat(),
    }
    write_json(readiness_file, readiness)
    ticket = {
        "ticket_version": TICKET_VERSION, "status": "ACTIVE", "synthetic": False,
        "one_use": True, "campaign_id": campaign_id,
        "readiness_path": readiness_file.relative_to(root).as_posix(),
        "readiness_sha256": sha256_file(readiness_file),
        "config_sha256": readiness["config_sha256"],
        "schema_sha256": readiness["schema_sha256"],
        "data_bundle_version": data_bundle["version"],
        "data_bundle_sha256": data_bundle["sha256"],
        "code_sha256": readiness["code_sha256"],
        "evaluation_policy_sha256": policy_sha,
        "promotion_gates_sha256": readiness["promotion_gates_sha256"],
        "campaign_budget_sha256": readiness["campaign_budget_sha256"],
        "partition_contract_sha256": readiness["partition_contract_sha256"],
        "champion_ranking_rule_sha256": readiness["champion_ranking_rule_sha256"],
        "incident_amendment_id": incident["amendment_id"],
        "incident_amendment_sha256": incident["sha256"],
        "protected_final_authorized": False,
        "protected_final_evaluations_remaining": 1,
        "issued_at_utc": created.astimezone(UTC).isoformat(),
    }
    try:
        write_json(ticket_file, ticket)
    except BaseException:
        # Readiness alone grants nothing, but avoid leaving a misleading pair
        # after a local write failure.
        readiness_file.unlink(missing_ok=True)
        raise
    return readiness, ticket


def _new_project_file(root: Path, path: Path, label: str) -> Path:
    value = path if path.is_absolute() else root / path
    value = value.resolve()
    try:
        value.relative_to(root)
    except ValueError as exc:
        raise V3SubstantiveReadinessError(f"V3 {label} must remain inside the project") from exc
    if "protected_final" in {part.casefold().replace("-", "_") for part in value.parts}:
        raise V3SubstantiveReadinessError(f"V3 {label} cannot be stored in protected final")
    if value.exists() or value.with_name(value.name + ".claimed.json").exists():
        raise V3SubstantiveReadinessError(f"V3 {label} path already exists; one-use artifact refused")
    return value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("probes", "publish-worker-probe", "finalize-source", "validate", "issue"),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--campaign-id")
    parser.add_argument("--readiness-path", type=Path, default=READINESS_PATH)
    parser.add_argument("--ticket-path", type=Path, default=TICKET_PATH)
    parser.add_argument(
        "--runtime-spec", type=Path, default=Path("data/models/runtime_spec.json"),
        help="Pinned local-runtime manifest to bind to an existing V3 probe",
    )
    parser.add_argument(
        "--v3-protocol-probe", type=Path,
        help="Existing successful V3 protocol report; defaults to the local-worker pointer",
    )
    args = parser.parse_args(argv)
    if args.command == "probes":
        result = publish_v3_capability_probes(args.root)
    elif args.command == "publish-worker-probe":
        result = publish_actual_v3_worker_probe(
            args.root,
            runtime_spec_path=args.runtime_spec,
            v3_protocol_probe_path=args.v3_protocol_probe,
        )
    elif args.command == "finalize-source":
        result = finalize_v3_source_feasibility(args.root)
    elif args.command == "validate":
        result = {"status": "PASS", "components": validate_all_v3_components(args.root)}
    else:
        if not args.campaign_id:
            parser.error("issue requires --campaign-id")
        readiness, ticket = issue_v3_campaign_readiness(
            args.root, args.campaign_id,
            readiness_path=args.readiness_path, ticket_path=args.ticket_path,
        )
        readiness_file = (args.readiness_path if args.readiness_path.is_absolute()
                          else args.root / args.readiness_path).resolve()
        ticket_file = (args.ticket_path if args.ticket_path.is_absolute()
                       else args.root / args.ticket_path).resolve()
        result = {
            "readiness_sha256": sha256_file(readiness_file),
            "ticket_sha256": sha256_file(ticket_file),
            "ticket_campaign_id": ticket["campaign_id"],
            "protected_final_authorized": False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
