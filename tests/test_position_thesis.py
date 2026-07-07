from kalshi_weather.rules_engine_ext.position_thesis import EntryThesis, CurrentThesis, evaluate_position


def entry():
    return EntryThesis(
        bracket="69-70",
        side="YES",
        entry_price_cents=35,
        entry_final_trade_probability=0.63,
        entry_fair_value_cents=63,
        entry_top_bracket="69-70",
        entry_model_disagreement_level="medium",
        entry_full_model_spread_f=4.0,
        active_profile="active_nowcast",
    )


def test_yes_holds_when_model_thesis_valid_despite_markdown():
    cur = CurrentThesis(0.61, 61, "69-70", "medium", 4.2, current_mark_cents=25)
    d = evaluate_position(entry(), cur)
    assert d.state == "conviction_hold"
    assert d.hold_reason == "model_thesis_still_valid"
    assert d.market_moved_against_but_model_still_valid is True


def test_yes_closes_when_top_bracket_changes():
    cur = CurrentThesis(0.40, 40, "71-72", "medium", 4.2)
    d = evaluate_position(entry(), cur)
    assert d.state == "close"
    assert "close_model_top_changed" in d.close_reasons


def test_take_profit_watch_when_mark_reaches_target():
    cur = CurrentThesis(0.64, 64, "69-70", "medium", 4.2, current_mark_cents=48)
    d = evaluate_position(entry(), cur, take_profit_gain_cents=12)
    assert d.state == "take_profit_watch"
    assert d.take_profit_reached is True
    assert d.take_profit_fraction == 0.5
    assert d.take_profit_reason == "market reached first take-profit target"


def test_take_profit_target_2_near_fair_generates_partial_close():
    cur = CurrentThesis(0.64, 64, "69-70", "medium", 4.2, current_mark_cents=60)
    d = evaluate_position(entry(), cur)
    assert d.state == "partial_take_profit"
    assert d.take_profit_target_2_cents == 59
