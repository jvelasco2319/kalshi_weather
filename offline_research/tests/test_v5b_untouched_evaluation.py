from v5b_confirmation.evaluation import _summary, _winner


def test_winner_and_return_summary() -> None:
    label = {
        "resolution_winning_outcome_id": "no-id",
        "contract_sides": [
            {"id": "yes-id", "name": "Yes"},
            {"id": "no-id", "name": "No"},
        ],
    }
    assert _winner(label) == "NO"
    result = _summary([
        {"entry_outlay_dollars": 0.25, "net_profit_dollars": 0.75, "won": True},
        {"entry_outlay_dollars": 0.50, "net_profit_dollars": -0.50, "won": False},
    ])
    assert result["selected_days"] == 2
    assert result["win_count"] == 1
    assert abs(result["aggregate_realized_net_return"] - (0.25 / 0.75)) < 1e-12
