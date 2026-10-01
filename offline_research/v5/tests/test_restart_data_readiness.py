from pathlib import Path

from v5.restart_data_readiness import build


ROOT = Path(__file__).resolve().parents[2]


def test_restart_stays_blocked_without_full_window_depth_archive() -> None:
    result = build(ROOT)
    assert result["status"] == "BLOCKED_EXTERNAL_EXECUTION_ARCHIVE"
    assert result["restart_authorized"] is False
    assert result["all_missing_data_possible_to_obtain_from_verified_sources"] is False
    assert result["data_classes"]["historical_execution"]["best_documented_coverage_upper_bound"] < 0.9
    assert result["protected_confirmation_labels_read"] is False
    assert result["actual_orders_placed"] is False


def test_feasible_public_data_is_measured_but_not_misclassified() -> None:
    result = build(ROOT)
    probability = result["data_classes"]["weather_and_probability"]
    assert probability["obtainable"] is True
    assert probability["weather_planned_requests"] == 25_620
    assert 15.0 < probability["weather_estimated_gib"] < 15.2
    assert result["download_decision"]["bulk_weather_and_diagnostic_market_download_started"] is False
