from kalshi_weather.edge_engine.backtest import BacktestRecord, run_synthetic_backtest
from kalshi_weather.edge_engine.types import CostConfig, MarketQuote, RiskConfig, StrategyConfig


def test_backtest_preserves_quote_columns_from_candidate_metadata():
    rec = BacktestRecord(
        target_date="2026-06-26",
        station="KLAX",
        run_timestamp="2026-06-26T10:00:00",
        official_final_high=70,
        official_final_bracket="70-71",
        probabilities={"70-71": 0.65},
        quotes=[MarketQuote("70-71", yes_bid_cents=58, no_bid_cents=41)],
    )
    rows = run_synthetic_backtest(
        [rec],
        series="KXHIGHLAX",
        cost_config=CostConfig(include_fees=False),
        risk_config=RiskConfig(),
        strategy_config=StrategyConfig(strategy="exact-bin", decision_mode="rules", order_style="taker"),
    )
    assert rows[0]["yes_bid_cents"] == 58
    assert rows[0]["yes_ask_cents"] == 59
    assert rows[0]["no_bid_cents"] == 41
    assert rows[0]["no_ask_cents"] == 42
