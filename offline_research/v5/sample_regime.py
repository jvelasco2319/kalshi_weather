"""Outcome-blind sample and regime readiness for V5."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .common import (
    V5IntegrityError, canonical_hash, file_record, load_object, safety_record,
)


COLONY = "sample_and_regime_robustness"
VERSION = "klax-v5-sample-regime-readiness-v1"


def _calendar_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def audit_sample_regime(
    root: Path, config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit only outcome-blind sample prerequisites.

    The function deliberately does not enumerate, open, or score protected
    confirmation labels. A selected-trade count is accepted only from a future
    hash-bound execution manifest that explicitly records no label read.
    """
    root = Path(root).resolve()
    cfg = dict(config or load_object(
        root / "configs/v5_four_colony_verification_campaign.json"))
    partitions = cfg.get("partitions")
    barrier = cfg.get("protected_label_barrier")
    if not isinstance(partitions, dict) or not isinstance(barrier, dict):
        raise V5IntegrityError("V5 sample partition or barrier is missing")
    pool = partitions.get("combined_confirmation_pool")
    if not isinstance(pool, dict):
        raise V5IntegrityError("V5 confirmation pool is missing")
    calendar_days = _calendar_days(pool["date_start"], pool["date_end"])
    if calendar_days != pool.get("maximum_calendar_days"):
        raise V5IntegrityError("V5 confirmation calendar span differs")

    source_records = [
        file_record(root, "configs/v5_four_colony_verification_campaign.json"),
        file_record(root, "data/manifests/v4_dataset_1330_1500_1800.json"),
        file_record(root, "data/manifests/v4_five_fold_split_1330_1500_1800.json"),
    ]
    selected_manifest_path = (
        root / "data/manifests/v5_outcome_blind_selected_trades.json")
    selected_count = 0
    distinct_days = 0
    per_fold = [0, 0, 0, 0, 0]
    selected_manifest_status = "ABSENT"
    if selected_manifest_path.is_file():
        selected = load_object(selected_manifest_path)
        if (
            selected.get("protected_confirmation_labels_read") is not False
            or selected.get("outcome_blind") is not True
            or selected.get("manifest_sha256") != canonical_hash({
                key: value for key, value in selected.items()
                if key != "manifest_sha256"
            })
        ):
            raise V5IntegrityError(
                "selected-trade manifest is not outcome-blind and hash-bound")
        selected_count = int(selected.get("selected_trade_count", 0))
        distinct_days = int(selected.get("distinct_settlement_days", 0))
        per_fold = list(selected.get("selected_trade_count_by_fold", []))
        if len(per_fold) != 5 or any(type(item) is not int or item < 0 for item in per_fold):
            raise V5IntegrityError("selected-trade fold counts are invalid")
        selected_manifest_status = "VERIFIED"

    minimum_trades = int(barrier["minimum_selected_trades"])
    minimum_days = int(barrier["minimum_distinct_settlement_days"])
    minimum_per_fold = int(barrier["minimum_selected_trades_per_fold"])
    sample_ready = (
        selected_count >= minimum_trades
        and distinct_days >= minimum_days
        and min(per_fold) >= minimum_per_fold
    )
    verdict = (
        "SAMPLE_AND_REGIME_EVIDENCE_READY"
        if sample_ready else "INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ"
    )
    result: dict[str, Any] = {
        "version": VERSION,
        "colony": COLONY,
        "status": verdict,
        "promotion_ready": sample_ready,
        "confirmation_pool": {
            "date_start": pool["date_start"],
            "date_end": pool["date_end"],
            "maximum_calendar_days": calendar_days,
            "chronological_fold_count": pool["chronological_fold_count"],
        },
        "prelabel_counts": {
            "selected_trade_manifest_status": selected_manifest_status,
            "selected_trade_count": selected_count,
            "distinct_settlement_days": distinct_days,
            "selected_trade_count_by_fold": per_fold,
            "minimum_selected_trades": minimum_trades,
            "minimum_distinct_settlement_days": minimum_days,
            "minimum_selected_trades_per_fold": minimum_per_fold,
        },
        "registered_postlabel_tests": list(
            cfg["colonies"][COLONY]["registered_breakdowns"]),
        "source_records": source_records,
        "failure_reasons": [] if sample_ready else [
            "outcome_blind_selected_trade_manifest_not_yet_sufficient"
        ],
        **safety_record(),
    }
    result["result_sha256"] = canonical_hash(result)
    return result


def cross_review_sample_regime(value: Mapping[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "result_sha256"}
    passed = (
        value.get("version") == VERSION
        and value.get("result_sha256") == canonical_hash(body)
        and value.get("protected_confirmation_labels_read") is False
        and value.get("actual_orders_placed") is False
        and value.get("status") in {
            "SAMPLE_AND_REGIME_EVIDENCE_READY",
            "INSUFFICIENT_SAMPLE_BEFORE_LABEL_READ",
        }
    )
    return {
        "review_version": "klax-v5-sample-regime-cross-review-v1",
        "target_colony": COLONY,
        "status": "PASS" if passed else "FAIL",
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
