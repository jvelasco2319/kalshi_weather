import json
from pathlib import Path

from past7_replay.engine import _read_json
from scripts.validate_v7h_replay import CAMPAIGN_ID, EXPECTED_DATES, validate
from v7h_replay.engine import _load_outcomes


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/replays" / CAMPAIGN_ID


def test_v7h_is_exactly_six_historical_weeks_and_outcome_blind_at_freeze():
    freeze = _read_json(RUN / "prediction-order-freeze.json", sealed=True)
    assert tuple(freeze["target_dates"]) == EXPECTED_DATES
    assert len(EXPECTED_DATES) == 42
    assert freeze["training_dates"]["end"] < EXPECTED_DATES[0]
    assert freeze["outcomes_read"] is False
    assert freeze["settlement_labels_read"] is False
    assert freeze["paper_orders_placed"] == 0
    assert freeze["live_orders_placed"] == 0


def test_outcome_loader_uses_explicit_ticker_date_mapping():
    freeze = _read_json(RUN / "prediction-order-freeze.json", sealed=True)
    ticker_to_date = {
        item["market_ticker"]: record["climate_date"]
        for record in freeze["methods"]["v5b_causal_no"]["records"]
        for item in record["probabilities"]
    }
    outcomes = _load_outcomes(RUN / "settlement-outcomes.jsonl", set(ticker_to_date), ticker_to_date)
    assert set(outcomes) == set(EXPECTED_DATES)
    assert all(len(rows) == 6 for rows in outcomes.values())
    assert all(list(rows.values()).count("YES") == 1 for rows in outcomes.values())


def test_independent_validation_reproduces_result_and_preliminary_gate():
    result = validate(ROOT)
    assert result["status"] == "VALIDATED_WITH_CAVEATS"
    assert result["input_binding_failures"] == 0
    assert result["independently_recomputed_scored_rows"] == 294
    assert result["preliminary_gate_winners"] == ["v5b_causal_no"]
    assert result["methods"]["v5b_causal_no"]["filled_count"] == 9
    assert result["methods"]["v5b_causal_no"]["win_count"] == 7
    assert result["statistically_untouched"] is False


def test_v7h_record_keeps_missing_books_and_exposure_limit_explicit():
    record = json.loads((ROOT / "configs/v7h_historical_replay.json").read_text(encoding="utf-8"))
    assert record["source_coverage"]["paid_archive_dates"] == 40
    assert record["source_coverage"]["unavailable_archive_dates"] == ["2026-08-06", "2026-08-10"]
    assert record["statistically_untouched"] is False
    assert record["causal_controls"]["proxy_fills_allowed"] is False
