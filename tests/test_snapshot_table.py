from kalshi_weather.edge_engine.snapshot import SnapshotInputs, render_market_table_snapshot, should_emit_snapshot
from kalshi_weather.edge_engine.types import Action, CandidateTrade, MarketQuote, OrderType, Side


def _candidate(cid="c1", edge=10):
    return CandidateTrade(cid, Action.BUY, Side.NO, "72-73", OrderType.TAKER, quantity=1, price_cents=64, net_edge_cents=edge, spread_cents=1, upside_cents=36, max_loss_dollars=0.64)


def test_snapshot_emits_on_blend_move():
    prev = SnapshotInputs(1, "10:00", 70.1, "70-71", best_candidate=_candidate())
    cur = SnapshotInputs(2, "10:01", 70.7, "70-71", best_candidate=_candidate())
    assert should_emit_snapshot(prev, cur, snapshot_every=999)


def test_snapshot_table_includes_each_bracket_once():
    cur = SnapshotInputs(1, "10:00", 70.1, "70-71", quotes=[
        MarketQuote("70-71°", yes_bid_cents=58, yes_ask_cents=59, no_bid_cents=41, no_ask_cents=42),
        MarketQuote("70-71°F", yes_bid_cents=58, yes_ask_cents=59, no_bid_cents=41, no_ask_cents=42),
        MarketQuote("72-73", yes_bid_cents=36, yes_ask_cents=37, no_bid_cents=63, no_ask_cents=64),
    ])
    text = render_market_table_snapshot(cur)
    assert text.count("70-71") == 1
    assert text.count("72-73") == 1
