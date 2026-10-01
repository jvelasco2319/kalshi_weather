"""Deterministic, offline analysis primitives for the V5P campaign.

The module has no network, acquisition, broker, or order interface.  It accepts
only already-normalized, hash-bound records and never opens confirmation labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from math import floor, isfinite
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from klax_lab.candidate_model_v3 import (
    FittedCandidateModelV3,
    fitted_candidate_model_from_state,
)
from v5.probability import SOURCE_PATH, validate_frozen_leader


UNIVERSE_VERSION = "klax-v5p-outcome-blind-universe-v1"
PROPOSAL_VERSION = "klax-v5p-development-proposal-v1"
LEADER_BINDING_VERSION = "klax-v5p-frozen-leader-binding-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_GRADES = {"A", "B_PLUS", "B", "C"}
_OUTCOME_TOKENS = {
    "label", "outcome", "result", "winner", "payout", "settled_value",
    "settlement_value", "profit", "realized_return", "brier", "crps",
    "log_loss",
}
_UNIVERSE_FIELDS = {
    "climate_date", "event_ticker", "decision_time_utc",
    "probability_ready", "market_ready", "fee_rule_ready",
    "settlement_source_ready", "station_identity_screen",
    "execution_evidence_grade", "source_bindings", "exclusion_reasons",
    "settlement_evidence_grade", "settlement_rule_revision_exact",
    "date_promotion_ready",
}
_PROPOSAL_FIELDS = {
    "version", "proposal_id", "parent_proposal_id", "hypothesis",
    "change_type", "parameters", "development_only", "refit_performed",
    "protected_confirmation_labels_read", "actual_orders_placed",
}
_CHANGE_TYPES = {
    "evidence_filter", "entry_threshold", "timing_sensitivity",
    "fee_scenario", "abstention_policy",
}
_OBJECTIVES = (
    "net_return", "calibration", "temporal_stability", "evidence_quality",
)


class V5PAnalysisError(ValueError):
    """Raised when an offline V5P analysis record violates the registration."""


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_bound(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != field}
    return {**body, field: canonical_hash(body)}


def _verify_hash_bound(value: Mapping[str, Any], field: str) -> None:
    claimed = value.get(field)
    body = {key: item for key, item in value.items() if key != field}
    if not isinstance(claimed, str) or claimed != canonical_hash(body):
        raise V5PAnalysisError(f"{field} mismatch")


def sample_label(independent_days: int) -> str:
    if isinstance(independent_days, bool) or not isinstance(independent_days, int):
        raise V5PAnalysisError("independent day count must be an integer")
    if independent_days < 0:
        raise V5PAnalysisError("independent day count cannot be negative")
    if independent_days == 0:
        return "NO_PARTIAL_EVIDENCE_SAMPLE"
    if independent_days <= 9:
        return "ANECDOTAL_PARTIAL_EVIDENCE"
    if independent_days <= 29:
        return "PRELIMINARY_PARTIAL_EVIDENCE"
    return "SUPPORTED_PARTIAL_EVIDENCE"


def _reject_outcome_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            if any(token in key for token in _OUTCOME_TOKENS):
                raise V5PAnalysisError(f"outcome-bearing field denied: {path}.{raw_key}")
            _reject_outcome_fields(item, f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_outcome_fields(item, f"{path}[{index}]")


def _validate_source_bindings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise V5PAnalysisError("source_bindings must be an array")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "bytes", "sha256"}:
            raise V5PAnalysisError("source binding fields differ")
        if (
            not isinstance(item["path"], str)
            or Path(item["path"]).is_absolute()
            or ".." in Path(item["path"]).parts
            or type(item["bytes"]) is not int
            or item["bytes"] < 0
            or not isinstance(item["sha256"], str)
            or not _SHA256.fullmatch(item["sha256"])
        ):
            raise V5PAnalysisError("source binding is malformed")
        lowered = [part.casefold().replace("-", "_") for part in Path(item["path"]).parts]
        if any(
            any(token in part for token in (
                "holdout", "label", "outcome", "protected", "settlement_target",
            ))
            for part in lowered
        ):
            raise V5PAnalysisError("source binding enters protected storage")
        result.append(dict(item))
    return sorted(result, key=lambda row: row["path"])


def freeze_outcome_blind_universe(
    rows: Sequence[Mapping[str, Any]],
    *,
    config_sha256: str,
    acquisition_closed: bool,
) -> dict[str, Any]:
    """Freeze dates before any corresponding outcome is opened.

    All candidate rows, including excluded rows, are retained in the ledger.
    The earliest 70 percent of eligible dates become development and the latest
    30 percent become the one-shot holdout.
    """

    if not _SHA256.fullmatch(str(config_sha256)):
        raise V5PAnalysisError("config_sha256 is malformed")
    if type(acquisition_closed) is not bool:
        raise V5PAnalysisError("acquisition_closed must be boolean")
    normalized: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    seen_events: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping) or set(raw) != _UNIVERSE_FIELDS:
            raise V5PAnalysisError("universe candidate fields differ")
        _reject_outcome_fields(raw)
        climate_date = str(raw["climate_date"])
        try:
            parsed = date.fromisoformat(climate_date)
        except ValueError as exc:
            raise V5PAnalysisError("invalid universe climate_date") from exc
        if not date(2025, 7, 1) <= parsed <= date(2026, 8, 31):
            raise V5PAnalysisError("universe date is outside the registered window")
        event = str(raw["event_ticker"])
        if not event.startswith("KXHIGHLAX-"):
            raise V5PAnalysisError("universe event is not KXHIGHLAX")
        if climate_date in seen_dates or event in seen_events:
            raise V5PAnalysisError("universe date/event is duplicated")
        seen_dates.add(climate_date)
        seen_events.add(event)
        if raw["decision_time_utc"] != "18:00":
            raise V5PAnalysisError("universe decision time differs")
        booleans = (
            "probability_ready", "market_ready", "fee_rule_ready",
            "settlement_source_ready", "station_identity_screen",
        )
        if any(type(raw[field]) is not bool for field in booleans):
            raise V5PAnalysisError("universe readiness fields must be boolean")
        grade = str(raw["execution_evidence_grade"])
        if grade not in _EVIDENCE_GRADES:
            raise V5PAnalysisError("execution evidence grade is invalid")
        settlement_grade = str(raw["settlement_evidence_grade"])
        if settlement_grade not in {
            "EXACT_REVISION", "BOUNDED_COMMON_NWS_LAX_SEMANTICS",
        }:
            raise V5PAnalysisError("settlement evidence grade is invalid")
        if (
            type(raw["settlement_rule_revision_exact"]) is not bool
            or type(raw["date_promotion_ready"]) is not bool
            or settlement_grade == "BOUNDED_COMMON_NWS_LAX_SEMANTICS"
            and (
                raw["settlement_rule_revision_exact"] is not False
                or raw["date_promotion_ready"] is not False
            )
            or settlement_grade == "EXACT_REVISION"
            and (
                raw["settlement_rule_revision_exact"] is not True
                or raw["date_promotion_ready"] is not True
            )
        ):
            raise V5PAnalysisError("settlement promotion fields differ")
        reasons = raw["exclusion_reasons"]
        if (
            not isinstance(reasons, list)
            or any(not isinstance(item, str) or not item for item in reasons)
            or reasons != sorted(set(reasons))
        ):
            raise V5PAnalysisError("exclusion reasons must be sorted unique strings")
        computed_reasons = []
        mapping = {
            "probability_ready": "probability_cache_missing",
            "market_ready": "decision_window_market_evidence_missing",
            "fee_rule_ready": "fee_rule_evidence_missing",
            "settlement_source_ready": "settlement_source_missing",
            "station_identity_screen": "station_identity_failed",
        }
        for field, reason in mapping.items():
            if raw[field] is False:
                computed_reasons.append(reason)
        if reasons != sorted(computed_reasons):
            raise V5PAnalysisError("exclusion reasons do not match readiness fields")
        normalized.append({
            **{key: raw[key] for key in _UNIVERSE_FIELDS if key != "source_bindings"},
            "source_bindings": _validate_source_bindings(raw["source_bindings"]),
            "eligible": not computed_reasons,
        })

    normalized.sort(key=lambda row: (row["climate_date"], row["event_ticker"]))
    eligible = [row for row in normalized if row["eligible"]]
    count = len(eligible)
    if acquisition_closed:
        if count <= 1:
            development_count = 0
        else:
            development_count = max(1, min(count - 1, floor(count * 0.70)))
        development = eligible[:development_count]
        holdout = eligible[development_count:]
        split_by_event = {
            row["event_ticker"]: "development" for row in development
        } | {
            row["event_ticker"]: "holdout" for row in holdout
        }
    else:
        # Rolling acquisition may publish outcome-blind coverage and expected-
        # value diagnostics.  It never creates or opens a provisional holdout,
        # and it cannot tune a proposal.  The final 70/30 split is recomputed
        # from the closed acquisition universe.
        development = []
        holdout = []
        split_by_event = {
            row["event_ticker"]: "rolling_descriptive" for row in eligible
        }
    records = [
        {
            **row,
            "partition": split_by_event.get(row["event_ticker"], "excluded"),
        }
        for row in normalized
    ]
    body = {
        "version": UNIVERSE_VERSION,
        "config_sha256": config_sha256,
        "acquisition_closed": acquisition_closed,
        "outcome_blind": True,
        "date_start": "2025-07-01",
        "date_end": "2026-08-31",
        "decision_time_utc": "18:00",
        "split_method": (
            "chronological_earliest_70_latest_30"
            if acquisition_closed else "not_frozen_during_rolling_acquisition"
        ),
        "candidate_date_count": len(records),
        "eligible_date_count": count,
        "excluded_date_count": len(records) - count,
        "development_date_count": len(development),
        "holdout_date_count": len(holdout),
        "development_dates": [row["climate_date"] for row in development],
        "holdout_dates": [row["climate_date"] for row in holdout],
        "eligible_sample_label": sample_label(count),
        "optimization_permitted": acquisition_closed and count >= 10,
        "partial_or_bounded_evidence_present": any(
            row["settlement_rule_revision_exact"] is False for row in eligible
        ),
        "promotion_ready": (
            acquisition_closed
            and count >= 30
            and all(row["date_promotion_ready"] is True for row in eligible)
        ),
        "rolling_descriptive_only": not acquisition_closed,
        "holdout_access_authorized": False,
        "records": records,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    return _hash_bound(body, "universe_sha256")


def verify_universe(value: Mapping[str, Any]) -> None:
    _verify_hash_bound(value, "universe_sha256")
    if (
        value.get("version") != UNIVERSE_VERSION
        or value.get("outcome_blind") is not True
        or value.get("holdout_access_authorized") is not False
        or value.get("protected_confirmation_labels_read") is not False
        or value.get("actual_orders_placed") is not False
        or value.get("network_used") is not False
    ):
        raise V5PAnalysisError("universe safety boundary differs")


@dataclass(frozen=True)
class FrozenLeader:
    model: FittedCandidateModelV3
    binding: dict[str, Any]


def reconstruct_frozen_leader(
    root: Path,
    config: Mapping[str, Any],
    probability_readiness: Mapping[str, Any],
) -> FrozenLeader:
    """Reconstruct the registered V4 leader without invoking any fit routine."""

    root = Path(root).resolve()
    _verify_hash_bound(probability_readiness, "readiness_sha256")
    if (
        probability_readiness.get("protected_confirmation_labels_read") is not False
        or probability_readiness.get("actual_orders_placed") is not False
        or probability_readiness.get("network_used") is not False
        or probability_readiness.get("refit_performed") is not False
    ):
        raise V5PAnalysisError("probability readiness violates offline safety")
    validation = validate_frozen_leader(root)
    if probability_readiness.get("frozen_leader_validation_sha256") != validation.get("validation_sha256"):
        raise V5PAnalysisError("V5P readiness does not bind the frozen leader validation")
    expected = config.get("frozen_candidate")
    if not isinstance(expected, Mapping):
        raise V5PAnalysisError("V5P frozen candidate registration is missing")
    source = json.loads((root / SOURCE_PATH).read_text(encoding="utf-8"))
    checks = {
        "candidate_id": validation.get("candidate_id") == expected.get("candidate_id"),
        "research_plan_sha256": source.get("research_plan_sha256") == expected.get("research_plan_sha256"),
        "fitted_model_sha256": source.get("fitted_model_sha256") == expected.get("fitted_model_sha256"),
        "model_state_sha256": source.get("fitted_model_state", {}).get("state_sha256") == expected.get("model_state_sha256"),
    }
    if not all(checks.values()):
        raise V5PAnalysisError("frozen leader identity differs from V5P registration")
    model = fitted_candidate_model_from_state(source["fitted_model_state"])
    if model.identity != expected["fitted_model_sha256"]:
        raise V5PAnalysisError("reconstructed frozen leader identity differs")
    body = {
        "version": LEADER_BINDING_VERSION,
        "candidate_id": expected["candidate_id"],
        "research_plan_sha256": expected["research_plan_sha256"],
        "fitted_model_sha256": expected["fitted_model_sha256"],
        "model_state_sha256": expected["model_state_sha256"],
        "source_path": SOURCE_PATH.as_posix(),
        "frozen_leader_validation_sha256": validation["validation_sha256"],
        "refit_performed": False,
        "recalibration_performed": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "network_used": False,
    }
    return FrozenLeader(model=model, binding=_hash_bound(body, "binding_sha256"))


def validate_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PROPOSAL_FIELDS:
        raise V5PAnalysisError("development proposal fields differ")
    _reject_outcome_fields(value)
    if value.get("version") != PROPOSAL_VERSION:
        raise V5PAnalysisError("development proposal version differs")
    if value.get("change_type") not in _CHANGE_TYPES:
        raise V5PAnalysisError("proposal change type is not registered")
    if (
        value.get("development_only") is not True
        or value.get("refit_performed") is not False
        or value.get("protected_confirmation_labels_read") is not False
        or value.get("actual_orders_placed") is not False
    ):
        raise V5PAnalysisError("proposal violates development-only safety")
    if not isinstance(value.get("proposal_id"), str) or not value["proposal_id"]:
        raise V5PAnalysisError("proposal_id is required")
    if not isinstance(value.get("hypothesis"), str) or not value["hypothesis"].strip():
        raise V5PAnalysisError("proposal hypothesis is required")
    if not isinstance(value.get("parameters"), Mapping):
        raise V5PAnalysisError("proposal parameters must be an object")
    result = dict(value)
    result["proposal_sha256"] = canonical_hash(result)
    return result


def validate_development_metrics(value: Mapping[str, Any]) -> dict[str, float | int]:
    required = set(_OBJECTIVES) | {"selected_days", "selected_trades"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise V5PAnalysisError("development metric fields differ")
    result: dict[str, float | int] = {}
    for name in _OBJECTIVES:
        raw = value[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not isfinite(raw):
            raise V5PAnalysisError(f"development metric is not finite: {name}")
        result[name] = float(raw)
    for name in ("selected_days", "selected_trades"):
        raw = value[name]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise V5PAnalysisError(f"development count is invalid: {name}")
        result[name] = raw
    return result


def is_pareto_improvement(
    candidate: Mapping[str, Any], incumbent: Mapping[str, Any] | None,
) -> bool:
    current = validate_development_metrics(candidate)
    if incumbent is None:
        return True
    prior = validate_development_metrics(incumbent)
    not_worse = all(float(current[key]) >= float(prior[key]) for key in _OBJECTIVES)
    strictly_better = any(float(current[key]) > float(prior[key]) for key in _OBJECTIVES)
    return not_worse and strictly_better
