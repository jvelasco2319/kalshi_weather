from kalshi_weather.edge_engine.costs import kalshi_fee_dollars, fee_cents_per_contract
from kalshi_weather.edge_engine.types import CostConfig, OrderType


def test_kalshi_fee_rounds_up_for_100_contracts_at_50c():
    assert kalshi_fee_dollars(50, 100, rate=0.07) == 1.75


def test_fee_per_contract_reduces_edge():
    cfg = CostConfig(include_fees=True)
    fee_pc = fee_cents_per_contract(50, 100, OrderType.TAKER, cfg)
    assert fee_pc == 1.75


def test_passive_no_maker_fee_by_default():
    cfg = CostConfig(include_fees=True, maker_fee_enabled=False)
    assert fee_cents_per_contract(50, 100, OrderType.PASSIVE_LIMIT, cfg) == 0.0
