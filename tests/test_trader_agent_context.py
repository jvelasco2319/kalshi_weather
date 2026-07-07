from test_trader_trade_board import sample_context


def test_context_is_fake_money_only_and_has_candidates():
    context = sample_context()
    assert context.mode == "fake_money_only"
    assert context.series == "KXHIGHLAX"
    assert context.station == "KLAX"
    assert len(context.candidate_trades) >= 13
    payload = context.to_dict()
    assert payload["mode"] == "fake_money_only"
    assert payload["candidate_trades"]
