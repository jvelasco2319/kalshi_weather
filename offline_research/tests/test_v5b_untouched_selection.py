import pandas as pd

from v5b_confirmation.selection import select_days


PARAMETERS = {
    "additional_adverse_price_cents": 0,
    "allowed_grades": ["A", "B_PLUS", "B"],
    "allowed_sides": ["NO"],
    "calibration": "none",
    "calibration_min_days": 10,
    "calibration_strength": 20.0,
    "maximum_entropy": 1.0,
    "maximum_price_cents": 80,
    "maximum_spread_cents": 5,
    "minimum_expected_net_return": 0.1,
    "minimum_price_cents": 5,
    "minimum_probability_gap": 0.1,
    "neighbor_smoothing": 0.0,
    "probability_haircut": 0.0,
    "probability_power": 1.0,
    "probability_source": "calibrated",
    "selection_mode": "expected_profit",
    "tail_policy": "all",
    "uniform_blend": 0.0,
}


def test_frozen_no_policy_selects_at_most_one_trade_per_day() -> None:
    day = "2026-09-03"
    records = []
    probabilities = []
    for index, probability in enumerate([0.70, 0.10, 0.08, 0.06, 0.04, 0.02]):
        ticker = f"KXHIGHLAX-26SEP03-X{index}"
        kind = "less" if index == 0 else "greater" if index == 5 else "between"
        floor = None if index == 0 else 70 + index
        cap = 72 if index == 0 else None if index == 5 else 71 + index
        probabilities.append({"climate_date": day, "market_ticker": ticker, "yes_probability": probability})
        for side, ask, bid in (("YES", 70, 69), ("NO", 25, 24)):
            records.append({
                "climate_date": day,
                "market_ticker": ticker,
                "contract_side": side,
                "strike_type": kind,
                "floor_strike": floor,
                "cap_strike": cap,
                "execution_evidence_grade": "A",
                "execution_price_cents": ask,
                "bid_price_cents": bid,
                "confirmation_window_role": "post-development-level-2",
                "settlement_sources": [{"name": "The Weather Company", "url": None}],
                "assumed_fill": False,
                "execution_source_kind": "PAID_FULL_BOOK",
            })
    selected = select_days([day], records, pd.DataFrame(probabilities), PARAMETERS)
    assert len(selected) == 1
    assert selected[0]["contract_side"] == "NO"
    assert selected[0]["market_ticker"].endswith("X5")
    assert selected[0]["expected_net_return"] >= 0.10
