from pathlib import Path

from v5.probability_acquisition import (
    audit_current_outcome_blind_coverage,
    build_probability_acquisition_plan,
)


ROOT = Path(__file__).resolve().parents[2]


def test_outcome_blind_local_coverage_audit_reports_later_gaps():
    audit = audit_current_outcome_blind_coverage(ROOT)
    assert audit["weather"] == {
        "hrrr_gefs_complete_days": 0, "missing_days": 427,
    }
    assert audit["kalshi_safe_metadata"]["covered_days"] == 184
    assert audit["kalshi_safe_metadata"]["safe_contract_count"] == 1104
    assert audit["kalshi_one_minute_candles"]["covered_days"] == 0
    assert audit["kalshi_public_trades"]["covered_days"] == 0
    assert audit["clilax_archive_envelope"]["covered_days"] == 191
    assert audit["protected_confirmation_labels_read"] is False
    assert audit["raw_kalshi_responses_read"] is False
    assert audit["clilax_product_text_read"] is False


def test_acquisition_plan_is_finite_and_outcome_blind():
    plan = build_probability_acquisition_plan(ROOT)
    assert plan["status"] == "PLANNED_NO_DATA_ACQUIRED"
    assert plan["confirmation_window"]["calendar_days"] == 427
    assert [fold["calendar_days"] for fold in plan["confirmation_window"]["chronological_folds"]] == [86, 86, 85, 85, 85]
    assert plan["weather_plan"]["planned_requests"] == 25_620
    assert plan["weather_plan"]["request_starts_per_second"] == 2.0
    assert 3.55 < plan["weather_plan"]["serial_request_start_floor_hours"] < 3.57
    assert 15 * 1024**3 < plan["weather_plan"]["estimated_raw_bytes_from_v3_observed_average"] < 16 * 1024**3
    assert plan["network_used"] is False
    assert plan["protected_confirmation_labels_read"] is False
    assert plan["labels_remain_sealed"] is True
    assert plan["actual_orders_placed"] is False

