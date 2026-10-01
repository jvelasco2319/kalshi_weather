"""Deterministic V5 Execution Evidence colony readiness checks.

This module deliberately does not acquire data, connect to a network, submit an
order, or read confirmation labels.  It inventories frozen historical source
capabilities and fails closed when the archive cannot prove an executable fill.
Candles and public trade prints remain useful diagnostics, but are never
promotion-grade execution evidence.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


CONFIG_RELATIVE_PATH = Path("configs/v5_four_colony_verification_campaign.json")
MINUTE_MANIFEST_RELATIVE_PATH = Path("data/manifests/v3_minute_kalshi.json")

SOURCE_SCHEMA_VERSION = "klax-v5-execution-source-record-v1"
CENSUS_SCHEMA_VERSION = "klax-v5-execution-source-capability-census-v1"
AUDIT_SCHEMA_VERSION = "klax-v5-current-execution-evidence-audit-v1"
VERDICT_SCHEMA_VERSION = "klax-v5-execution-readiness-verdict-v1"
EVIDENCE_SCHEMA_VERSION = "klax-v5-execution-evidence-record-v1"
CONTROLLER_RESULT_VERSION = "klax-v5-execution-controller-result-v1"
CONTROLLER_REVIEW_VERSION = "klax-v5-execution-cross-review-v1"

DIAGNOSTIC_SOURCE_KINDS = frozenset({
    "one_minute_candles",
    "public_trade_prints",
})
PROMOTION_SOURCE_KINDS = frozenset({
    "actual_member_order_and_fill",
    "contemporaneous_gap_free_orderbook_snapshot_and_deltas",
    "timestamped_high_frequency_full_book_with_size",
})

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PROTECTED_PATH_PARTS = frozenset({
    "protected_final",
    "protected-final",
    "holdout",
    "confirmation_labels",
    "confirmation-labels",
})

_COMMON_PROMOTION_CAPABILITIES = (
    "immutable",
    "content_hash_verified",
    "market_identity",
    "exchange_timestamp",
    "timestamp_semantics_documented",
    "side",
    "price",
    "historical_coverage_documented",
    "completeness_documented",
)
_BOOK_PROMOTION_CAPABILITIES = (
    "quantity_at_price",
    "snapshot_or_sequence_identity",
    "update_semantics_documented",
    "contemporaneous_availability_timestamp",
    "gap_free",
)
_MEMBER_FILL_PROMOTION_CAPABILITIES = (
    "order_identity",
    "fill_identity",
    "actual_fill_binding",
    "executed_quantity",
    "fee_at_fill",
)


class ExecutionEvidenceError(ValueError):
    """Raised when a V5 execution artifact violates the registered contract."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _artifact_sha256(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


def _with_artifact_sha256(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result["artifact_sha256"] = _artifact_sha256(result)
    return result


def _with_result_sha256(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("result_sha256", None)
    result["result_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def _verify_artifact_sha256(value: Mapping[str, Any]) -> None:
    observed = value.get("artifact_sha256")
    if not isinstance(observed, str) or not _HASH_RE.fullmatch(observed):
        raise ExecutionEvidenceError("artifact_sha256 is missing or malformed")
    if observed != _artifact_sha256(value):
        raise ExecutionEvidenceError("artifact_sha256 does not match canonical content")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _safe_relative_path(root: Path, relative: str | Path) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ExecutionEvidenceError("V5 execution inputs must be workspace-relative")
    normalized_parts = {
        part.casefold().replace(" ", "_") for part in candidate.parts
    }
    if normalized_parts & _PROTECTED_PATH_PARTS:
        raise ExecutionEvidenceError("protected or confirmation-label input is prohibited")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ExecutionEvidenceError("execution input escapes the workspace")
    return resolved


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExecutionEvidenceError(f"{label} must be an object")
    return value


def load_registration(root: Path | str) -> tuple[dict[str, Any], str]:
    """Load and validate the controlling V5 registration without label access."""

    workspace = Path(root).resolve()
    config_path = _safe_relative_path(workspace, CONFIG_RELATIVE_PATH)
    registration = _require_mapping(_json(config_path), "V5 registration")
    registration = deepcopy(dict(registration))

    if registration.get("schema_version") != "klax-v5-four-colony-goal-v1":
        raise ExecutionEvidenceError("unexpected V5 registration schema")
    if registration.get("offline_only") is not True:
        raise ExecutionEvidenceError("V5 must remain offline")
    if registration.get("live_or_paper_orders_authorized") is not False:
        raise ExecutionEvidenceError("V5 does not authorize live or paper orders")
    if registration.get("prospective_live_collection_authorized") is not False:
        raise ExecutionEvidenceError("V5 does not authorize prospective collection")

    network = _require_mapping(registration.get("network_policy"), "network_policy")
    if (network.get("experiment_execution") != "network_denied"
            or network.get("live_feeds") is not False
            or network.get("websocket_recorders") is not False
            or network.get("latest_price_fetches") is not False):
        raise ExecutionEvidenceError("V5 experiment network boundary changed")

    target = _require_mapping(registration.get("target"), "target")
    if (target.get("series") != "KXHIGHLAX"
            or target.get("station") != "KLAX"
            or target.get("decision_time_utc") != "18:00"
            or target.get("quantity_contracts_primary") != 1
            or target.get("maximum_positions_per_event") != 1):
        raise ExecutionEvidenceError("V5 execution target changed")

    colony = _require_mapping(
        _require_mapping(registration.get("colonies"), "colonies").get("execution_evidence"),
        "execution_evidence colony",
    )
    if (colony.get("primary_order_type") != "one_contract_marketable_ioc_limit"
            or colony.get("signal_to_arrival_seconds_primary") != 5
            or colony.get("signal_to_arrival_seconds_sensitivities") != [30, 60]
            or colony.get("minimum_outcome_blind_event_window_coverage") != 0.9):
        raise ExecutionEvidenceError("registered execution replay changed")
    if set(colony.get("promotion_sources", ())) != PROMOTION_SOURCE_KINDS:
        raise ExecutionEvidenceError("registered promotion source language changed")
    if set(colony.get("diagnostic_only_sources", ())) != DIAGNOSTIC_SOURCE_KINDS:
        raise ExecutionEvidenceError("registered diagnostic source language changed")

    goal_path = _safe_relative_path(workspace, registration["goal_document"])
    if _file_sha256(goal_path) != registration.get("goal_document_sha256"):
        raise ExecutionEvidenceError("V5 goal document hash mismatch")
    return registration, _file_sha256(config_path)


def _source_capability_result(
    source: Mapping[str, Any],
    promotion_sources: set[str],
    diagnostic_sources: set[str],
) -> dict[str, Any]:
    record = deepcopy(dict(source))
    if record.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise ExecutionEvidenceError("unexpected execution source record schema")
    source_id = record.get("source_id")
    source_kind = record.get("source_kind")
    if not isinstance(source_id, str) or not source_id:
        raise ExecutionEvidenceError("source_id is required")
    if source_kind not in promotion_sources | diagnostic_sources:
        raise ExecutionEvidenceError(f"unregistered execution source kind: {source_kind!r}")
    if record.get("network_used_during_census") is not False:
        raise ExecutionEvidenceError("source census must run without network access")
    if record.get("protected_final_read") is not False:
        raise ExecutionEvidenceError("source census may not read protected data")
    if record.get("campaign_orders_placed") is not False:
        raise ExecutionEvidenceError("source census may not place orders")

    path_text = record.get("path")
    if not isinstance(path_text, str) or not path_text:
        raise ExecutionEvidenceError("source path is required")
    path_parts = {part.casefold().replace(" ", "_") for part in Path(path_text).parts}
    if path_parts & _PROTECTED_PATH_PARTS:
        raise ExecutionEvidenceError("execution source points at protected data")

    sha256 = record.get("content_sha256")
    if not isinstance(sha256, str) or not _HASH_RE.fullmatch(sha256):
        raise ExecutionEvidenceError("source content_sha256 is malformed")
    capabilities = _require_mapping(record.get("capabilities"), "source capabilities")

    failures: list[str] = []
    promotion_eligible = False
    if source_kind in diagnostic_sources:
        failures.append("SOURCE_KIND_REGISTERED_DIAGNOSTIC_ONLY")
        evidence_grade = "B_AGGREGATED_QUOTE" if source_kind == "one_minute_candles" else "C_PUBLIC_PRINT"
    else:
        required = list(_COMMON_PROMOTION_CAPABILITIES)
        if source_kind == "actual_member_order_and_fill":
            required.extend(_MEMBER_FILL_PROMOTION_CAPABILITIES)
            evidence_grade = "A_ACTUAL_MEMBER_FILL"
        else:
            required.extend(_BOOK_PROMOTION_CAPABILITIES)
            evidence_grade = "A_EXECUTION_AWARE_BOOK"
        for field in required:
            if capabilities.get(field) is not True:
                failures.append(f"MISSING_CAPABILITY:{field}")
        promotion_eligible = not failures

    record["evidence_grade"] = evidence_grade
    record["promotion_eligible"] = promotion_eligible
    record["capability_failures"] = sorted(failures)
    return record


def build_source_capability_census(
    sources: Iterable[Mapping[str, Any]],
    registration: Mapping[str, Any],
    registration_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic, outcome-blind execution source census."""

    colony = _require_mapping(
        _require_mapping(registration.get("colonies"), "colonies").get("execution_evidence"),
        "execution_evidence colony",
    )
    promotion_sources = set(colony["promotion_sources"])
    diagnostic_sources = set(colony["diagnostic_only_sources"])
    evaluated = [
        _source_capability_result(source, promotion_sources, diagnostic_sources)
        for source in sources
    ]
    evaluated.sort(key=lambda row: row["source_id"])
    source_ids = [row["source_id"] for row in evaluated]
    if len(source_ids) != len(set(source_ids)):
        raise ExecutionEvidenceError("duplicate execution source_id")

    promotion_count = sum(row["promotion_eligible"] is True for row in evaluated)
    diagnostic_count = sum(row["source_kind"] in diagnostic_sources for row in evaluated)
    result = {
        "schema_version": CENSUS_SCHEMA_VERSION,
        "campaign_family": registration["campaign_family"],
        "colony": "execution_evidence",
        "registration_sha256": registration_sha256,
        "source_count": len(evaluated),
        "promotion_eligible_source_count": promotion_count,
        "diagnostic_only_source_count": diagnostic_count,
        "status": "PASS" if promotion_count else "NO_PROMOTION_SOURCE",
        "sources": evaluated,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return _with_artifact_sha256(result)


def _current_source_records(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    manifest_path = _safe_relative_path(root, MINUTE_MANIFEST_RELATIVE_PATH)
    manifest_sha = _file_sha256(manifest_path)
    common = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "path": MINUTE_MANIFEST_RELATIVE_PATH.as_posix(),
        "content_sha256": manifest_sha,
        "network_used_during_census": False,
        "protected_final_read": False,
        "campaign_orders_placed": False,
    }
    candle_capabilities = {
        "immutable": True,
        "content_hash_verified": int(manifest.get("verified_raw_source_files", 0)) > 0,
        "market_identity": True,
        "exchange_timestamp": True,
        "timestamp_semantics_documented": True,
        "side": True,
        "price": True,
        "quantity_at_price": False,
        "executed_quantity": False,
        "snapshot_or_sequence_identity": False,
        "update_semantics_documented": False,
        "contemporaneous_availability_timestamp": False,
        "gap_free": False,
        "historical_coverage_documented": True,
        "completeness_documented": True,
        "order_identity": False,
        "fill_identity": False,
        "actual_fill_binding": False,
        "fee_at_fill": False,
    }
    trade_capabilities = dict(candle_capabilities)
    trade_capabilities.update({
        "executed_quantity": True,
        "quantity_at_price": False,
    })
    return [
        {
            **common,
            "source_id": "v3-frozen-one-minute-candles",
            "source_kind": "one_minute_candles",
            "row_count": int(manifest.get("one_minute_candle_rows", 0)),
            "capabilities": candle_capabilities,
        },
        {
            **common,
            "source_id": "v3-frozen-public-trade-prints",
            "source_kind": "public_trade_prints",
            "row_count": int(manifest.get("public_trade_rows", 0)),
            "capabilities": trade_capabilities,
        },
    ]


def audit_current_execution_evidence(
    root: Path | str,
    registration: Mapping[str, Any],
    registration_sha256: str,
    census: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit only exposed V4 development evidence; never open protected labels."""

    _verify_artifact_sha256(census)
    workspace = Path(root).resolve()
    leader = _require_mapping(registration.get("frozen_probability_leader"), "frozen leader")
    compiled_path = _safe_relative_path(workspace, leader["source_artifact"])
    compiled = _require_mapping(_json(compiled_path), "compiled V4 candidate")
    ledger_path = compiled_path.with_name("ledger.json")
    ledger = _require_mapping(_json(ledger_path), "V4 candidate ledger")
    minute_path = _safe_relative_path(workspace, MINUTE_MANIFEST_RELATIVE_PATH)
    minute = _require_mapping(_json(minute_path), "V3 minute-market manifest")

    identity_checks = {
        "research_plan_sha256": compiled.get("research_plan_sha256") == leader.get("research_plan_sha256"),
        "fitted_model_sha256": compiled.get("fitted_model_sha256") == leader.get("fitted_model_sha256"),
        "model_state_sha256": (
            _require_mapping(compiled.get("fitted_model_state"), "fitted_model_state").get("state_sha256")
            == leader.get("model_state_sha256")
        ),
        "ledger_plan_sha256": ledger.get("research_plan_sha256") == leader.get("research_plan_sha256"),
    }

    decisions = ledger.get("decisions")
    settlements = ledger.get("settlements")
    if not isinstance(decisions, list) or not isinstance(settlements, list):
        raise ExecutionEvidenceError("candidate ledger decisions and settlements must be arrays")
    accepted = [row for row in decisions if row.get("status") == "ACCEPTED"]
    accepted_events = {row.get("event_ticker") for row in accepted}
    settled_events = {row.get("event_ticker") for row in settlements}
    evidence_grades = sorted({str(row.get("evidence_grade")) for row in accepted})
    fee_verified_count = sum(row.get("historical_fees_verified") is True for row in accepted)
    assumed_fill_count = sum(row.get("reason") == "ASSUMED_FILL_SCENARIO" for row in accepted)

    prior = _require_mapping(registration.get("prior_evidence"), "prior_evidence")
    integrity_checks = {
        **identity_checks,
        "goal_source_manifest_offline": minute.get("network_used") is False,
        "source_manifest_protected_read_denied": minute.get("protected_final_read") is False,
        "source_manifest_depth_absent": minute.get("historical_depth_available") is False,
        "leader_campaign_orders_absent": ledger.get("actual_orders_placed") is False,
        "leader_assumed_fill_only": ledger.get("historical_assumed_fill_only") is True,
        "accepted_decision_count_matches": len(accepted) == 17,
        "event_selection_count_matches": len(accepted_events) == 13,
        "settlement_count_matches": len(settlements) == 13,
        "event_selection_reconciles": accepted_events == settled_events,
        "all_accepted_entries_are_assumed_fills": assumed_fill_count == len(accepted),
        "no_accepted_entry_has_verified_historical_fee": fee_verified_count == 0,
        "prior_snapshot_count_matches": prior.get("frozen_snapshot_count") == 3150,
        "prior_depth_count_is_zero": prior.get("historical_depth_snapshot_count") == 0,
        "prior_fill_support_count_is_zero": prior.get("hypothetical_fill_supported_snapshot_count") == 0,
    }
    integrity_passed = all(integrity_checks.values())
    promotion_source_count = int(census.get("promotion_eligible_source_count", 0))

    blockers = [
        "NO_HISTORICAL_PRICE_LEVEL_SIZE",
        "NO_GAP_FREE_ORDERBOOK_SEQUENCE",
        "NO_CONTEMPORANEOUS_AVAILABILITY_TIMESTAMP",
        "CANDLE_CLOSES_REGISTERED_DIAGNOSTIC_ONLY",
        "PUBLIC_TRADES_REGISTERED_DIAGNOSTIC_ONLY",
    ]
    if not integrity_passed:
        blockers.insert(0, "CURRENT_EVIDENCE_INTEGRITY_MISMATCH")

    result = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "campaign_family": registration["campaign_family"],
        "colony": "execution_evidence",
        "registration_sha256": registration_sha256,
        "candidate_id": leader["candidate_id"],
        "research_plan_sha256": leader["research_plan_sha256"],
        "source_bindings": {
            "compiled_manifest": {
                "path": leader["source_artifact"],
                "sha256": _file_sha256(compiled_path),
            },
            "ledger": {
                "path": ledger_path.relative_to(workspace).as_posix(),
                "sha256": _file_sha256(ledger_path),
            },
            "minute_market_manifest": {
                "path": MINUTE_MANIFEST_RELATIVE_PATH.as_posix(),
                "sha256": _file_sha256(minute_path),
            },
        },
        "integrity_checks": integrity_checks,
        "integrity_passed": integrity_passed,
        "current_market_evidence": {
            "one_minute_candle_rows": int(minute.get("one_minute_candle_rows", 0)),
            "public_trade_rows": int(minute.get("public_trade_rows", 0)),
            "frozen_contract_decision_snapshots": int(prior["frozen_snapshot_count"]),
            "historical_depth_snapshots": int(prior["historical_depth_snapshot_count"]),
            "hypothetical_fill_supported_snapshots": int(prior["hypothetical_fill_supported_snapshot_count"]),
            "accepted_contract_decisions": len(accepted),
            "event_level_selections": len(accepted_events),
            "settled_assumed_fill_trades": len(settlements),
            "accepted_evidence_grades": evidence_grades,
            "accepted_historical_fee_verified_count": fee_verified_count,
            "promotion_eligible_source_count": promotion_source_count,
        },
        "book_reconstruction_valid": False,
        "promotion_grade_event_window_coverage": 0.0,
        "promotion_eligible_selected_trade_count": 0,
        "selected_trade_execution_fields_complete": False,
        "blockers": blockers,
        "current_verdict": (
            "DATA_INTEGRITY_FAILURE" if not integrity_passed
            else "EXECUTION_EVIDENCE_UNAVAILABLE"
        ),
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return _with_artifact_sha256(result)


def evaluate_execution_readiness(
    registration: Mapping[str, Any],
    registration_sha256: str,
    census: Mapping[str, Any],
    audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the execution colony gate without authorizing label access."""

    _verify_artifact_sha256(census)
    _verify_artifact_sha256(audit)
    for name, artifact in (("census", census), ("audit", audit)):
        if artifact.get("registration_sha256") != registration_sha256:
            raise ExecutionEvidenceError(f"{name} registration binding mismatch")
        if (artifact.get("network_used") is not False
                or artifact.get("protected_final_read") is not False
                or artifact.get("actual_orders_placed") is not False):
            raise ExecutionEvidenceError(f"{name} violates the offline safety boundary")

    colony = _require_mapping(
        _require_mapping(registration.get("colonies"), "colonies").get("execution_evidence"),
        "execution_evidence colony",
    )
    barrier = _require_mapping(registration.get("protected_label_barrier"), "protected_label_barrier")
    promotion_count = int(census.get("promotion_eligible_source_count", 0))
    coverage = float(audit.get("promotion_grade_event_window_coverage", 0.0))
    selected_count = int(audit.get("promotion_eligible_selected_trade_count", 0))

    unmet: list[str] = []
    if audit.get("integrity_passed") is not True:
        verdict = "DATA_INTEGRITY_FAILURE"
        unmet.append("CURRENT_EVIDENCE_INTEGRITY")
    elif promotion_count < 1:
        verdict = "EXECUTION_EVIDENCE_UNAVAILABLE"
        unmet.append("PROMOTION_GRADE_SOURCE")
    elif audit.get("book_reconstruction_valid") is not True:
        verdict = "BOOK_RECONSTRUCTION_INVALID"
        unmet.append("GAP_FREE_BOOK_RECONSTRUCTION")
    else:
        minimum_coverage = float(colony["minimum_outcome_blind_event_window_coverage"])
        minimum_trades = int(barrier["minimum_selected_trades"])
        if coverage < minimum_coverage:
            unmet.append("OUTCOME_BLIND_EVENT_WINDOW_COVERAGE")
        if selected_count < minimum_trades:
            unmet.append("MINIMUM_EXECUTABLE_SELECTED_TRADES")
        if audit.get("selected_trade_execution_fields_complete") is not True:
            unmet.append("COMPLETE_EXECUTION_FIELDS_FOR_EVERY_SELECTED_TRADE")
        verdict = "INSUFFICIENT_EXECUTABLE_SAMPLE" if unmet else colony["primary_pass_label"]

    passed = verdict == colony["primary_pass_label"]
    result = {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "campaign_family": registration["campaign_family"],
        "colony": "execution_evidence",
        "registration_sha256": registration_sha256,
        "source_census_sha256": census["artifact_sha256"],
        "current_evidence_audit_sha256": audit["artifact_sha256"],
        "status": "PASS" if passed else "BLOCKED",
        "verdict": verdict,
        "execution_colony_gate_passed": passed,
        "promotion_ready": passed,
        "protected_label_access_authorized": False,
        "unmet_gates": sorted(unmet),
        "registered_minimum_event_window_coverage": float(colony["minimum_outcome_blind_event_window_coverage"]),
        "observed_promotion_grade_event_window_coverage": coverage,
        "registered_minimum_selected_trades": int(barrier["minimum_selected_trades"]),
        "observed_promotion_eligible_selected_trades": selected_count,
        "network_used": False,
        "protected_final_read": False,
        "actual_orders_placed": False,
    }
    return _with_artifact_sha256(result)


def validate_execution_evidence_record(
    record: Mapping[str, Any],
    registration: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one future controller-owned execution record.

    This is a schema and safety check, not a fill simulator.  It makes it
    impossible for a candle or public trade to be marked promotion eligible.
    """

    row = deepcopy(dict(record))
    if row.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ExecutionEvidenceError("unexpected execution evidence schema")
    required = (
        "event_ticker", "ticker", "decision_at_utc", "arrival_at_utc",
        "source_id", "source_kind", "side", "observed_bid_cents",
        "observed_ask_cents", "observed_quote_timestamp_utc",
        "displayed_quantity_contracts", "price_source_sha256",
        "book_reconstruction_valid", "sequence_gap",
        "contemporaneous_availability_verified", "promotion_eligible",
        "network_used", "protected_final_read", "actual_orders_placed",
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise ExecutionEvidenceError(f"execution evidence fields missing: {sorted(missing)}")
    if (row["network_used"] is not False
            or row["protected_final_read"] is not False
            or row["actual_orders_placed"] is not False):
        raise ExecutionEvidenceError("execution evidence violates offline safety boundary")
    if row["source_kind"] in DIAGNOSTIC_SOURCE_KINDS and row["promotion_eligible"] is True:
        raise ExecutionEvidenceError("candles and public trades cannot support promotion")
    if row["source_kind"] not in DIAGNOSTIC_SOURCE_KINDS | PROMOTION_SOURCE_KINDS:
        raise ExecutionEvidenceError("execution evidence uses an unregistered source kind")
    if row["side"] not in {"YES", "NO"}:
        raise ExecutionEvidenceError("execution side must be YES or NO")

    try:
        bid = int(row["observed_bid_cents"])
        ask = int(row["observed_ask_cents"])
        quantity = float(row["displayed_quantity_contracts"])
    except (TypeError, ValueError) as error:
        raise ExecutionEvidenceError("execution price or quantity is malformed") from error
    if not (0 <= bid <= ask <= 100):
        raise ExecutionEvidenceError("execution bid/ask is invalid")
    if quantity < 0:
        raise ExecutionEvidenceError("displayed quantity cannot be negative")
    if not _HASH_RE.fullmatch(str(row["price_source_sha256"])):
        raise ExecutionEvidenceError("price_source_sha256 is malformed")

    def parse_utc(value: Any, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise ExecutionEvidenceError(f"{field} is not an ISO timestamp") from error
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ExecutionEvidenceError(f"{field} must be UTC")
        return parsed

    decision = parse_utc(row["decision_at_utc"], "decision_at_utc")
    arrival = parse_utc(row["arrival_at_utc"], "arrival_at_utc")
    quote_time = parse_utc(row["observed_quote_timestamp_utc"], "observed_quote_timestamp_utc")
    registered_delay = int(registration["colonies"]["execution_evidence"]["signal_to_arrival_seconds_primary"])
    if int((arrival - decision).total_seconds()) != registered_delay:
        raise ExecutionEvidenceError("execution record does not use the registered primary delay")
    if quote_time > arrival:
        raise ExecutionEvidenceError("future book state used for execution")

    if row["promotion_eligible"] is True:
        if row["source_kind"] not in PROMOTION_SOURCE_KINDS:
            raise ExecutionEvidenceError("promotion record uses a nonpromotion source")
        if (row["book_reconstruction_valid"] is not True
                or row["sequence_gap"] is not False
                or row["contemporaneous_availability_verified"] is not True
                or quantity < 1):
            raise ExecutionEvidenceError("promotion record lacks executable book evidence")
    return row


def run_current_execution_readiness(root: Path | str) -> dict[str, Any]:
    """Produce the complete current V5 execution readiness bundle."""

    workspace = Path(root).resolve()
    registration, registration_sha = load_registration(workspace)
    minute = _require_mapping(
        _json(_safe_relative_path(workspace, MINUTE_MANIFEST_RELATIVE_PATH)),
        "V3 minute-market manifest",
    )
    census = build_source_capability_census(
        _current_source_records(workspace, minute), registration, registration_sha,
    )
    audit = audit_current_execution_evidence(
        workspace, registration, registration_sha, census,
    )
    verdict = evaluate_execution_readiness(
        registration, registration_sha, census, audit,
    )
    return {"census": census, "audit": audit, "verdict": verdict}


def audit_execution_evidence(
    root: Path,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the controller-facing, self-hashed execution colony result.

    The function is pure with respect to the workspace: it reads the registered
    V5 configuration and exposed V4 development evidence, but writes nothing.
    A caller-supplied configuration must be canonically identical to the
    registered file.  Any error becomes a fail-closed integrity verdict.
    """

    workspace = Path(root).resolve()
    try:
        registered, _ = load_registration(workspace)
        if config is not None:
            if not isinstance(config, Mapping):
                raise ExecutionEvidenceError("V5 controller config is not an object")
            if _canonical_bytes(config) != _canonical_bytes(registered):
                raise ExecutionEvidenceError("supplied config differs from frozen registration")
        bundle = run_current_execution_readiness(workspace)
        verdict = bundle["verdict"]
        audit = bundle["audit"]
        promotion_ready = verdict["promotion_ready"] is True
        failure_reasons = [] if promotion_ready else sorted(set(
            [str(item) for item in verdict.get("unmet_gates", ())]
            + [str(item) for item in audit.get("blockers", ())]
        ))
        body = {
            "version": CONTROLLER_RESULT_VERSION,
            "colony": "execution_evidence",
            "status": verdict["verdict"],
            "promotion_ready": promotion_ready,
            "failure_reasons": failure_reasons,
            "source_census_sha256": bundle["census"]["artifact_sha256"],
            "current_evidence_audit_sha256": audit["artifact_sha256"],
            "execution_readiness_sha256": verdict["artifact_sha256"],
            "evidence_counts": deepcopy(audit["current_market_evidence"]),
            "protected_confirmation_labels_read": False,
            "live_or_paper_orders_authorized": False,
            "actual_orders_placed": False,
            "network_used": False,
        }
    except (ExecutionEvidenceError, OSError, ValueError, KeyError, TypeError) as error:
        body = {
            "version": CONTROLLER_RESULT_VERSION,
            "colony": "execution_evidence",
            "status": "DATA_INTEGRITY_FAILURE",
            "promotion_ready": False,
            "failure_reasons": [f"{type(error).__name__}:{error}"],
            "protected_confirmation_labels_read": False,
            "live_or_paper_orders_authorized": False,
            "actual_orders_placed": False,
            "network_used": False,
        }
    return _with_result_sha256(body)


def cross_review_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    """Independently validate a controller-facing execution colony result."""

    reasons: list[str] = []
    if not isinstance(value, Mapping):
        value = {}
        reasons.append("RECORD_NOT_OBJECT")
    required = {
        "version", "colony", "status", "promotion_ready", "failure_reasons",
        "protected_confirmation_labels_read", "live_or_paper_orders_authorized",
        "actual_orders_placed", "network_used", "result_sha256",
    }
    if not required <= set(value):
        reasons.append("REQUIRED_FIELD_MISSING")
    if value.get("version") != CONTROLLER_RESULT_VERSION:
        reasons.append("VERSION_MISMATCH")
    if value.get("colony") != "execution_evidence":
        reasons.append("COLONY_IDENTITY_MISMATCH")
    allowed_statuses = {
        "PROVISIONAL_EXECUTION_SUPPORTED",
        "EXECUTION_EVIDENCE_UNAVAILABLE",
        "BOOK_RECONSTRUCTION_INVALID",
        "EXECUTABLE_EDGE_DISAPPEARS",
        "INSUFFICIENT_EXECUTABLE_SAMPLE",
        "EXECUTION_EDGE_UNSTABLE",
        "DATA_INTEGRITY_FAILURE",
    }
    status = value.get("status")
    if status not in allowed_statuses:
        reasons.append("STATUS_INVALID")
    failures = value.get("failure_reasons")
    if (not isinstance(failures, list)
            or any(not isinstance(item, str) or not item for item in failures)):
        reasons.append("FAILURE_REASONS_INVALID")
    promotion_ready = value.get("promotion_ready")
    if type(promotion_ready) is not bool:
        reasons.append("PROMOTION_READY_INVALID")
    if promotion_ready is True and (
        status != "PROVISIONAL_EXECUTION_SUPPORTED" or failures
    ):
        reasons.append("PROMOTION_CLAIM_INCONSISTENT")
    if promotion_ready is False and status == "PROVISIONAL_EXECUTION_SUPPORTED":
        reasons.append("PASS_STATUS_WITHOUT_PROMOTION_READY")
    if value.get("protected_confirmation_labels_read") is not False:
        reasons.append("PROTECTED_CONFIRMATION_LABEL_READ")
    if value.get("live_or_paper_orders_authorized") is not False:
        reasons.append("ORDER_AUTHORIZATION_DETECTED")
    if value.get("actual_orders_placed") is not False:
        reasons.append("ORDER_ACTIVITY_DETECTED")
    if value.get("network_used") is not False:
        reasons.append("NETWORK_ACTIVITY_DETECTED")
    supplied_hash = value.get("result_sha256")
    unhashed = dict(value)
    unhashed.pop("result_sha256", None)
    expected_hash = hashlib.sha256(_canonical_bytes(unhashed)).hexdigest()
    if not isinstance(supplied_hash, str) or supplied_hash != expected_hash:
        reasons.append("SELF_HASH_MISMATCH")

    result = {
        "version": CONTROLLER_REVIEW_VERSION,
        "colony": "execution_evidence",
        "reviewed_result_sha256": supplied_hash,
        "status": "PASS" if not reasons else "REJECT",
        "promotion_ready": bool(
            not reasons
            and promotion_ready is True
            and status == "PROVISIONAL_EXECUTION_SUPPORTED"
        ),
        "failure_reasons": sorted(set(reasons)),
        "protected_confirmation_labels_read": False,
        "live_or_paper_orders_authorized": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    return _with_result_sha256(result)


def write_current_execution_artifacts(
    root: Path | str,
    output_relative: str | Path = Path("v5/artifacts"),
) -> dict[str, Path]:
    """Atomically save deterministic current-evidence artifacts under ``v5``."""

    workspace = Path(root).resolve()
    output = _safe_relative_path(workspace, output_relative)
    relative_output = output.relative_to(workspace)
    if not relative_output.parts or relative_output.parts[0].casefold() != "v5":
        raise ExecutionEvidenceError("V5 execution artifacts must be written under v5/")
    output.mkdir(parents=True, exist_ok=True)
    bundle = run_current_execution_readiness(workspace)
    names = {
        "census": "current-execution-source-capability-census.json",
        "audit": "current-execution-evidence-audit.json",
        "verdict": "current-execution-readiness-verdict.json",
    }
    written: dict[str, Path] = {}
    for key, filename in names.items():
        destination = output / filename
        temporary = destination.with_suffix(destination.suffix + ".pending")
        temporary.write_bytes(_canonical_bytes(bundle[key]) + b"\n")
        temporary.replace(destination)
        written[key] = destination
    return written


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build V5 execution readiness artifacts")
    parser.add_argument("--root", default=".")
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    if arguments.write:
        paths = write_current_execution_artifacts(arguments.root)
        print(json.dumps({key: str(path) for key, path in paths.items()}, sort_keys=True))
    else:
        print(json.dumps(run_current_execution_readiness(arguments.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
