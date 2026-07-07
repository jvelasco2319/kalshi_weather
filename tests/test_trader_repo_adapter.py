from decimal import Decimal

from kalshi_weather.trader_agent.repo_adapter import trader_context_from_model_payload


def test_repo_adapter_builds_yes_and_no_candidates_from_model_payload():
    payload = {
        "generated_at_utc": "2026-06-25T18:00:00+00:00",
        "series": "KXHIGHLAX",
        "station": "KLAX",
        "market_date": "2026-06-26",
        "observed_high_so_far_f": 68.0,
        "latest_observation_utc": "2026-06-25T17:55:00+00:00",
        "estimates": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "settlement_high_estimate_f": 71.0,
                "asof_utc": "2026-06-25T18:00:00+00:00",
            }
        ],
        "probabilities": [
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "KXHIGHLAX-26JUN26-B70.5",
                "bracket_label": "70-71",
                "bracket_lower_f": 70,
                "bracket_upper_f": 71,
                "p_yes": 0.60,
                "yes_bid": Decimal("0.54"),
                "yes_ask": Decimal("0.55"),
                "no_bid": Decimal("0.44"),
                "no_ask": Decimal("0.45"),
            },
            {
                "provider": "current",
                "model_id": "current_weighted_blend",
                "market_ticker": "KXHIGHLAX-26JUN26-B72.5",
                "bracket_label": "72-73",
                "bracket_lower_f": 72,
                "bracket_upper_f": 73,
                "p_yes": 0.25,
                "yes_bid": Decimal("0.34"),
                "yes_ask": Decimal("0.35"),
                "no_bid": Decimal("0.64"),
                "no_ask": Decimal("0.65"),
            },
        ],
    }

    context = trader_context_from_model_payload(payload)
    buy_candidates = [candidate for candidate in context.candidate_trades if candidate.action == "BUY"]

    assert len(context.market_brackets) == 2
    assert len([candidate for candidate in buy_candidates if candidate.side == "YES"]) == 2
    assert len([candidate for candidate in buy_candidates if candidate.side == "NO"]) == 2
    assert any(candidate.candidate_id == "HOLD" for candidate in context.candidate_trades)
