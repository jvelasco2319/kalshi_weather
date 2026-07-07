from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from kalshi_weather.cli import (
    _blend_probabilities_for_context,
    _edge_cost_config,
    _edge_portfolio_state_from_context,
    _edge_risk_config,
    _market_cycle_paper_command,
    _model_refresh_cache_from_disk,
    _rule_decision_for_context,
    _trader_cached_model_payload,
    _write_model_refresh_cache,
)
from kalshi_weather.model.model_estimates import ModelEstimate as SourceModelEstimate
from kalshi_weather.trader_agent.context_builder import build_context_from_inputs
from kalshi_weather.trader_agent.trader_types import MarketBracket, ModelEstimate, ProbabilityBin, RiskLimits


class _FakeStore:
    def save_model_estimate(self, _estimate: object) -> int:
        return 1

    def save_model_estimate_probability(self, _probability: object) -> int:
        return 1


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_estimate_probability_residual_sigma_f=1.0,
        monte_carlo_samples=10,
        kalshi_enable_real_orders=False,
    )


def _install_refresh_fakes(monkeypatch):
    calls = {"fast": 0, "noaa": 0, "market": 0}

    def fake_market_context(_settings, _series, market_date):
        calls["market"] += 1
        return {
            "market_date": market_date,
            "markets": [],
            "brackets": [],
            "tops": {},
            "orderbooks": {},
        }

    def fake_weather_context(_settings, _station, _market_date):
        weather = SimpleNamespace(
            observed_high_so_far_f=68.0,
            latest_observation_utc="2026-07-01T18:00:00+00:00",
            model_future_high_f=70.0,
        )
        forecast = SimpleNamespace()
        return weather, forecast

    def fake_estimates(_settings, _station, providers_raw, *_args):
        if providers_raw == "noaa_herbie":
            calls["noaa"] += 1
            return [_source_estimate("noaa_herbie", "hrrr", 70.5)]
        calls["fast"] += 1
        return [_source_estimate("current", "current_weighted_blend", 70.0)]

    monkeypatch.setattr("kalshi_weather.cli._market_context", fake_market_context)
    monkeypatch.setattr("kalshi_weather.cli._weather_context", fake_weather_context)
    monkeypatch.setattr("kalshi_weather.cli._estimates_from_weather", fake_estimates)
    monkeypatch.setattr("kalshi_weather.cli._store", lambda _settings: _FakeStore())
    monkeypatch.setattr("kalshi_weather.cli.forecast_diagnostics", lambda _forecast: {})
    return calls


def _source_estimate(provider: str, model_id: str, high_f: float) -> SourceModelEstimate:
    return SourceModelEstimate(
        asof_utc=datetime.now(timezone.utc),
        station="KLAX",
        market_date=date(2026, 7, 2),
        provider=provider,
        model_id=model_id,
        model_name=model_id,
        model_family=provider,
        run_utc=None,
        cycle_utc=None,
        forecast_window_start_utc=None,
        forecast_window_end_utc=None,
        future_high_f=high_f,
        settlement_high_estimate_f=high_f,
        successful=True,
    )


def _cached_payload(
    cache: dict[str, object],
    *,
    noaa_mode: str = "full_recompute_each_iteration",
    use_cached_models: bool = False,
    force_model_recompute_every_iteration: bool = True,
    model_refresh_seconds: int = 0,
) -> dict[str, object]:
    return _trader_cached_model_payload(
        settings=_settings(),
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        cache=cache,
        noaa_model_mode=noaa_mode,
        market_refresh_seconds=60,
        fast_model_refresh_seconds=300,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        use_cached_models=use_cached_models,
        force_model_recompute_every_iteration=force_model_recompute_every_iteration,
        model_refresh_seconds=model_refresh_seconds,
    )


def test_market_loop_uses_cached_noaa_between_refreshes(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    first = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=300,
    )
    second = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=300,
    )

    assert calls["noaa"] == 1
    assert first["noaa_cache_used"] is False
    assert second["noaa_cache_used"] is True


def test_no_use_cached_models_forces_recompute_every_iteration(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    first = _cached_payload(cache)
    second = _cached_payload(cache)

    assert calls["fast"] == 2
    assert calls["noaa"] == 2
    assert first["model_source_mode"] == "fresh_recompute_each_iteration"
    assert second["model_source_mode"] == "fresh_recompute_each_iteration"
    assert second["model_cache_used"] is False
    assert second["fast_model_cache_used"] is False
    assert second["noaa_cache_used"] is False
    assert second["noaa_model_mode"] == "full_recompute_each_iteration"
    assert second["noaa_cache_age_seconds"] is None
    assert second["noaa_next_refresh_utc"] is None
    assert second["force_model_recompute_every_iteration"] is True
    assert second["use_cached_models"] is False


def test_model_refresh_seconds_zero_disables_bot_model_cache(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=300,
    )
    payload = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=0,
    )

    assert calls["fast"] == 2
    assert calls["noaa"] == 2
    assert payload["model_source_mode"] == "fresh_recompute_each_iteration"
    assert payload["model_cache_used"] is False
    assert payload["fast_model_cache_used"] is False
    assert payload["noaa_cache_used"] is False


def test_force_model_recompute_every_iteration_disables_fast_with_cached_noaa(monkeypatch) -> None:
    _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=True,
        model_refresh_seconds=300,
    )
    payload = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=True,
        model_refresh_seconds=300,
    )

    assert payload["model_source_mode"] != "fast_with_cached_noaa"
    assert payload["model_source_mode"] == "fresh_recompute_each_iteration"
    assert payload["noaa_cache_used"] is False


def test_cached_model_warning_if_cache_used_when_disabled(monkeypatch) -> None:
    _install_refresh_fakes(monkeypatch)
    payload = _trader_cached_model_payload(
        settings=_settings(),
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        cache={},
        noaa_model_mode="full_recompute_each_iteration",
        market_refresh_seconds=60,
        fast_model_refresh_seconds=300,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        use_cached_models=False,
        force_model_recompute_every_iteration=True,
        model_refresh_seconds=0,
    )

    diagnostics = payload["model_source_diagnostics"]
    diagnostics["noaa_cache_used"] = True
    if (
        diagnostics["force_model_recompute_every_iteration"]
        and (diagnostics["model_cache_used"] or diagnostics["fast_model_cache_used"] or diagnostics["noaa_cache_used"])
    ):
        diagnostics["warnings"].append(
            "cached model estimate was used even though --no-use-cached-models or force recompute was enabled"
        )

    assert "cached model estimate was used even though --no-use-cached-models or force recompute was enabled" in diagnostics["warnings"]


def test_noaa_mode_off_skips_herbie_fetch(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)

    payload = _cached_payload({}, noaa_mode="off")

    assert calls["noaa"] == 0
    assert payload["model_source_mode"] == "fast_noaa_off"
    assert payload["model_source_degraded"] is True


def test_noaa_mode_scheduled_refreshes_after_interval(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=300,
    )
    cache["noaa_last_refresh_utc"] = datetime.now(timezone.utc) - timedelta(seconds=901)
    payload = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=300,
    )

    assert calls["noaa"] == 2
    assert payload["noaa_cache_used"] is False


def test_noaa_off_reduces_model_weight() -> None:
    blended, debug = _blend_probabilities_for_context(
        {"70-71": 0.90},
        market_distribution={"probability_by_bracket": {"70-71": 0.50}},
        active_profile="fixed",
        model_disagreement_level="low",
        probability_blend_mode="raw",
        model_source_degraded=True,
    )

    row = debug["by_bracket"]["70-71"]
    assert row["model_weight"] < 1.0
    assert blended["70-71"] < 0.90


def test_noaa_off_allows_high_confidence_no_when_model_trusted() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-07-02",
        model_estimates=[ModelEstimate("current:current_weighted_blend", 70.0)],
        probability_bins=[ProbabilityBin("70-71", 0.90), ProbabilityBin("72-73", 0.10)],
        market_brackets=[
            MarketBracket("KXHIGHLAX-26JUL02", "KXHIGHLAX-26JUL02-B70.5", "70-71", yes_bid_cents=60, yes_ask_cents=61),
            MarketBracket("KXHIGHLAX-26JUL02", "KXHIGHLAX-26JUL02-B72.5", "72-73", no_bid_cents=50, no_ask_cents=51),
        ],
        risk_limits=RiskLimits(min_edge_cents=-10.0),
        recent_price_trend_summary={
            "model_source": {
                "model_source_degraded": True,
                "model_source_degraded_reason": "noaa_model_mode_off",
                "model_source_mode": "fast_noaa_off",
            }
        },
    )
    risk_limits = RiskLimits(min_edge_cents=-10.0)
    _result, rules = _rule_decision_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        risk_limits=risk_limits,
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.0,
            tail_risk_padding_cents=0.0,
            passive_improvement_cents=1,
        ),
        risk_config=_edge_risk_config(
            risk_limits=risk_limits,
            min_yes_edge_cents=-10.0,
            min_no_edge_cents=-10.0,
            min_no_upside_cents=0.0,
            max_no_bin_probability=1.0,
            max_spread_cents=100,
        ),
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio={"cash_value": 1000.0}),
    )

    no_rows = [row for row in rules["candidate_board"] if row["action"] == "BUY" and row["side"] == "NO"]
    assert no_rows
    assert any(row["eligible"] for row in no_rows)
    assert all(
        (row["metadata"] or {}).get("pre_rejection_reason") != "model_source_degraded_blocks_high_confidence_no"
        for row in no_rows
    )


def test_noaa_off_blocks_high_confidence_no_when_strict_flag_enabled() -> None:
    context = build_context_from_inputs(
        series="KXHIGHLAX",
        station="KLAX",
        market_date="2026-07-02",
        model_estimates=[ModelEstimate("current:current_weighted_blend", 70.0)],
        probability_bins=[ProbabilityBin("70-71", 0.90), ProbabilityBin("72-73", 0.10)],
        market_brackets=[
            MarketBracket("KXHIGHLAX-26JUL02", "KXHIGHLAX-26JUL02-B70.5", "70-71", yes_bid_cents=60, yes_ask_cents=61),
            MarketBracket("KXHIGHLAX-26JUL02", "KXHIGHLAX-26JUL02-B72.5", "72-73", no_bid_cents=50, no_ask_cents=51),
        ],
        risk_limits=RiskLimits(min_edge_cents=-10.0, block_no_on_model_source_degraded=True),
        recent_price_trend_summary={
            "model_source": {
                "model_source_degraded": True,
                "model_source_degraded_reason": "noaa_model_mode_off",
                "model_source_mode": "fast_noaa_off",
            }
        },
    )
    risk_limits = RiskLimits(min_edge_cents=-10.0, block_no_on_model_source_degraded=True)
    _result, rules = _rule_decision_for_context(
        context,
        strategy="hybrid",
        decision_mode="rules",
        order_style="passive",
        risk_limits=risk_limits,
        cost_config=_edge_cost_config(
            risk_limits=risk_limits,
            slippage_cents=0.0,
            tail_risk_padding_cents=0.0,
            passive_improvement_cents=1,
        ),
        risk_config=_edge_risk_config(
            risk_limits=risk_limits,
            min_yes_edge_cents=-10.0,
            min_no_edge_cents=-10.0,
            min_no_upside_cents=0.0,
            max_no_bin_probability=1.0,
            max_spread_cents=100,
        ),
        portfolio_state=_edge_portfolio_state_from_context(context, fills=[], portfolio={"cash_value": 1000.0}),
    )

    no_rows = [row for row in rules["candidate_board"] if row["action"] == "BUY" and row["side"] == "NO"]
    assert no_rows
    assert all(not row["eligible"] for row in no_rows)
    assert all(
        (row["metadata"] or {}).get("pre_rejection_reason") == "model_source_degraded_blocks_high_confidence_no"
        for row in no_rows
    )


def test_runtime_diagnostics_include_noaa_fetch_time(monkeypatch) -> None:
    _install_refresh_fakes(monkeypatch)

    payload = _cached_payload({})

    assert "noaa_fetch_elapsed_seconds" in payload
    assert "fast_model_fetch_elapsed_seconds" in payload
    assert "market_fetch_elapsed_seconds" in payload


def test_official_settlement_not_disabled_by_noaa_model_off(monkeypatch) -> None:
    _install_refresh_fakes(monkeypatch)

    payload = _cached_payload({}, noaa_mode="off")

    assert payload["observed_high_so_far_f"] == 68.0
    assert payload["latest_observation_utc"] == "2026-07-01T18:00:00+00:00"
    assert payload["paper_trading"] is False


def test_market_cycle_forwards_refresh_and_cache_flags(tmp_path) -> None:
    cache_path = tmp_path / "model_refresh_cache.json"

    command = _market_cycle_paper_command(
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        race_id="cycle_test",
        journal_path=tmp_path / "diagnostic.sqlite",
        debug_dir=tmp_path,
        decision_mode="rules",
        strategy="hybrid",
        order_style="passive",
        paper_fill_price_mode="conservative",
        cancel_existing_passive_orders_on_taker_start=True,
        profile_mode="auto",
        lifecycle_active_profile="active_nowcast",
        profile_config="configs/trader_time_profiles.yaml",
        probability_blend_mode="blend",
        probability_blend_config="configs/probability_blend_defaults.yaml",
        model_authoritative=False,
        model_weight=None,
        market_weight=None,
        use_market_implied_probability_as_prior="true",
        no_probability_filter_mode=None,
        no_probability_penalty_start=None,
        no_probability_penalty_factor=0.30,
        absolute_no_bin_probability_cap=0.60,
        allow_cheap_ask_yes_with_missing_bid=False,
        cheap_ask_yes_max_cents=2.0,
        cheap_ask_yes_min_net_edge_cents=8.0,
        cheap_ask_yes_max_contracts=25,
        starting_cash=1000,
        min_edge_cents=2.0,
        min_no_edge_cents=2.0,
        min_no_upside_cents=2.0,
        max_no_bin_probability=0.4,
        journal_exists=False,
        interval_seconds=60,
        model_refresh_seconds=300,
        market_refresh_seconds=60,
        fast_model_refresh_seconds=300,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        noaa_model_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_cache_path=cache_path,
        allow_scale_in=False,
        model_consensus_enabled=True,
        consensus_method="family_weighted_iqr",
        block_high_confidence_no_on_extreme_spread=False,
        extreme_spread_no_block_threshold_f=8.0,
        block_no_on_model_source_degraded=False,
        show_snapshot="changed",
        snapshot_every=15,
        snapshot_style="compact",
        show_settlement_scenarios=True,
        settlement_scenario_style="compact",
        debug_decision=True,
        explain_hold=True,
        audit_pricing=True,
        audit_portfolio=True,
        audit_data=True,
        show_rejections="summary",
    )

    joined = " ".join(str(part) for part in command)
    assert "--model-refresh-seconds 300" in joined
    assert "--market-refresh-seconds 60" in joined
    assert "--fast-model-refresh-seconds 300" in joined
    assert "--min-edge-cents 2.0" in joined
    assert "--min-no-edge-cents 2.0" in joined
    assert "--min-no-upside-cents 2.0" in joined
    assert "--max-no-bin-probability 0.4" in joined
    assert "--noaa-model-refresh-seconds 900" in joined
    assert "--noaa-model-mode scheduled" in joined
    assert "--use-cached-models" in command
    assert "--no-force-model-recompute-every-iteration" in command
    assert f"--model-cache-path {cache_path}" in joined
    assert "--profile-mode auto" in joined
    assert "--lifecycle-active-profile active_nowcast" in joined
    assert "--fresh-journal" in command
    assert "--resume-paper-portfolio" not in command
    assert "--no-allow-scale-in" in command
    assert "--no-block-high-confidence-no-on-extreme-spread" in command
    assert "--no-block-no-on-model-source-degraded" in command
    assert "--show-settlement-scenarios" in command


def test_market_cycle_forwards_model_authoritative_flags(tmp_path) -> None:
    command = _market_cycle_paper_command(
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        race_id="cycle_model_authoritative",
        journal_path=tmp_path / "diagnostic.sqlite",
        debug_dir=tmp_path,
        decision_mode="rules",
        strategy="hybrid",
        order_style="passive",
        paper_fill_price_mode="conservative",
        cancel_existing_passive_orders_on_taker_start=True,
        profile_mode="fixed_test",
        lifecycle_active_profile="active_nowcast",
        profile_config=None,
        probability_blend_mode="model_only",
        probability_blend_config=None,
        model_authoritative=True,
        model_weight=1.0,
        market_weight=0.0,
        use_market_implied_probability_as_prior="false",
        no_probability_filter_mode="soft_penalty",
        no_probability_penalty_start=0.40,
        no_probability_penalty_factor=0.30,
        absolute_no_bin_probability_cap=0.60,
        allow_cheap_ask_yes_with_missing_bid=True,
        cheap_ask_yes_max_cents=2.0,
        cheap_ask_yes_min_net_edge_cents=8.0,
        cheap_ask_yes_max_contracts=25,
        starting_cash=1000,
        min_edge_cents=1.0,
        min_no_edge_cents=1.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.6,
        journal_exists=False,
        interval_seconds=60,
        model_refresh_seconds=60,
        market_refresh_seconds=60,
        fast_model_refresh_seconds=60,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        noaa_model_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_cache_path=tmp_path / "model_refresh_cache.json",
        allow_scale_in=False,
        model_consensus_enabled=True,
        consensus_method="family_weighted_iqr",
        block_high_confidence_no_on_extreme_spread=False,
        extreme_spread_no_block_threshold_f=8.0,
        block_no_on_model_source_degraded=False,
        show_snapshot="every",
        snapshot_every=1,
        snapshot_style="table",
        show_settlement_scenarios=True,
        settlement_scenario_style="compact",
        debug_decision=True,
        explain_hold=True,
        audit_pricing=True,
        audit_portfolio=True,
        audit_data=True,
        show_rejections="summary",
    )

    joined = " ".join(str(part) for part in command)
    assert "--model-authoritative" in command
    assert "--probability-blend-mode model_only" in joined
    assert "--model-weight 1.0" in joined
    assert "--market-weight 0.0" in joined
    assert "--use-market-implied-probability-as-prior false" in joined
    assert "--no-probability-filter-mode soft_penalty" in joined
    assert "--no-probability-penalty-start 0.4" in joined
    assert "--no-probability-penalty-factor 0.3" in joined
    assert "--absolute-no-bin-probability-cap 0.6" in joined
    assert "--allow-cheap-ask-yes-with-missing-bid" in command


def test_market_cycle_forwards_taker_no_cache_recompute_flags(tmp_path) -> None:
    command = _market_cycle_paper_command(
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        race_id="cycle_taker_no_cache",
        journal_path=tmp_path / "diagnostic.sqlite",
        debug_dir=tmp_path,
        decision_mode="rules",
        strategy="hybrid",
        order_style="taker",
        paper_fill_price_mode="conservative",
        cancel_existing_passive_orders_on_taker_start=True,
        profile_mode="fixed_test",
        lifecycle_active_profile="active_nowcast",
        profile_config=None,
        probability_blend_mode="model_only",
        probability_blend_config=None,
        model_authoritative=True,
        model_weight=1.0,
        market_weight=0.0,
        use_market_implied_probability_as_prior="false",
        no_probability_filter_mode="soft_penalty",
        no_probability_penalty_start=None,
        no_probability_penalty_factor=0.30,
        absolute_no_bin_probability_cap=0.60,
        allow_cheap_ask_yes_with_missing_bid=False,
        cheap_ask_yes_max_cents=2.0,
        cheap_ask_yes_min_net_edge_cents=8.0,
        cheap_ask_yes_max_contracts=25,
        starting_cash=1000,
        min_edge_cents=1.0,
        min_no_edge_cents=1.0,
        min_no_upside_cents=1.0,
        max_no_bin_probability=0.4,
        journal_exists=False,
        interval_seconds=60,
        model_refresh_seconds=0,
        market_refresh_seconds=60,
        fast_model_refresh_seconds=60,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        noaa_model_mode="full_recompute_each_iteration",
        use_cached_models=False,
        force_model_recompute_every_iteration=True,
        model_cache_path=None,
        allow_scale_in=False,
        model_consensus_enabled=True,
        consensus_method="family_weighted_iqr",
        block_high_confidence_no_on_extreme_spread=False,
        extreme_spread_no_block_threshold_f=8.0,
        block_no_on_model_source_degraded=False,
        show_snapshot="every",
        snapshot_every=1,
        snapshot_style="table",
        show_settlement_scenarios=True,
        settlement_scenario_style="compact",
        debug_decision=True,
        explain_hold=True,
        audit_pricing=True,
        audit_portfolio=True,
        audit_data=True,
        show_rejections="summary",
    )

    joined = " ".join(str(part) for part in command)
    assert "--order-style taker" in joined
    assert "--cancel-existing-passive-orders-on-taker-start" in command
    assert "--no-use-cached-models" in command
    assert "--force-model-recompute-every-iteration" in command
    assert "--model-refresh-seconds 0" in joined
    assert "--noaa-model-mode full_recompute_each_iteration" in joined
    assert "--model-authoritative" in command
    assert "--probability-blend-mode model_only" in joined


def test_market_cycle_resume_does_not_pass_starting_cash(tmp_path) -> None:
    command = _market_cycle_paper_command(
        series="KXHIGHLAX",
        station="KLAX",
        target_date=date(2026, 7, 2),
        race_id="cycle_resume",
        journal_path=tmp_path / "diagnostic.sqlite",
        debug_dir=tmp_path,
        decision_mode="rules",
        strategy="hybrid",
        order_style="passive",
        paper_fill_price_mode="conservative",
        cancel_existing_passive_orders_on_taker_start=True,
        profile_mode="auto",
        lifecycle_active_profile="active_nowcast",
        profile_config=None,
        probability_blend_mode="blend",
        probability_blend_config=None,
        model_authoritative=False,
        model_weight=None,
        market_weight=None,
        use_market_implied_probability_as_prior="true",
        no_probability_filter_mode=None,
        no_probability_penalty_start=None,
        no_probability_penalty_factor=0.30,
        absolute_no_bin_probability_cap=0.60,
        allow_cheap_ask_yes_with_missing_bid=False,
        cheap_ask_yes_max_cents=2.0,
        cheap_ask_yes_min_net_edge_cents=8.0,
        cheap_ask_yes_max_contracts=25,
        starting_cash=1000,
        min_edge_cents=2.0,
        min_no_edge_cents=2.0,
        min_no_upside_cents=2.0,
        max_no_bin_probability=0.4,
        journal_exists=True,
        interval_seconds=60,
        model_refresh_seconds=0,
        market_refresh_seconds=60,
        fast_model_refresh_seconds=300,
        noaa_model_refresh_seconds=900,
        observation_refresh_seconds=300,
        noaa_model_mode="scheduled",
        use_cached_models=False,
        force_model_recompute_every_iteration=True,
        model_cache_path=None,
        allow_scale_in=False,
        model_consensus_enabled=True,
        consensus_method="family_weighted_iqr",
        block_high_confidence_no_on_extreme_spread=True,
        extreme_spread_no_block_threshold_f=8.0,
        block_no_on_model_source_degraded=True,
        show_snapshot="changed",
        snapshot_every=15,
        snapshot_style="compact",
        show_settlement_scenarios=False,
        settlement_scenario_style="compact",
        debug_decision=False,
        explain_hold=False,
        audit_pricing=False,
        audit_portfolio=False,
        audit_data=False,
        show_rejections="summary",
    )

    assert "--resume-paper-portfolio" in command
    assert "--fresh-journal" not in command
    assert "--starting-cash" not in command
    assert "--model-refresh-seconds" in command
    assert "0" in command
    assert "--no-use-cached-models" in command
    assert "--force-model-recompute-every-iteration" in command
    assert "--block-high-confidence-no-on-extreme-spread" in command
    assert "--block-no-on-model-source-degraded" in command




def test_fast_model_fetch_failure_without_cache_fails_closed(monkeypatch) -> None:
    _install_refresh_fakes(monkeypatch)

    def broken_weather_context(*_args, **_kwargs):
        raise ConnectionError("open-meteo connection reset")

    monkeypatch.setattr("kalshi_weather.cli._weather_context", broken_weather_context)
    payload = _cached_payload(
        {},
        noaa_mode="off",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=60,
    )

    assert payload["all_estimate_count"] == 0
    assert payload["fast_model_cache_used"] is False
    assert payload["model_cache_used"] is False
    assert payload["model_source_degraded"] is True
    assert "fast_model_fetch_failed_no_cache" in str(payload["model_source_degraded_reason"])

def test_fast_model_fetch_failure_uses_cached_snapshot(monkeypatch) -> None:
    calls = _install_refresh_fakes(monkeypatch)
    cache: dict[str, object] = {}

    _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=60,
    )
    cache["fast_model_last_refresh_utc"] = datetime.now(timezone.utc) - timedelta(seconds=301)
    cache["observation_last_refresh_utc"] = datetime.now(timezone.utc) - timedelta(seconds=301)

    def broken_weather_context(*_args, **_kwargs):
        raise ConnectionError("open-meteo connection reset")

    monkeypatch.setattr("kalshi_weather.cli._weather_context", broken_weather_context)
    payload = _cached_payload(
        cache,
        noaa_mode="scheduled",
        use_cached_models=True,
        force_model_recompute_every_iteration=False,
        model_refresh_seconds=60,
    )

    assert calls["fast"] == 1
    assert payload["fast_model_cache_used"] is True
    assert payload["model_cache_used"] is True
    assert payload["model_source_degraded"] is True
    assert "fast_model_fetch_failed_using_cache" in str(payload["model_source_degraded_reason"])
    assert payload["all_estimate_count"] >= 1

def test_noaa_model_cache_round_trips_to_disk(tmp_path) -> None:
    cache_path = tmp_path / "model_refresh_cache.json"
    refreshed_at = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    estimate = _source_estimate("noaa_herbie", "hrrr", 70.5)

    _write_model_refresh_cache(
        cache_path,
        {
            "noaa_last_refresh_utc": refreshed_at,
            "noaa_estimates": [estimate],
        },
    )
    loaded = _model_refresh_cache_from_disk(cache_path)

    assert loaded["noaa_last_refresh_utc"] == refreshed_at
    assert loaded["noaa_estimates"][0].provider == "noaa_herbie"
    assert loaded["noaa_estimates"][0].model_id == "hrrr"


def test_fast_model_cache_round_trips_to_disk(tmp_path) -> None:
    cache_path = tmp_path / "model_refresh_cache.json"
    refreshed_at = datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    estimate = _source_estimate("open_meteo", "gfs013", 71.2)
    weather = SimpleNamespace(
        station_id="KLAX",
        timestamp_utc=refreshed_at,
        observed_high_so_far_f=69.0,
        latest_observation_utc=refreshed_at,
        observation_count=12,
        model_future_high_f=71.2,
        model_details={"selected_future_high_f": 71.2},
    )
    forecast = SimpleNamespace(
        successful_models=["gfs013"],
        failed_models={},
        fallback_used=False,
        model_maxes_f={"temperature_2m__gfs013": 71.2},
        raw_columns=["time", "temperature_2m__gfs013"],
        feature_summary={"future_max_f": 71.2},
        failed_variable_requests={},
    )

    _write_model_refresh_cache(
        cache_path,
        {
            "fast_model_last_refresh_utc": refreshed_at,
            "observation_last_refresh_utc": refreshed_at,
            "fast_estimates": [estimate],
            "weather": weather,
            "forecast": forecast,
        },
    )
    loaded = _model_refresh_cache_from_disk(cache_path)

    assert loaded["fast_model_last_refresh_utc"] == refreshed_at
    assert loaded["observation_last_refresh_utc"] == refreshed_at
    assert loaded["fast_estimates"][0].provider == "open_meteo"
    assert loaded["fast_estimates"][0].model_id == "gfs013"
    assert loaded["weather"].model_future_high_f == 71.2
    assert loaded["forecast"].model_maxes_f["temperature_2m__gfs013"] == 71.2
