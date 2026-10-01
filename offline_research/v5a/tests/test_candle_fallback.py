from datetime import UTC, datetime

from v5a.candle_fallback import _extract_quote, build


def test_extract_quote_avoids_post_decision_lookahead():
    decision = int(datetime(2026, 6, 1, 18, 0, tzinfo=UTC).timestamp())
    payload = {"candlesticks": [
        {"end_period_ts": decision, "yes_ask": {"close": "0.42"}, "yes_bid": {"close": "0.39"}},
        {"end_period_ts": decision + 60, "yes_ask": {"close": "0.10"}, "yes_bid": {"close": "0.09"}},
    ]}
    yes = _extract_quote(payload, decision_epoch=decision, contract_side="YES")
    no = _extract_quote(payload, decision_epoch=decision, contract_side="NO")
    assert yes["best_ask_proxy_cents"] == 42
    assert no["best_ask_proxy_cents"] == 61


def test_workspace_fallback_is_outcome_blind():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    value = build(root)
    assert value["outcomes_read"] is False
    assert value["protected_confirmation_labels_read"] is False
    assert value["network_used"] is False
    assert value["actual_orders_placed"] is False
    assert value["grade_b_is_assumed_fill"] is True
