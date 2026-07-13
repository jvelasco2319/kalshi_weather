from __future__ import annotations

from decimal import Decimal

import pytest

from kalshi_weather.strategy_current.economics import (
    fee,
    max_qualifying_price,
    trade_economics,
    whole_cent_price_grid,
)
from kalshi_weather.strategy_current.risk import (
    EventPosition,
    breaches_event_loss_cap,
    drift_flag,
    event_outcome_pnl,
    full_kelly_fraction,
    spread_policy,
    used_kelly_fraction,
)
from kalshi_weather.strategy_current.settlement import (
    SettlementBracket,
    bracket_for_official_high,
    official_integer_f,
    validate_settlement_brackets,
)


def test_settlement_brackets_are_exhaustive_and_quantize_once() -> None:
    brackets = [
        SettlementBracket("below", None, 65),
        SettlementBracket("mid", 66, 67),
        SettlementBracket("above", 68, None),
    ]

    validate_settlement_brackets(brackets)
    assert official_integer_f(65.5) == 66
    assert bracket_for_official_high(67.4, brackets).bracket_id == "mid"
    assert bracket_for_official_high(67.5, brackets).bracket_id == "above"
    with pytest.raises(ValueError, match="gap or overlap"):
        validate_settlement_brackets(
            [SettlementBracket("below", None, 65), SettlementBracket("above", 67, None)]
        )


def test_fee_aware_price_ceiling_reference_vectors() -> None:
    prices = whole_cent_price_grid()

    assert max_qualifying_price(
        probability=Decimal("1"),
        quantity=100,
        role="taker",
        hurdle=Decimal("0.15"),
        price_levels=prices,
    ) == Decimal("0.86")
    assert max_qualifying_price(
        probability=Decimal("1"),
        quantity=100,
        role="taker",
        hurdle=Decimal("0.10"),
        price_levels=prices,
    ) == Decimal("0.90")
    assert fee(quantity=100, price=Decimal("0.86"), role="taker") > 0


def test_yes_and_no_economics_are_symmetric_inputs() -> None:
    yes = trade_economics(
        side="yes",
        probability=Decimal("0.70"),
        quantity=10,
        price=Decimal("0.50"),
        role="maker",
    )
    no = trade_economics(
        side="no",
        probability=Decimal("0.70"),
        quantity=10,
        price=Decimal("0.50"),
        role="maker",
    )

    assert yes.expected_value == no.expected_value
    assert yes.roi == no.roi


def test_spread_drift_kelly_and_event_loss_matrix() -> None:
    assert spread_policy(2.9).hard_stop is False
    assert spread_policy(3.2).size_multiplier == Decimal("0.50")
    assert spread_policy(4.0).hard_stop is True
    assert drift_flag(1.6, 0.0) is True
    assert drift_flag(1.2, -1.1) is True

    full = full_kelly_fraction(Decimal("0.70"), Decimal("0.50"))
    used = used_kelly_fraction(Decimal("0.70"), Decimal("0.50"))
    assert used == full * Decimal("0.25")

    outcomes = event_outcome_pnl(
        bracket_count=3,
        positions=[
            EventPosition(0, "yes", Decimal("1"), Decimal("0.40")),
            EventPosition(1, "no", Decimal("1"), Decimal("0.30")),
        ],
    )
    assert outcomes == (Decimal("1.30"), Decimal("-0.70"), Decimal("0.30"))
    assert breaches_event_loss_cap(outcomes, Decimal("0.50")) is True
