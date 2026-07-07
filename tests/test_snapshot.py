from kalshi_weather.edge_engine.snapshot import SnapshotConfig, SnapshotState, render_compact_snapshot, should_print_snapshot
from kalshi_weather.edge_engine.types import Action, CandidateTrade, MarketQuote, OrderType, Side


def test_snapshot_prints_first_iteration_and_changes():
    cfg = SnapshotConfig(show_snapshot="changed", snapshot_every=5)
    prev = SnapshotState(iteration=1, top_bracket="70-71", best_candidate_id="a", blend_temp_f=70.0)
    cur = SnapshotState(iteration=2, top_bracket="72-73", best_candidate_id="a", blend_temp_f=70.0)
    assert should_print_snapshot(config=cfg, previous=None, current=prev)
    assert should_print_snapshot(config=cfg, previous=prev, current=cur)


def test_compact_snapshot_is_short_and_contains_best_edge():
    c = CandidateTrade("c1", Action.BUY, Side.NO, "72-73", OrderType.TAKER, quantity=1, price_cents=64, net_edge_cents=19.2, spread_cents=1, upside_cents=36, max_loss_dollars=0.64)
    text = render_compact_snapshot(
        timestamp="10:25",
        blend_temp_f=70.3,
        top_bracket="70-71",
        model_agreement="medium",
        outliers_note="HRRR/NBM low",
        quotes=[MarketQuote("72-73", yes_bid_cents=36, yes_ask_cents=37, no_bid_cents=63, no_ask_cents=64)],
        candidates=[c],
    )
    assert "Snapshot 10:25" in text
    assert "Best edge" in text
    assert len(max(text.splitlines(), key=len)) < 180
