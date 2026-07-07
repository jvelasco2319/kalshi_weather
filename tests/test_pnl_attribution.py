from kalshi_weather.edge_engine.pnl_attribution import classify_trade_failure, summarize_attribution


def test_classifies_negative_clv_loss():
    row = {"net_pnl_cents": -20, "clv_30m_cents": -5, "net_edge_cents": 10, "settled_result": 0, "model_probability": 0.8}
    assert classify_trade_failure(row) == "negative_clv_execution_or_model"


def test_summarize_attribution_groups_rows():
    rows = [
        {"net_pnl_cents": -20, "clv_30m_cents": -5},
        {"net_pnl_cents": 10, "clv_30m_cents": 2},
    ]
    summary = summarize_attribution(rows)
    assert sum(x.trades for x in summary) == 2
