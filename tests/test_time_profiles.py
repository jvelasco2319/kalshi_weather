from datetime import datetime
from kalshi_weather.rules_engine_ext.time_profiles import ProfileInputs, select_profile


def test_auto_profile_overnight_uses_conservative_limits():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 29, 20, 0),
        target_date_local=datetime(2026, 6, 30, 0, 0),
    )
    decision = select_profile(inputs)
    cfg = decision.effective_risk_config
    assert decision.active_profile == "overnight_next_day"
    assert cfg.min_edge_cents >= 12
    assert cfg.max_total_open_risk_groups <= 2
    assert cfg.max_risk_dollars_per_trade <= 25


def test_stale_observation_disables_elimination():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 10, 0),
        target_date_local=datetime(2026, 6, 30, 0, 0),
        observation_available=False,
        observation_stale=True,
    )
    decision = select_profile(inputs)
    assert decision.effective_risk_config.allow_observation_elimination is False
    assert decision.effective_allow_observation_elimination is False
    assert decision.dynamic_override_reason == "stale_or_missing_observation"


def test_active_nowcast_before_1330():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 13, 29),
        target_date_local=datetime(2026, 6, 30, 0, 0),
        observation_available=True,
        observation_stale=False,
        latest_observation_time_utc="2026-06-30T20:25:00+00:00",
        observed_high_so_far_f=70.0,
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "active_nowcast"


def test_late_day_risk_manage_after_1330():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 13, 30),
        target_date_local=datetime(2026, 6, 30, 0, 0),
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "late_day_risk_manage"
    assert decision.effective_risk_config.allow_new_entries == "limited"


def test_close_only_after_1630():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 16, 30),
        target_date_local=datetime(2026, 6, 30, 0, 0),
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "close_only"
    assert decision.effective_risk_config.allow_new_entries is False


def test_post_close_after_1800():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 18, 1),
        target_date_local=datetime(2026, 6, 30, 0, 0),
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "post_close"
    assert decision.effective_risk_config.stop_new_trading is True


def test_fresh_station_matched_observation_allows_elimination():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 10, 0),
        target_date_local=datetime(2026, 6, 30, 0, 0),
        observation_available=True,
        observation_stale=False,
        observation_station_matches_settlement=True,
        latest_observation_time_utc="2026-06-30T17:00:00+00:00",
        observed_high_so_far_f=70.0,
    )
    decision = select_profile(inputs)
    assert decision.effective_allow_observation_elimination is True
    assert decision.effective_risk_config.effective_allow_observation_elimination is True


def test_drawdown_switches_to_close_only():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 10, 0),
        target_date_local=datetime(2026, 6, 30, 0, 0),
        open_pnl_dollars=-45,
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "close_only"
    assert not decision.effective_risk_config.allow_new_entries


def test_max_risk_groups_switches_risk_reduce():
    inputs = ProfileInputs(
        now_local=datetime(2026, 6, 30, 10, 0),
        target_date_local=datetime(2026, 6, 30, 0, 0),
        max_risk_groups_reached=True,
    )
    decision = select_profile(inputs)
    assert decision.active_profile == "risk_reduce"
    assert not decision.effective_risk_config.allow_new_entries
