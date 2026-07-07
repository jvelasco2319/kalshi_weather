from datetime import datetime, timezone

from kalshi_weather.edge_engine.data_freshness import FreshnessConfig, assess_freshness


def test_freshness_flags_stale_inputs():
    now = datetime(2026, 6, 26, 17, 0, tzinfo=timezone.utc)
    report = assess_freshness(
        now=now,
        market_ts="2026-06-26T16:59:30Z",
        model_ts="2026-06-26T16:00:00Z",
        observation_ts="2026-06-26T16:30:00Z",
        config=FreshnessConfig(max_market_age_seconds=90, max_model_age_seconds=1800, max_observation_age_seconds=600),
    )
    assert not report.market_stale
    assert report.model_stale
    assert report.observation_stale


def test_freshness_metadata_keys():
    now = datetime(2026, 6, 26, 17, 0, tzinfo=timezone.utc)
    report = assess_freshness(now=now, market_ts=None, model_ts=None, observation_ts=None)
    md = report.as_candidate_metadata()
    assert md["market_stale"] is True
    assert md["model_stale"] is True
    assert md["observation_stale"] is True
