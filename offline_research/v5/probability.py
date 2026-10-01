"""Frozen, outcome-blind probability-colony controls for V5.

This module freezes the sole V4 probability leader and its deterministic
evaluation contract.  It can inventory whether later HRRR/GEFS inputs were
available by the registered 18:00 UTC decision time, but it deliberately has
no label loader, refit path, network client, or order interface.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any


VERSION = "klax-v5-frozen-probability-colony-v1"
CONFIG_PATH = Path("configs/v5_four_colony_verification_campaign.json")
GOAL_PATH = Path("docs/V5_FOUR_COLONY_VERIFICATION_CAMPAIGN_GOAL.md")
SOURCE_PATH = Path(
    "runs/campaigns_v4/v4-offline-20260926T212122609Z/candidates/primary/"
    "a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461/"
    "compiled_manifest.json"
)

KNOWN_HASHES = {
    CONFIG_PATH.as_posix(): "f285210dba0ee391989ceb3b1ab8ce41985898cfa99d8bf04c397d199d2955d3",
    GOAL_PATH.as_posix(): "8956e36c9f1afde241e1c6e65e753906f2ad8ba96ef2d696d502bfeecaab942d",
    SOURCE_PATH.as_posix(): "e1bcdfff09602cf63b0c598c8c320ceb931ce49afefc119f58a0da3d2f11cd0f",
    "src/klax_lab/evaluator_v3.py": "8e9632c8bdcb34fd69dca910736a61ec55d919a1d3ebb22bfbdb8c3a0ad67967",
    "src/klax_lab/domain.py": "7e6e3ff53ad55f88e5b1cf13c3d53445237a4c460b905f56582f5a23988adba0",
    "src/klax_lab/candidate_model_v3.py": "ac5b3ed84b429260da82158ea7f21712505067cbc52a9d5fd7583367d05663fc",
    "src/klax_lab/probability_v3.py": "2811ef2b7ac082f068d06d90f72e9bc6e3fc1f586c3acc26b8e15204bf08d059",
}

EXPECTED_LEADER = {
    "candidate_id": "v4-candidate-a779fd17e7b160650c8f",
    "research_plan_sha256": "a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461",
    "fitted_model_sha256": "9bf8b1ba469b1c7258108f5dd11c67c2822e7487edff5c819688d37319574f93",
    "model_state_sha256": "7c677c1264115b39d73c70cfb7162b9be138ae1c9df9ac30df472426f2a57fd3",
    "forecast_source_set": "hrrr_gefs_summary",
    "feature_set": "temperature_only",
    "probability_family": "quantile_brackets",
    "calibration_operator": "isotonic_bracket",
    "regime_model": "pooled",
    "market_residual_model": "none",
}

PASS = "FROZEN_PROBABILITY_SIGNAL_CONFIRMED"
REJECTED = "FROZEN_PROBABILITY_SIGNAL_REJECTED"
INSUFFICIENT = "INSUFFICIENT_PROBABILITY_EVIDENCE"
INTEGRITY = "INTEGRITY_FAILURE"
READY = "FROZEN_PROBABILITY_PRELABEL_READY"

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PROTECTED_KEY_TOKENS = (
    "label", "outcome", "settlement", "payout", "profit", "return",
    "score", "brier", "crps", "log_loss", "realized", "probability",
    "market_price", "fee",
)
_CENSUS_REQUIRED = {
    "climate_date", "event_ticker", "model", "decision_at_utc",
    "nominal_cycle_at_utc", "effective_available_at_utc",
    "forecast_row_count", "as_of_validated", "complete", "source_sha256",
}
_CENSUS_OPTIONAL = {"archived_last_modified_at_utc"}


class ProbabilityIntegrityError(ValueError):
    """Raised when a frozen binding or outcome-blind boundary is violated."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ProbabilityIntegrityError(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant,
        )
    except ProbabilityIntegrityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbabilityIntegrityError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ProbabilityIntegrityError(f"JSON object required: {path}")
    return value


def _hash_bound(body: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(body)
    result[field] = _canonical_hash(body)
    return result


def _safety() -> dict[str, bool]:
    return {
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "refit_performed": False,
        "recalibration_performed": False,
        "actual_orders_placed": False,
        "live_or_paper_orders_authorized": False,
    }


def _known_file_records(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    records: list[dict[str, Any]] = []
    for relative, expected in sorted(KNOWN_HASHES.items()):
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ProbabilityIntegrityError(f"path escapes project: {relative}") from exc
        if not path.is_file():
            raise ProbabilityIntegrityError(f"frozen dependency missing: {relative}")
        actual = _file_hash(path)
        if actual != expected:
            raise ProbabilityIntegrityError(f"known hash mismatch: {relative}")
        records.append({
            "path": relative, "bytes": path.stat().st_size, "sha256": actual,
        })
    return records


def validate_frozen_leader(root: Path) -> dict[str, Any]:
    """Validate the sole promotable V4 leader and every frozen dependency."""
    root = Path(root).resolve()
    records = _known_file_records(root)
    config = _load_object(root / CONFIG_PATH)
    leader = config.get("frozen_probability_leader")
    if not isinstance(leader, dict):
        raise ProbabilityIntegrityError("frozen_probability_leader is missing")
    for key, expected in EXPECTED_LEADER.items():
        if leader.get(key) != expected:
            raise ProbabilityIntegrityError(f"leader binding differs: {key}")
    if leader.get("source_artifact") != SOURCE_PATH.as_posix():
        raise ProbabilityIntegrityError("leader source artifact differs")
    requirements = leader.get("transfer_requirements")
    expected_requirements = {
        "new_transfer_identity_required": True,
        "refit_performed": False,
        "retraining_permitted": False,
        "recalibration_permitted": False,
        "feature_selection_permitted": False,
        "threshold_adjustment_permitted": False,
        "decision_time_change_permitted": False,
        "promotable_candidate_identity_limit": 1,
    }
    if requirements != expected_requirements:
        raise ProbabilityIntegrityError("frozen transfer requirements differ")

    source = _load_object(root / SOURCE_PATH)
    source_state = source.get("fitted_model_state")
    source_model = source.get("model")
    if not isinstance(source_state, dict) or not isinstance(source_model, dict):
        raise ProbabilityIntegrityError("source model state is missing")
    source_checks = {
        "manifest_version": source.get("manifest_version") == "klax-v4-execution-manifest-v1",
        "research_plan_sha256": source.get("research_plan_sha256") == EXPECTED_LEADER["research_plan_sha256"],
        "fitted_model_sha256": source.get("fitted_model_sha256") == EXPECTED_LEADER["fitted_model_sha256"],
        "model_state_sha256": source_state.get("state_sha256") == EXPECTED_LEADER["model_state_sha256"],
        "forecast_source_set": source_model.get("forecast_source_set") == EXPECTED_LEADER["forecast_source_set"],
        "feature_set": source_model.get("feature_set") == EXPECTED_LEADER["feature_set"],
        "probability_family": source_model.get("probability_family") == EXPECTED_LEADER["probability_family"],
        "calibration_operator": source_model.get("calibration_operator") == EXPECTED_LEADER["calibration_operator"],
        "regime_model": source_model.get("regime_model") == EXPECTED_LEADER["regime_model"],
        "market_residual_model": source_model.get("market_residual_model") == EXPECTED_LEADER["market_residual_model"],
        "source_contains_no_development_outcomes": source.get("contains_development_labels_or_outcomes") is False,
        "source_used_no_protected_final": source.get("protected_final_used_for_fit") is False,
        "state_contains_no_development_outcomes": source_state.get("contains_development_labels_or_outcomes") is False,
        "state_contains_no_training_rows": source_state.get("contains_training_or_calibration_rows") is False,
        "state_used_no_protected_final": source_state.get("protected_final_used_for_fit") is False,
    }
    if not all(source_checks.values()):
        failed = sorted(key for key, passed in source_checks.items() if not passed)
        raise ProbabilityIntegrityError("source leader validation failed: " + ",".join(failed))
    policy = leader.get("decision_policy")
    if policy != {
        "entry_price_band_cents": [5, 80],
        "maximum_interval_width_f": 4.0,
        "maximum_price_age_minutes": 15,
        "maximum_spread_cents": 5,
        "allowed_sides": ["YES", "NO"],
        "minimum_expected_net_return": 0.1,
    }:
        raise ProbabilityIntegrityError("frozen decision policy differs")

    body = {
        "version": VERSION,
        "record_type": "frozen_probability_leader_validation",
        "candidate_id": EXPECTED_LEADER["candidate_id"],
        "leader_binding": {key: leader[key] for key in EXPECTED_LEADER},
        "source_checks": source_checks,
        "decision_time_utc": config["target"]["decision_time_utc"],
        "decision_policy": policy,
        "transfer_requirements": requirements,
        "known_hash_validation_passed": True,
        "known_file_records": records,
        **_safety(),
    }
    return _hash_bound(body, "validation_sha256")


def probability_gate_schema(root: Path) -> dict[str, Any]:
    """Return the hash-bound, registered gate contract without reading labels."""
    validate_frozen_leader(root)
    config = _load_object(Path(root) / CONFIG_PATH)
    colony = config["colonies"]["frozen_probability_validation"]
    barrier = config["protected_label_barrier"]
    body = {
        "version": VERSION,
        "record_type": "probability_gate_schema",
        "primary_score": colony["primary_score"],
        "secondary_scores": colony["secondary_scores"],
        "prelabel_sample_gates": {
            "minimum_scored_probability_events": barrier["minimum_scored_probability_events"],
            "minimum_scored_probability_events_per_fold": barrier["minimum_scored_probability_events_per_fold"],
            "chronological_fold_count": config["partitions"]["combined_confirmation_pool"]["chronological_fold_count"],
        },
        "postlabel_probability_gates": {
            "probability_sum_tolerance": colony["probability_sum_tolerance"],
            "log_loss_probability_clip": colony["log_loss_probability_clip"],
            "aggregate_crps_improvement_required": colony["aggregate_crps_improvement_required"],
            "paired_bootstrap_lower_95_above_zero_required": colony["paired_bootstrap_lower_95_above_zero_required"],
            "brier_no_worse_required": colony["brier_no_worse_required"],
            "crps_improving_folds_minimum": colony["crps_improving_folds_minimum"],
            "brier_no_worse_folds_minimum": colony["brier_no_worse_folds_minimum"],
            "final_two_folds_no_worse_required": colony["final_two_folds_no_worse_required"],
            "overall_reliability_error_maximum": colony["overall_reliability_error_maximum"],
            "selected_subset_reliability_error_maximum": colony["selected_subset_reliability_error_maximum"],
            "availability_sensitivities_hours": colony["availability_sensitivities_hours"],
            "calibration_jackknife": colony["calibration_jackknife"],
        },
        "verdicts": {
            "pass": colony["primary_pass_label"],
            "failures": colony["failure_labels"],
        },
        "adaptive_changes_permitted": {
            "refit": False, "recalibration": False, "feature_selection": False,
            "threshold_adjustment": False, "decision_time_change": False,
        },
        "promotable_candidate_identity_limit": 1,
        **_safety(),
    }
    return _hash_bound(body, "schema_sha256")


def _reject_protected_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).casefold().replace("-", "_")
            if any(token in key for token in _PROTECTED_KEY_TOKENS):
                raise ProbabilityIntegrityError(f"protected or scored field denied: {path}.{raw_key}")
            _reject_protected_keys(item, f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_protected_keys(item, f"{path}[{index}]")


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProbabilityIntegrityError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProbabilityIntegrityError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProbabilityIntegrityError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _confirmation_bounds(root: Path) -> tuple[date, date, int, int]:
    config = _load_object(Path(root) / CONFIG_PATH)
    pool = config["partitions"]["combined_confirmation_pool"]
    barrier = config["protected_label_barrier"]
    return (
        date.fromisoformat(pool["date_start"]),
        date.fromisoformat(pool["date_end"]),
        int(barrier["minimum_scored_probability_events"]),
        int(barrier["minimum_scored_probability_events_per_fold"]),
    )


def build_confirmation_availability_census(
    root: Path,
    records: Sequence[Mapping[str, Any]],
    fold_by_date: Mapping[str, int],
) -> dict[str, Any]:
    """Census outcome-blind HRRR/GEFS availability for later dates.

    Only timing, completeness, row-count, and source-integrity metadata are
    accepted.  Any label, outcome, score, probability, market price, or profit
    key fails before a record is interpreted.
    """
    validate_frozen_leader(root)
    _reject_protected_keys(records)
    _reject_protected_keys(fold_by_date)
    start, end, minimum_total, minimum_per_fold = _confirmation_bounds(root)
    by_date: dict[str, dict[str, dict[str, Any]]] = {}
    integrity_failures: list[str] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ProbabilityIntegrityError(f"census record {index} must be an object")
        keys = set(raw)
        if not _CENSUS_REQUIRED <= keys or keys - _CENSUS_REQUIRED - _CENSUS_OPTIONAL:
            raise ProbabilityIntegrityError(f"census record {index} fields differ")
        try:
            climate = date.fromisoformat(str(raw["climate_date"]))
        except ValueError as exc:
            raise ProbabilityIntegrityError(f"census record {index} has invalid date") from exc
        if not start <= climate <= end:
            raise ProbabilityIntegrityError(f"census record {index} outside confirmation pool")
        day = climate.isoformat()
        model = str(raw["model"]).casefold()
        if model not in {"hrrr", "gefs"}:
            raise ProbabilityIntegrityError(f"census record {index} has invalid model")
        if not str(raw["event_ticker"]).startswith("KXHIGHLAX-"):
            raise ProbabilityIntegrityError(f"census record {index} is not KXHIGHLAX")
        decision = _parse_utc(raw["decision_at_utc"], "decision_at_utc")
        nominal = _parse_utc(raw["nominal_cycle_at_utc"], "nominal_cycle_at_utc")
        effective = _parse_utc(raw["effective_available_at_utc"], "effective_available_at_utc")
        archived_raw = raw.get("archived_last_modified_at_utc")
        archived = _parse_utc(archived_raw, "archived_last_modified_at_utc") if archived_raw else None
        if decision.date() != climate or (decision.hour, decision.minute, decision.second) != (18, 0, 0):
            raise ProbabilityIntegrityError(f"census record {index} decision time differs")
        rows = raw["forecast_row_count"]
        if type(rows) is not int or rows <= 0:
            raise ProbabilityIntegrityError(f"census record {index} row count invalid")
        source_hash = raw["source_sha256"]
        if not isinstance(source_hash, str) or not _SHA.fullmatch(source_hash):
            raise ProbabilityIntegrityError(f"census record {index} source hash invalid")
        if model in by_date.setdefault(day, {}):
            raise ProbabilityIntegrityError(f"duplicate {model} record for {day}")
        usable = (
            raw["as_of_validated"] is True and raw["complete"] is True
            and nominal <= effective <= decision
            and (archived is None or archived <= effective)
        )
        if not usable:
            integrity_failures.append(f"unusable_{model}_{day}")
        by_date[day][model] = {
            "usable": usable,
            "nominal": nominal,
            "effective": effective,
            "archived": archived,
            "source_sha256": source_hash,
        }

    eligible: list[str] = []
    by_fold = {str(fold): 0 for fold in range(1, 6)}
    sensitivity_dates = {"8": [], "12": []}
    for day in sorted(by_date):
        models = by_date[day]
        if set(models) != {"hrrr", "gefs"} or not all(item["usable"] for item in models.values()):
            continue
        fold = fold_by_date.get(day)
        if type(fold) is not int or fold not in range(1, 6):
            raise ProbabilityIntegrityError(f"missing or invalid fold for eligible date {day}")
        eligible.append(day)
        by_fold[str(fold)] += 1
        for hours in (8, 12):
            qualified = True
            for item in models.values():
                delayed = item["nominal"] + timedelta(hours=hours)
                if item["archived"] is not None:
                    delayed = max(delayed, item["archived"])
                decision = datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC) + timedelta(hours=18)
                qualified = qualified and delayed <= decision
            if qualified:
                sensitivity_dates[str(hours)].append(day)

    coverage_ready = len(eligible) >= minimum_total and min(by_fold.values()) >= minimum_per_fold
    body = {
        "version": VERSION,
        "record_type": "confirmation_feature_availability_census",
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "submitted_record_count": len(records),
        "dates_with_any_record": len(by_date),
        "eligible_scored_event_count": len(eligible),
        "eligible_scored_event_dates_sha256": _canonical_hash(eligible),
        "eligible_scored_event_count_by_fold": by_fold,
        "availability_sensitivity_eligible_counts": {
            hours: len(days) for hours, days in sensitivity_dates.items()
        },
        "availability_sensitivity_date_hashes": {
            hours: _canonical_hash(days) for hours, days in sensitivity_dates.items()
        },
        "minimum_scored_probability_events": minimum_total,
        "minimum_scored_probability_events_per_fold": minimum_per_fold,
        "coverage_ready": coverage_ready,
        "unusable_record_count": len(integrity_failures),
        "unusable_record_ids_sha256": _canonical_hash(sorted(integrity_failures)),
        "contains_labels_or_outcomes": False,
        "contains_predictions_or_scores": False,
        **_safety(),
    }
    return _hash_bound(body, "census_sha256")


def _validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ProbabilityIntegrityError(f"{field} must be lowercase SHA-256")
    return value


def build_transfer_identity(
    root: Path,
    census: Mapping[str, Any],
    confirmation_dataset_binding: Mapping[str, Any],
    confirmation_fold_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one deterministic transfer identity without fitting anything."""
    leader = validate_frozen_leader(root)
    schema = probability_gate_schema(root)
    census_body = {key: value for key, value in census.items() if key != "census_sha256"}
    if census.get("census_sha256") != _canonical_hash(census_body):
        raise ProbabilityIntegrityError("availability census hash mismatch")
    if (
        census.get("version") != VERSION
        or census.get("record_type") != "confirmation_feature_availability_census"
        or census.get("contains_labels_or_outcomes") is not False
        or census.get("contains_predictions_or_scores") is not False
        or census.get("protected_confirmation_labels_read") is not False
        or census.get("network_used") is not False
        or census.get("refit_performed") is not False
        or census.get("recalibration_performed") is not False
        or census.get("actual_orders_placed") is not False
    ):
        raise ProbabilityIntegrityError("availability census crossed label barrier")
    allowed_dataset = {
        "dataset_id", "manifest_sha256", "date_start", "date_end",
        "outcome_blind", "contains_labels_or_outcomes", "experiment_network_used",
    }
    if set(confirmation_dataset_binding) != allowed_dataset:
        raise ProbabilityIntegrityError("confirmation dataset binding fields differ")
    if (
        confirmation_dataset_binding.get("outcome_blind") is not True
        or confirmation_dataset_binding.get("contains_labels_or_outcomes") is not False
        or confirmation_dataset_binding.get("experiment_network_used") is not False
    ):
        raise ProbabilityIntegrityError("confirmation dataset is not outcome-blind offline data")
    _validate_sha(confirmation_dataset_binding.get("dataset_id"), "dataset_id")
    _validate_sha(confirmation_dataset_binding.get("manifest_sha256"), "manifest_sha256")
    if (
        confirmation_dataset_binding.get("date_start") != census.get("date_start")
        or confirmation_dataset_binding.get("date_end") != census.get("date_end")
    ):
        raise ProbabilityIntegrityError("confirmation dataset dates differ from census")
    allowed_fold = {
        "split_sha256", "chronological_fold_count", "fold_date_counts", "outcome_blind",
    }
    if set(confirmation_fold_binding) != allowed_fold:
        raise ProbabilityIntegrityError("confirmation fold binding fields differ")
    counts = confirmation_fold_binding.get("fold_date_counts")
    if (
        confirmation_fold_binding.get("outcome_blind") is not True
        or confirmation_fold_binding.get("chronological_fold_count") != 5
        or not isinstance(counts, list) or len(counts) != 5
        or any(type(item) is not int or item < 0 for item in counts)
    ):
        raise ProbabilityIntegrityError("confirmation fold binding is invalid")
    _validate_sha(confirmation_fold_binding.get("split_sha256"), "split_sha256")
    expected_counts = [
        census.get("eligible_scored_event_count_by_fold", {}).get(str(fold))
        for fold in range(1, 6)
    ]
    if counts != expected_counts or sum(counts) != census.get("eligible_scored_event_count"):
        raise ProbabilityIntegrityError("confirmation fold counts differ from census")
    body = {
        "version": VERSION,
        "record_type": "frozen_probability_transfer_identity",
        "candidate_id": EXPECTED_LEADER["candidate_id"],
        "leader_validation_sha256": leader["validation_sha256"],
        "gate_schema_sha256": schema["schema_sha256"],
        "implementation_sha256": _file_hash(Path(__file__).resolve()),
        "availability_census_sha256": census["census_sha256"],
        "confirmation_dataset_binding": dict(confirmation_dataset_binding),
        "confirmation_fold_binding": dict(confirmation_fold_binding),
        "research_plan_sha256": EXPECTED_LEADER["research_plan_sha256"],
        "fitted_model_sha256": EXPECTED_LEADER["fitted_model_sha256"],
        "model_state_sha256": EXPECTED_LEADER["model_state_sha256"],
        "promotable_candidate_identity_limit": 1,
        **_safety(),
    }
    return _hash_bound(body, "transfer_identity_sha256")


def prelabel_readiness_verdict(
    root: Path,
    census: Mapping[str, Any] | None = None,
    confirmation_dataset_binding: Mapping[str, Any] | None = None,
    confirmation_fold_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed until all probability-colony outcome-blind evidence exists."""
    try:
        leader = validate_frozen_leader(root)
        schema = probability_gate_schema(root)
        blockers: list[str] = []
        transfer: dict[str, Any] | None = None
        if census is None:
            blockers.append("confirmation_availability_census_missing")
        if confirmation_dataset_binding is None:
            blockers.append("confirmation_dataset_binding_missing")
        if confirmation_fold_binding is None:
            blockers.append("confirmation_fold_binding_missing")
        if not blockers:
            transfer = build_transfer_identity(
                root, census or {}, confirmation_dataset_binding or {},
                confirmation_fold_binding or {},
            )
            if census.get("coverage_ready") is not True:
                blockers.append("minimum_probability_sample_not_met")
        ready = not blockers
        body = {
            "version": VERSION,
            "record_type": "frozen_probability_prelabel_readiness",
            "status": READY if ready else INSUFFICIENT,
            "probability_colony_readiness_passed": ready,
            "ready_for_protected_label_read": False,
            "reason_ready_for_label_read_is_false": (
                "all_four_colonies_and_cross_review_must_pass_the_global_barrier"
            ),
            "blockers": blockers,
            "leader_validation_sha256": leader["validation_sha256"],
            "gate_schema_sha256": schema["schema_sha256"],
            "transfer_identity_sha256": (
                transfer["transfer_identity_sha256"] if transfer else None
            ),
            **_safety(),
        }
    except ProbabilityIntegrityError as exc:
        body = {
            "version": VERSION,
            "record_type": "frozen_probability_prelabel_readiness",
            "status": INTEGRITY,
            "probability_colony_readiness_passed": False,
            "ready_for_protected_label_read": False,
            "blockers": [str(exc)],
            **_safety(),
        }
    return _hash_bound(body, "verdict_sha256")


def audit_frozen_probability(
    root: Path, config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Controller-facing audit of the frozen probability colony.

    A supplied config must be byte-for-byte equivalent in canonical form to
    the registered on-disk config.  Optional readiness inputs are loaded only
    from three fixed outcome-blind manifest names; caller-selected paths are
    never followed.
    """
    root = Path(root).resolve()
    disk_config = _load_object(root / CONFIG_PATH)
    if config is not None and _canonical_hash(config) != _canonical_hash(disk_config):
        body = {
            "version": VERSION,
            "colony": "frozen_probability_validation",
            "status": INTEGRITY,
            "promotion_ready": False,
            "failure_reasons": ["supplied_config_differs_from_frozen_registration"],
            **_safety(),
        }
        return _hash_bound(body, "result_sha256")

    names = {
        "census": "v5_probability_confirmation_availability_census.json",
        "dataset": "v5_probability_confirmation_dataset_binding.json",
        "fold": "v5_probability_confirmation_fold_binding.json",
    }
    loaded: dict[str, dict[str, Any] | None] = {}
    for key, name in names.items():
        path = root / "data" / "manifests" / name
        loaded[key] = _load_object(path) if path.is_file() else None
    readiness = prelabel_readiness_verdict(
        root, loaded["census"], loaded["dataset"], loaded["fold"],
    )
    body = {
        "version": VERSION,
        "colony": "frozen_probability_validation",
        "status": readiness["status"],
        "promotion_ready": readiness["probability_colony_readiness_passed"],
        "failure_reasons": list(readiness["blockers"]),
        "prelabel_readiness_sha256": readiness["verdict_sha256"],
        **_safety(),
    }
    return _hash_bound(body, "result_sha256")


def cross_review_probability(value: Mapping[str, Any]) -> dict[str, Any]:
    """Independently check the controller-facing result's boundary and hash."""
    body = {key: item for key, item in value.items() if key != "result_sha256"}
    passed = (
        value.get("version") == VERSION
        and value.get("colony") == "frozen_probability_validation"
        and value.get("result_sha256") == _canonical_hash(body)
        and value.get("protected_confirmation_labels_read") is False
        and value.get("network_used") is False
        and value.get("refit_performed") is False
        and value.get("recalibration_performed") is False
        and value.get("actual_orders_placed") is False
        and value.get("status") in {READY, INSUFFICIENT, INTEGRITY}
        and value.get("promotion_ready") is (value.get("status") == READY)
    )
    review = {
        "review_version": "klax-v5-frozen-probability-cross-review-v1",
        "target_colony": "frozen_probability_validation",
        "status": "PASS" if passed else "FAIL",
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    return _hash_bound(review, "review_sha256")


_METRIC_FIELDS = {
    "scored_probability_events", "scored_probability_events_by_fold",
    "selected_subset_count", "aggregate_crps_candidate", "aggregate_crps_reference",
    "paired_crps_improvement_bootstrap_lower_95", "aggregate_brier_candidate",
    "aggregate_brier_reference", "crps_improving_fold_count",
    "brier_no_worse_fold_count", "final_two_folds_crps_no_worse",
    "final_two_folds_brier_no_worse", "overall_reliability_error",
    "selected_subset_reliability_error", "probability_mass_max_abs_error",
    "minimum_realized_bracket_probability", "availability_8h_no_worse",
    "availability_12h_no_worse", "independent_replication_passed",
    "critic_nonrejection",
}


def evaluate_authorized_probability_metrics(
    root: Path,
    metrics: Mapping[str, Any] | None,
    *,
    confirmation_authorized: bool,
) -> dict[str, Any]:
    """Apply registered gates to a controller-supplied aggregate metric record.

    This function never opens a label file.  Supplying metrics without an
    explicit controller authorization is itself an integrity failure.
    """
    schema = probability_gate_schema(root)
    if metrics is not None and not confirmation_authorized:
        status, failures = INTEGRITY, ["confirmation_metrics_without_authorization"]
    elif metrics is None:
        status, failures = INSUFFICIENT, ["authorized_probability_metrics_missing"]
    elif set(metrics) != _METRIC_FIELDS:
        status, failures = INTEGRITY, ["probability_metric_schema_differs"]
    else:
        try:
            numeric_fields = _METRIC_FIELDS - {
                "scored_probability_events_by_fold", "final_two_folds_crps_no_worse",
                "final_two_folds_brier_no_worse", "availability_8h_no_worse",
                "availability_12h_no_worse", "independent_replication_passed",
                "critic_nonrejection",
            }
            for field in numeric_fields:
                value = metrics[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ProbabilityIntegrityError(f"non-finite or invalid metric: {field}")
            count_fields = {
                "scored_probability_events", "selected_subset_count",
                "crps_improving_fold_count", "brier_no_worse_fold_count",
            }
            if any(
                type(metrics[field]) is not int or metrics[field] < 0
                for field in count_fields
            ):
                raise ProbabilityIntegrityError("probability count metric invalid")
            if any(
                metrics[field] > 5
                for field in {"crps_improving_fold_count", "brier_no_worse_fold_count"}
            ):
                raise ProbabilityIntegrityError("probability fold count exceeds five")
            nonnegative_fields = {
                "aggregate_crps_candidate", "aggregate_crps_reference",
                "aggregate_brier_candidate", "aggregate_brier_reference",
                "overall_reliability_error", "selected_subset_reliability_error",
                "probability_mass_max_abs_error", "minimum_realized_bracket_probability",
            }
            if any(metrics[field] < 0 for field in nonnegative_fields):
                raise ProbabilityIntegrityError("negative probability metric invalid")
            unit_interval_fields = {
                "overall_reliability_error", "selected_subset_reliability_error",
                "minimum_realized_bracket_probability",
            }
            if any(metrics[field] > 1 for field in unit_interval_fields):
                raise ProbabilityIntegrityError("probability metric exceeds one")
            folds = metrics["scored_probability_events_by_fold"]
            if not isinstance(folds, list) or len(folds) != 5 or any(type(item) is not int or item < 0 for item in folds):
                raise ProbabilityIntegrityError("scored_probability_events_by_fold invalid")
            if sum(folds) != metrics["scored_probability_events"]:
                raise ProbabilityIntegrityError("probability fold counts do not sum to total")
            booleans = {
                "final_two_folds_crps_no_worse", "final_two_folds_brier_no_worse",
                "availability_8h_no_worse", "availability_12h_no_worse",
                "independent_replication_passed", "critic_nonrejection",
            }
            if any(type(metrics[field]) is not bool for field in booleans):
                raise ProbabilityIntegrityError("boolean probability metric invalid")
            gates = schema["postlabel_probability_gates"]
            prelabel = schema["prelabel_sample_gates"]
            sample_failures: list[str] = []
            if metrics["scored_probability_events"] < prelabel["minimum_scored_probability_events"]:
                sample_failures.append("minimum_scored_probability_events_not_met")
            if min(folds) < prelabel["minimum_scored_probability_events_per_fold"]:
                sample_failures.append("minimum_scored_probability_events_per_fold_not_met")
            if metrics["selected_subset_count"] < 30:
                sample_failures.append("selected_subset_too_small_for_registered_reliability_gate")
            if sample_failures:
                status, failures = INSUFFICIENT, sample_failures
            else:
                checks = {
                    "aggregate_crps_not_improved": metrics["aggregate_crps_candidate"] < metrics["aggregate_crps_reference"],
                    "paired_crps_bootstrap_lower_not_positive": metrics["paired_crps_improvement_bootstrap_lower_95"] > 0,
                    "aggregate_brier_worse": metrics["aggregate_brier_candidate"] <= metrics["aggregate_brier_reference"],
                    "too_few_crps_improving_folds": metrics["crps_improving_fold_count"] >= gates["crps_improving_folds_minimum"],
                    "too_few_brier_no_worse_folds": metrics["brier_no_worse_fold_count"] >= gates["brier_no_worse_folds_minimum"],
                    "final_two_fold_crps_worse": metrics["final_two_folds_crps_no_worse"] is True,
                    "final_two_fold_brier_worse": metrics["final_two_folds_brier_no_worse"] is True,
                    "overall_reliability_too_high": metrics["overall_reliability_error"] <= gates["overall_reliability_error_maximum"],
                    "selected_reliability_too_high": metrics["selected_subset_reliability_error"] <= gates["selected_subset_reliability_error_maximum"],
                    "probability_mass_not_conserved": metrics["probability_mass_max_abs_error"] <= gates["probability_sum_tolerance"],
                    "realized_probability_below_log_clip": metrics["minimum_realized_bracket_probability"] >= gates["log_loss_probability_clip"],
                    "availability_8h_failed": metrics["availability_8h_no_worse"] is True,
                    "availability_12h_failed": metrics["availability_12h_no_worse"] is True,
                    "independent_replication_failed": metrics["independent_replication_passed"] is True,
                    "critic_rejected": metrics["critic_nonrejection"] is True,
                }
                failures = sorted(name for name, passed in checks.items() if not passed)
                status = PASS if not failures else REJECTED
        except ProbabilityIntegrityError as exc:
            status, failures = INTEGRITY, [str(exc)]
    body = {
        "version": VERSION,
        "record_type": "authorized_probability_gate_verdict",
        "status": status,
        "gate_failures": failures,
        "metrics_sha256": _canonical_hash(metrics) if metrics is not None else None,
        "confirmation_authorized": confirmation_authorized,
        "gate_schema_sha256": schema["schema_sha256"],
        **_safety(),
    }
    return _hash_bound(body, "verdict_sha256")


def _immutable_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise ProbabilityIntegrityError(f"immutable artifact differs: {path}")
        return
    with NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=path.name + ".", suffix=".pending", delete=False,
    ) as handle:
        handle.write(payload)
        pending = Path(handle.name)
    pending.replace(path)


def write_probability_artifacts(root: Path) -> list[Path]:
    """Write only outcome-blind, probability-colony registration artifacts."""
    root = Path(root).resolve()
    artifacts = {
        "v5_probability_frozen_leader_validation.json": validate_frozen_leader(root),
        "v5_probability_gate_schema.json": probability_gate_schema(root),
        "v5_probability_colony_status.json": prelabel_readiness_verdict(root),
    }
    paths: list[Path] = []
    for name, value in artifacts.items():
        path = root / "data" / "manifests" / name
        _immutable_write(path, value)
        paths.append(path)
    return paths


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--write-artifacts", action="store_true")
    args = parser.parse_args()
    output = (
        [str(path) for path in write_probability_artifacts(args.project_root)]
        if args.write_artifacts else prelabel_readiness_verdict(args.project_root)
    )
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
