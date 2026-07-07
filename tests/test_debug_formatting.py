from kalshi_weather.rules_engine_ext.debug_formatting import cleanup_cancel_candidate, edge_passes, format_buy_note


def test_cancel_cleanup_nulls_edge():
    c = cleanup_cancel_candidate({"candidate_type": "CANCEL", "net_edge_cents": 9999, "candidate_score": 9999})
    assert c["net_edge_cents"] is None
    assert c["candidate_score"] is None
    assert c["risk_control_priority_score"] == 9999
    assert c["deterministic_note"] == "risk-control cancel"


def test_buy_note_uses_final_net_edge():
    note = format_buy_note(62.4, 44.0, 18.4, 9.9, 8.0, 0.5)
    assert "final net edge 9.9c" in note
    assert "raw edge 18.4c" in note


def test_edge_epsilon():
    assert edge_passes(7.999999999, 8.0, 0.001)
