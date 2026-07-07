from kalshi_weather.model.calibration import calibration_buckets


def test_calibration_buckets_summarize_observed_rates() -> None:
    buckets = calibration_buckets([0.1, 0.2, 0.9], [0, 1, 1], bucket_count=2)

    assert buckets[0]["count"] == 2
    assert buckets[0]["observed_rate"] == 0.5
    assert buckets[1]["count"] == 1
    assert buckets[1]["observed_rate"] == 1.0
