from kalshi_weather.edge_engine.backtest import REQUIRED_BACKTEST_COLUMNS, BacktestRecord, run_synthetic_backtest
from kalshi_weather.edge_engine.types import CostConfig, MarketQuote, RiskConfig, StrategyConfig


def test_backtest_outputs_required_columns():
    rec = BacktestRecord(
        target_date="2026-06-26",
        station="KLAX",
        run_timestamp="2026-06-26T10:00:00",
        official_final_high=70,
        official_final_bracket="70-71",
        probabilities={"70-71": 0.65, "72-73": 0.12},
        quotes=[
            MarketQuote("70-71", yes_bid_cents=58, no_bid_cents=41),
            MarketQuote("72-73", yes_bid_cents=36, no_bid_cents=63),
        ],
    )
    rows = run_synthetic_backtest(
        [rec],
        series="KXHIGHLAX",
        cost_config=CostConfig(include_fees=False),
        risk_config=RiskConfig(),
        strategy_config=StrategyConfig(strategy="hybrid", decision_mode="rules", order_style="taker"),
    )
    assert rows
    for col in REQUIRED_BACKTEST_COLUMNS:
        assert col in rows[0]
