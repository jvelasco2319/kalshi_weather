"""Synthetic tests only; no historical or protected labels are consumed."""
import copy
from datetime import date, timedelta
import json

import pytest

from v5b.evaluation import evaluate_candidate, validate_spec


def fixture(days=35):
    dates = [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(days)]
    rows, probabilities, labels = {}, {}, {}
    for d in dates:
        rows[d] = []
        labels[d] = 65
        for j, probability in enumerate((.7, .2, .1)):
            ticker = f"{d}-{j}"
            probabilities[d, ticker] = probability
            for side in ("YES", "NO"):
                rows[d].append({"climate_date": d, "partition": "development",
                    "market_ticker": ticker, "contract_side": side,
                    "strike_type": ("less", "between", "greater")[j],
                    "floor_strike": (None, 70, 79)[j], "cap_strike": (70, 79, None)[j],
                    "execution_evidence_grade": "A", "assumed_fill": False,
                    "execution_price_cents": 20 if side == "YES" else 80,
                    "bid_price_cents": 19 if side == "YES" else 79})
    return {"dates": dates, "rows_by_date": rows, "probabilities": probabilities,
            "raw_probabilities": dict(probabilities), "labels": labels, "partition": "development"}


def test_single_daily_trade_exact_fees_stress_and_evidence():
    result = evaluate_candidate(fixture(), {"allowed_sides": ["YES"]})
    assert result["selected_days"] == 35
    assert len({t["climate_date"] for t in result["trades"]}) == 35
    assert result["trades"][0]["fee_dollars"] == .02
    assert result["trades"][0]["entry_outlay_dollars"] == pytest.approx(.22)
    assert result["positive_fold_count"] == 5
    assert result["execution_grade_counts"] == {"A": 35, "B_PLUS": 0, "B": 0}
    assert result["adverse_stress"]["3"]["aggregate_realized_net_return"] < result["adverse_stress"]["1"]["aggregate_realized_net_return"] < result["aggregate_realized_net_return"]
    assert result["best_day_removed_return"] == pytest.approx(result["aggregate_realized_net_return"])
    assert all(t["verified_fill"] is False for t in result["trades"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("bad", [{"decision_time": "17:00"}, {"probability_power": float("nan")},
    {"allowed_grades": ["UNAVAILABLE"]}, {"minimum_price_cents": 1.5},
    {"minimum_price_cents": 90, "maximum_price_cents": 10}, {"calibration": "full_sample"}])
def test_invalid_spec_rejected(bad):
    with pytest.raises(ValueError):
        validate_spec(bad)


def test_grade_lists_normalized_and_defaults_canonical():
    assert validate_spec({"allowed_grades": ["B", "A", "B_PLUS", "A"]}) == validate_spec({})


def test_holdout_context_and_extra_labels_denied():
    context = fixture()
    context["partition"] = "holdout"
    with pytest.raises(ValueError):
        evaluate_candidate(context, {})
    context["partition"] = "development"
    context["labels"]["2030-01-01"] = 80
    with pytest.raises(ValueError):
        evaluate_candidate(context, {})


def test_future_labels_cannot_change_earlier_calibrated_selections():
    original = fixture()
    altered = copy.deepcopy(original)
    for d in altered["dates"][20:]:
        altered["labels"][d] = 90
    spec = {"calibration": "expanding_rank_frequency", "allowed_sides": ["YES"], "calibration_min_days": 10}
    before, after = evaluate_candidate(original, spec), evaluate_candidate(altered, spec)
    cutoff = original["dates"][20]
    assert [t for t in before["trades"] if t["climate_date"] < cutoff] == [t for t in after["trades"] if t["climate_date"] < cutoff]
    assert len([a for a in before["abstentions"] if a["reason"] == "calibration_warmup"]) == 10
    assert before["calibration"]["max_history_dates"] == 34


def test_tail_entropy_and_neighbor_research_change_behavior():
    context = fixture()
    interior = evaluate_candidate(context, {"tail_policy": "interior", "allowed_sides": ["YES"]})
    assert interior["selected_days"] == 0
    entropy = evaluate_candidate(context, {"maximum_entropy": .1})
    assert entropy["selected_days"] == 0
    assert all(a["reason"] == "entropy" for a in entropy["abstentions"])
    smooth = evaluate_candidate(context, {"neighbor_smoothing": 1, "allowed_sides": ["YES"]})
    assert smooth["trades"][0]["strike_type"] == "between"
    assert smooth["aggregate_realized_net_return"] == -1


def test_raw_source_and_common_frozen_reference():
    context = fixture()
    for d in context["dates"]:
        for j, value in enumerate((.1, .8, .1)):
            context["raw_probabilities"][d, f"{d}-{j}"] = value
    raw = evaluate_candidate(context, {"probability_source": "raw", "allowed_sides": ["YES"]})
    calibrated = evaluate_candidate(context, {"allowed_sides": ["YES"]})
    assert raw["trades"][0]["strike_type"] == "between"
    assert raw["baseline_multiclass_brier"] == calibrated["baseline_multiclass_brier"]
    assert raw["multiclass_brier"] > calibrated["multiclass_brier"]


def test_nonexclusive_settlement_rejected():
    context = fixture()
    context["rows_by_date"][context["dates"][0]][2]["floor_strike"] = 60
    with pytest.raises(ValueError, match="settlement"):
        evaluate_candidate(context, {})
