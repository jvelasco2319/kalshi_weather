from kalshi_weather.edge_engine.passive_execution import PassiveOrder, should_cancel_passive_order
from kalshi_weather.edge_engine.types import Side


def test_cancel_passive_order_when_edge_disappears():
    order = PassiveOrder("c1", Side.YES, "70-71", 55, 10, "2026-06-26T10:00:00+00:00")
    result = should_cancel_passive_order(order=order, current_edge_cents=2, min_edge_cents=8, now_ts="2026-06-26T10:01:00+00:00")
    assert result.should_cancel
    assert result.reason == "edge_disappeared"


def test_cancel_passive_order_when_stale():
    order = PassiveOrder("c1", Side.YES, "70-71", 55, 10, "2026-06-26T10:00:00+00:00")
    result = should_cancel_passive_order(order=order, current_edge_cents=10, min_edge_cents=8, now_ts="2026-06-26T10:10:01+00:00", max_order_age_seconds=300)
    assert result.should_cancel
    assert result.reason == "order_stale"
