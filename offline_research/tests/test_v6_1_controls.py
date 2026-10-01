"""Synthetic controls for V6.1 governance and coherent market probabilities."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from v6_1.common import seal, write
from v6_1.market_projection import coherent_market_probabilities
from v6_1.baseline import quote_interval


def test_market_probabilities_are_coherent_and_sum_to_one():
    result = coherent_market_probabilities([
        {"bracket_id": "A", "lower": .10, "upper": .20},
        {"bracket_id": "B", "lower": .20, "upper": .40},
        {"bracket_id": "C", "lower": .30, "upper": .60},
    ])
    values = [item["probability"] for item in result["probabilities"]]
    assert result["intervals_relaxed"] is False
    assert sum(values) == pytest.approx(1)
    assert .10 <= values[0] <= .20
    assert .20 <= values[1] <= .40
    assert .30 <= values[2] <= .60


def test_infeasible_book_uses_fixed_outcome_blind_fallback():
    result = coherent_market_probabilities([
        {"bracket_id": "A", "lower": .7, "upper": .8},
        {"bracket_id": "B", "lower": .6, "upper": .7},
    ])
    assert result["intervals_relaxed"] is True
    assert result["sum"] == pytest.approx(1)
    assert result["method"] == "fixed_euclidean_bounded_simplex_v1"


def test_duplicate_bracket_is_rejected():
    with pytest.raises(ValueError, match="unique"):
        coherent_market_probabilities([
            {"bracket_id": "A", "lower": .1, "upper": .3},
            {"bracket_id": "A", "lower": .7, "upper": .9},
        ])


def test_complementary_yes_no_quotes_form_interval_without_labels():
    lower, upper, crossed = quote_interval(
        {"bid_price_cents": 20, "execution_price_cents": 24},
        {"bid_price_cents": 75, "execution_price_cents": 79},
    )
    assert (lower, upper, crossed) == pytest.approx((.21, .24, False))


def test_crossed_complement_quotes_collapse_by_fixed_rule():
    lower, upper, crossed = quote_interval(
        {"bid_price_cents": 55, "execution_price_cents": 60},
        {"bid_price_cents": 50, "execution_price_cents": 52},
    )
    assert crossed is True
    assert lower == upper == pytest.approx(.525)


def test_stale_holdout_seal_cannot_override_global_exposure(tmp_path):
    from v6_1 import date_ledger

    v5a = {"records": [
        {"climate_date":"2026-08-04","market_ticker":"T1","event_ticker":"E",
         "settlement_sources":[{"name":"NWS"}],"partition":"holdout"}
    ]}
    v5b = {"records": [
        {"climate_date":"2026-08-04","market_ticker":"T1","event_ticker":"E",
         "settlement_sources":[{"name":"NWS"}],"partition":"confirmation"}
    ]}
    result = {"trades": [
        {"climate_date":"2026-08-04","market_ticker":"T1"}
    ]}
    for relative, value in ((date_ledger.V5A_UNIVERSE, v5a),
                            (date_ledger.V5B_UNIVERSE, v5b),
                            (date_ledger.V5B_RESULT, result)):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    stale = tmp_path / "data/manifests/v5a_holdout_seal.json"
    stale.write_text(json.dumps({"sealed": True}), encoding="utf-8")
    ledger = date_ledger.build(tmp_path)
    assert ledger["confirmation_eligible_date_count"] == 0
    assert ledger["records"][0]["outcome_exposed"] is True
    assert ledger["records"][0]["final_eligibility"] == "PERMANENTLY_OUTCOME_EXPOSED"


def test_candidate_catalog_has_four_colonies_and_controls_cannot_promote():
    config = json.loads(Path("configs/v6_1_campaign.json").read_text(encoding="utf-8"))
    assert len(config["candidate_catalog"]) == 27
    assert len({item["id"] for item in config["candidate_catalog"]}) == 27
    assert {item["colony"] for item in config["candidate_catalog"]} == {
        "settlement_forecasting", "market_residuals", "execution_economics",
        "adversarial_validation"
    }
    assert all(not item["promotable"] for item in config["candidate_catalog"] if item["id"].startswith("A"))

