from kalshi_weather.rules_engine_ext.calibration_metrics import ForecastOutcome, brier_score, log_loss, bucket_reliability


def test_brier_score():
    rows = [ForecastOutcome(0.8, 1), ForecastOutcome(0.2, 0)]
    assert abs(brier_score(rows) - 0.04) < 1e-9


def test_log_loss_positive():
    rows = [ForecastOutcome(0.8, 1), ForecastOutcome(0.2, 0)]
    assert log_loss(rows) > 0


def test_bucket_reliability():
    rows = [ForecastOutcome(0.65, 1), ForecastOutcome(0.68, 0), ForecastOutcome(0.15, 0)]
    buckets = bucket_reliability(rows)
    assert any(b["count"] == 2 for b in buckets)
