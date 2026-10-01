from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from v5.probability import (
    INSUFFICIENT,
    INTEGRITY,
    PASS,
    READY,
    REJECTED,
    ProbabilityIntegrityError,
    audit_frozen_probability,
    build_confirmation_availability_census,
    build_transfer_identity,
    cross_review_probability,
    evaluate_authorized_probability_metrics,
    prelabel_readiness_verdict,
    probability_gate_schema,
    validate_frozen_leader,
)


ROOT = Path(__file__).resolve().parents[2]


def _records(days: int = 120):
    records = []
    folds = {}
    first = date(2025, 7, 1)
    for offset in range(days):
        day = first + timedelta(days=offset)
        iso = day.isoformat()
        folds[iso] = offset // 24 + 1
        for model in ("hrrr", "gefs"):
            records.append({
                "climate_date": iso,
                "event_ticker": f"KXHIGHLAX-{day:%y%b%d}".upper(),
                "model": model,
                "decision_at_utc": iso + "T18:00:00Z",
                "nominal_cycle_at_utc": iso + "T06:00:00Z",
                "effective_available_at_utc": iso + "T07:00:00Z",
                "forecast_row_count": 24,
                "as_of_validated": True,
                "complete": True,
                "source_sha256": ("a" if model == "hrrr" else "b") * 64,
            })
    return records, folds


def _bindings():
    return (
        {
            "dataset_id": "c" * 64,
            "manifest_sha256": "d" * 64,
            "date_start": "2025-07-01",
            "date_end": "2026-08-31",
            "outcome_blind": True,
            "contains_labels_or_outcomes": False,
            "experiment_network_used": False,
        },
        {
            "split_sha256": "e" * 64,
            "chronological_fold_count": 5,
            "fold_date_counts": [24, 24, 24, 24, 24],
            "outcome_blind": True,
        },
    )


def _passing_metrics():
    return {
        "scored_probability_events": 120,
        "scored_probability_events_by_fold": [24, 24, 24, 24, 24],
        "selected_subset_count": 30,
        "aggregate_crps_candidate": 0.5,
        "aggregate_crps_reference": 0.6,
        "paired_crps_improvement_bootstrap_lower_95": 0.01,
        "aggregate_brier_candidate": 0.7,
        "aggregate_brier_reference": 0.8,
        "crps_improving_fold_count": 4,
        "brier_no_worse_fold_count": 4,
        "final_two_folds_crps_no_worse": True,
        "final_two_folds_brier_no_worse": True,
        "overall_reliability_error": 0.09,
        "selected_subset_reliability_error": 0.14,
        "probability_mass_max_abs_error": 1e-12,
        "minimum_realized_bracket_probability": 1e-5,
        "availability_8h_no_worse": True,
        "availability_12h_no_worse": True,
        "independent_replication_passed": True,
        "critic_nonrejection": True,
    }


def test_actual_frozen_leader_and_gate_schema_are_hash_bound():
    leader = validate_frozen_leader(ROOT)
    schema = probability_gate_schema(ROOT)
    assert leader["known_hash_validation_passed"] is True
    assert leader["candidate_id"] == "v4-candidate-a779fd17e7b160650c8f"
    assert schema["promotable_candidate_identity_limit"] == 1
    assert schema["adaptive_changes_permitted"] == {
        "refit": False,
        "recalibration": False,
        "feature_selection": False,
        "threshold_adjustment": False,
        "decision_time_change": False,
    }


def test_confirmation_census_meets_registered_sample_without_labels():
    records, folds = _records()
    census = build_confirmation_availability_census(ROOT, records, folds)
    assert census["eligible_scored_event_count"] == 120
    assert census["eligible_scored_event_count_by_fold"] == {
        "1": 24, "2": 24, "3": 24, "4": 24, "5": 24,
    }
    assert census["availability_sensitivity_eligible_counts"] == {
        "8": 120, "12": 120,
    }
    assert census["coverage_ready"] is True
    assert census["protected_confirmation_labels_read"] is False
    assert census["contains_labels_or_outcomes"] is False


def test_confirmation_census_rejects_any_outcome_field():
    records, folds = _records(1)
    records[0]["settlement_outcome"] = 1
    with pytest.raises(ProbabilityIntegrityError, match="field denied"):
        build_confirmation_availability_census(ROOT, records, folds)


def test_transfer_identity_is_deterministic_and_frozen():
    records, folds = _records()
    census = build_confirmation_availability_census(ROOT, records, folds)
    dataset, split = _bindings()
    first = build_transfer_identity(ROOT, census, dataset, split)
    second = build_transfer_identity(ROOT, census, dataset, split)
    assert first == second
    assert first["refit_performed"] is False
    assert first["recalibration_performed"] is False
    changed = dict(dataset)
    changed["dataset_id"] = "f" * 64
    third = build_transfer_identity(ROOT, census, changed, split)
    assert third["transfer_identity_sha256"] != first["transfer_identity_sha256"]


def test_prelabel_readiness_fails_closed_until_inputs_exist():
    missing = prelabel_readiness_verdict(ROOT)
    assert missing["status"] == INSUFFICIENT
    assert missing["ready_for_protected_label_read"] is False
    assert len(missing["blockers"]) == 3

    records, folds = _records()
    census = build_confirmation_availability_census(ROOT, records, folds)
    dataset, split = _bindings()
    ready = prelabel_readiness_verdict(ROOT, census, dataset, split)
    assert ready["status"] == READY
    assert ready["probability_colony_readiness_passed"] is True
    assert ready["ready_for_protected_label_read"] is False


def test_controller_audit_and_cross_review_are_fail_closed():
    result = audit_frozen_probability(ROOT)
    assert result["colony"] == "frozen_probability_validation"
    assert result["status"] == INSUFFICIENT
    assert result["promotion_ready"] is False
    assert cross_review_probability(result)["status"] == "PASS"
    altered = dict(result)
    altered["promotion_ready"] = True
    assert cross_review_probability(altered)["status"] == "FAIL"


def test_metrics_require_authorization_and_all_registered_gates():
    metrics = _passing_metrics()
    unauthorized = evaluate_authorized_probability_metrics(
        ROOT, metrics, confirmation_authorized=False,
    )
    assert unauthorized["status"] == INTEGRITY
    passed = evaluate_authorized_probability_metrics(
        ROOT, metrics, confirmation_authorized=True,
    )
    assert passed["status"] == PASS
    failed_metrics = dict(metrics)
    failed_metrics["aggregate_crps_candidate"] = 0.7
    failed = evaluate_authorized_probability_metrics(
        ROOT, failed_metrics, confirmation_authorized=True,
    )
    assert failed["status"] == REJECTED
    assert "aggregate_crps_not_improved" in failed["gate_failures"]


def test_metric_shortfall_is_insufficient_not_a_rejection():
    metrics = _passing_metrics()
    metrics["scored_probability_events"] = 119
    metrics["scored_probability_events_by_fold"] = [23, 24, 24, 24, 24]
    verdict = evaluate_authorized_probability_metrics(
        ROOT, metrics, confirmation_authorized=True,
    )
    assert verdict["status"] == INSUFFICIENT

