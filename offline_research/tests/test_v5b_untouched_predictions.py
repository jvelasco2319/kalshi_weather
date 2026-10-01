from v5b_confirmation.predictions import _bounds, _ordered


def contract(ticker, kind, floor, cap):
    return {
        "market_ticker": ticker,
        "strike_type": kind,
        "floor_strike": floor,
        "cap_strike": cap,
    }


def test_contract_partition_orders_tails_and_interiors() -> None:
    rows = [
        contract("upper", "greater", 76, None),
        contract("b3", "between", 73, 74),
        contract("lower", "less", None, 69),
        contract("b1", "between", 69, 70),
        contract("b4", "between", 75, 76),
        contract("b2", "between", 71, 72),
    ]
    assert [row["market_ticker"] for row in _ordered(rows)] == [
        "lower", "b1", "b2", "b3", "b4", "upper",
    ]
    assert _bounds(rows[0]).integer_bounds()[1] is None
