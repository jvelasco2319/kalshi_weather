from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from v5a.paid_depth import (
    PaidDepthError,
    build_paid_depth_readiness,
    build_paid_execution_manifest,
    classify_primary_row,
)


ROOT = Path(__file__).resolve().parents[2]
RULES = {
    "arrival_delay_seconds": 5,
    "grade_a_max_quote_age_ms": 5000,
    "grade_b_plus_max_quote_age_ms": 60000,
}


def row(**changes):
    value = {
        "climate_date": "2026-08-31",
        "market_platform_id": "KXHIGHLAX-26AUG31-B80.5",
        "outcome_name": "Yes",
        "target_offset_s": 5,
        "target_ts": "2026-08-31 18:00:05.000000000",
        "before_timestamp": "2026-08-31 18:00:02.000000000",
        "before_age_ms": 3000.0,
        "before_state": "VERIFIED",
        "before_continuity": "CONTIGUOUS",
        "before_best_bid": 0.40,
        "before_best_bid_size": 10.0,
        "before_best_ask": 0.42,
        "before_best_ask_size": 4.0,
    }
    value.update(changes)
    return value


def test_grade_a_requires_fresh_verified_contiguous_book_with_size():
    result = classify_primary_row(row(), RULES)
    assert result["evidence_grade"] == "A"
    assert result["promotion_eligible_execution"] is True
    assert result["sequence_gap"] is False


def test_unknown_continuity_is_grade_b_plus_and_never_promotable():
    result = classify_primary_row(row(before_continuity="UNKNOWN"), RULES)
    assert result["evidence_grade"] == "B_PLUS"
    assert result["promotion_eligible_execution"] is False
    assert result["sequence_gap"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"before_age_ms": 60001.0},
        {"before_state": "INTERMEDIATE"},
        {"before_best_ask_size": 0.5},
        {"before_best_ask": None},
        {"before_continuity": "RESET"},
    ],
)
def test_unusable_rows_fail_closed(changes):
    result = classify_primary_row(row(**changes), RULES)
    assert result["evidence_grade"] == "UNAVAILABLE"
    assert result["promotion_eligible_execution"] is False


def test_workspace_manifest_is_outcome_blind_and_hash_bound():
    manifest = build_paid_execution_manifest(ROOT)
    assert manifest["outcomes_read"] is False
    assert manifest["protected_confirmation_labels_read"] is False
    assert manifest["network_used"] is False
    assert manifest["actual_orders_placed"] is False
    assert manifest["dates_with_any_grade_a_contract_side"] == 19
    assert manifest["dates_with_usable_full_book"] == 80
    assert manifest["probability_scoring_date_count"] == 92
    assert len(manifest["daily_evidence"]) == 92
    assert len(manifest["records"]) == 976
    forbidden = {"settlement_value", "winning_contract", "reported_high_f", "result"}
    assert not forbidden.intersection(str(manifest).casefold())


def test_original_v5_restart_is_denied_by_hard_bounds_with_exact_rules_bound():
    execution = build_paid_execution_manifest(ROOT)
    readiness = build_paid_depth_readiness(ROOT, execution)
    assert readiness["original_v5_restart_ready"] is False
    assert readiness["protected_label_access_authorized"] is False
    assert readiness["hard_bounds_before_selected_trade_freeze"]["maximum_possible_probability_events"] == 92
    assert readiness["hard_bounds_before_selected_trade_freeze"]["required_probability_events"] == 120
    assert readiness["economics"]["direct_taker_fee_schedule_exact_for_source_window"] is True
    assert readiness["economics"]["exact_historical_event_rule_revision_bound"] is True
    assert readiness["economics"]["event_rule_dates_bound"] == 92
    assert readiness["hard_bounds_before_selected_trade_freeze"]["dates_with_grade_a_b_plus_or_b_execution_price"] == 82
    assert readiness["hard_bounds_before_selected_trade_freeze"]["execution_abstention_date_count"] == 10


def test_tampering_is_rejected():
    execution = build_paid_execution_manifest(ROOT)
    changed = deepcopy(execution)
    changed["grade_counts"]["A"] += 1
    with pytest.raises(PaidDepthError, match="self_sha256 mismatch"):
        build_paid_depth_readiness(ROOT, changed)
