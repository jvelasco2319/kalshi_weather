"""Deterministic V6 tests built solely from synthetic development observations."""
from copy import deepcopy
from datetime import date, timedelta
import math
import json

import pytest

from v6 import research_specs as specs
from v6.evaluation import forecast_sequence, evaluate_candidate, reference_replay, validate_spec
from v5b.campaign import digest


def context():
    dates = [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(64)]
    features, labels, records, probabilities, bydate = [], {}, [], {}, {}
    for i, day in enumerate(dates):
        row = {"climate_date": day, "decision_at": day + "T18:00:00Z",
            "hrrr_temperature_f_max": 72 + 3 * math.sin(i / 4),
            "gefs_mean_temperature_f_max": 73 + 2 * math.cos(i / 5),
            "hrrr_cloud_percent_mean": 50 + 30 * math.sin(i / 6),
            "hrrr_wind_u_mean_m_s": math.sin(i / 5),
            "hrrr_wind_v_mean_m_s": math.cos(i / 3),
            "gefs_spread_delta_f_at_mean_max": 1 + (i % 4) / 2,
            "absolute_sampled_max_disagreement_f": 1 + (i % 7) / 3}
        features.append(row)
        labels[day] = round((row["hrrr_temperature_f_max"] + row["gefs_mean_temperature_f_max"]) / 2 + (i % 3) - 1)
        records.append({"climate_date": day, "reported_high_f": labels[day],
            "issued_at": (date.fromisoformat(day) + timedelta(days=1)).isoformat() + "T12:00:00Z"})
        bydate[day] = []
        for j, probability in enumerate((.2, .6, .2)):
            ticker = f"{day}-{j}"
            probabilities[day, ticker] = probability
            for side in ("YES", "NO"):
                bydate[day].append({"climate_date": day, "partition": "development", "market_ticker": ticker,
                    "contract_side": side, "strike_type": ("less", "between", "greater")[j],
                    "floor_strike": (None, 70, 79)[j], "cap_strike": (70, 79, None)[j],
                    "execution_evidence_grade": "B_PLUS", "assumed_fill": False,
                    "execution_price_cents": 30, "bid_price_cents": 29})
    return {"dates": dates, "weather_features": features, "label_records": records,
        "labels": labels, "rows_by_date": bydate, "probabilities": probabilities,
        "raw_probabilities": dict(probabilities), "partition": "development",
        "reference_parameters": {}, "reference_candidate_id": "synthetic-reference"}


@pytest.mark.parametrize("method", specs.METHODS)
def test_prior_only_models_preserve_common_cohort_and_ignore_future_labels(method):
    ctx = context()
    original = forecast_sequence(ctx["weather_features"], ctx["label_records"], method)
    changed = deepcopy(ctx["label_records"])
    for label in changed[40:]:
        label["reported_high_f"] += 30
    altered = forecast_sequence(ctx["weather_features"], changed, method)
    assert len(original) == 44 and [f["climate_date"] for f in original] == ctx["dates"][20:]
    # Current-day labels cannot affect that day's forecast either.
    assert original[:21] == altered[:21]
    for f in original:
        assert all(d < f["climate_date"] for d in f["training_dates"] + f["residual_dates"])
        assert f["training_count"] >= 20 and f["residual_count"] >= 10
        assert .5 <= f["scale_f"] <= 10
        assert f["interval_95_f"][0] <= f["interval_80_f"][0] <= f["interval_80_f"][1] <= f["interval_95_f"][1]


def test_stage_semantics_and_behavioral_fingerprint():
    ctx = context()
    base = {"method_id": specs.METHODS[0], "policy_id": "no_dollar_all", "stage": "precursor"}
    precursor = evaluate_candidate(ctx, base)
    economic = evaluate_candidate(ctx, {**base, "stage": "economic"})
    synthesis = evaluate_candidate(ctx, {**base, "stage": "synthesis"})
    assert precursor["promotion_eligible"] is False and economic["promotion_eligible"] is True
    assert precursor["behavioral_fingerprint"] == economic["behavioral_fingerprint"]
    assert precursor["multiclass_brier"] == economic["multiclass_brier"]
    assert synthesis["precursor"]["synthesis_weight_new"] == .5
    assert synthesis["baseline_multiclass_brier"] == economic["baseline_multiclass_brier"]
    assert synthesis["multiclass_brier"] != economic["multiclass_brier"]
    assert economic["common_scoring_dates"] == ctx["dates"][20:]
    assert economic["holdout_labels_opened"] is False and not economic["actual_orders_placed"]
    assert len(economic["behavioral_ledger"]) == 44
    assert [r["climate_date"] for r in economic["behavioral_ledger"]] == economic["common_scoring_dates"]
    assert economic["behavioral_fingerprint"] == digest(economic["behavioral_ledger"])
    json.dumps(synthesis, allow_nan=False)


def test_catalog_has_28_unique_specs_and_only_valid_precursors():
    reachable = specs.reachable_specs()
    assert len(reachable) == len({specs.fingerprint(s) for s in reachable}) == 28
    assert len(specs.seeds()) == 4
    for h in specs.seeds():
        assert specs.validate_hypothesis(h) == h
    with pytest.raises(ValueError):
        validate_spec({"method_id": specs.METHODS[0], "policy_id": "no_dollar_paid", "stage": "precursor"})
    with pytest.raises(ValueError):
        validate_spec({**reachable[0], "decision_time": "17:00"})


def test_causal_stage_dependencies_and_explicit_reference_lineage():
    populations = {h["colony"]: [h] for h in specs.seeds()}
    assert specs.propose(populations, critiques=[]) == []
    proposed = specs.propose(populations, critiques=[{"finding": "synthetic reviewed precursor"}], max_per_colony=3)
    assert len(proposed) == 12 and {h["parameters"]["stage"] for h in proposed} == {"economic"}
    for h in proposed:
        populations[h["colony"]].append(h)
    blended = specs.propose(populations, critiques=[{"finding": "synthetic reviewed policy"}], max_per_colony=3)
    assert len(blended) == 12 and all(specs.REFERENCE_ID in h["parent_ids"] for h in blended)
    assert all(specs.validate_hypothesis(h) == h for h in blended)


def test_delayed_labels_fail_common_date_readiness():
    ctx = context()
    ctx["label_records"][0]["issued_at"] = "2027-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="warmup"):
        forecast_sequence(ctx["weather_features"], ctx["label_records"], specs.METHODS[0])


def test_reference_replay_is_a_control_and_does_not_mutate_context():
    ctx = context()
    snapshot = deepcopy(ctx)
    replay = reference_replay(ctx)
    assert replay["counts_as_new_candidate"] is False
    assert ctx == snapshot
    assert replay["original_64_date_result"]["selected_days"] == 64
    assert replay["common_44_date_result"]["selected_days"] == 44


def test_missing_uncertainty_proxy_is_not_invented():
    ctx = context()
    del ctx["weather_features"][30]["gefs_spread_delta_f_at_mean_max"]
    with pytest.raises(KeyError):
        forecast_sequence(ctx["weather_features"], ctx["label_records"], specs.METHODS[0])


def test_holdout_context_rejected():
    ctx = context()
    ctx["partition"] = "holdout"
    with pytest.raises(ValueError, match="development"):
        evaluate_candidate(ctx, specs.reachable_specs()[0])


def test_behavioral_ledger_explicitly_records_abstentions():
    ctx = context()
    for rows in ctx["rows_by_date"].values():
        for row in rows:
            row["execution_evidence_grade"] = "UNAVAILABLE"
            row["execution_price_cents"] = None
    result = evaluate_candidate(ctx, specs.reachable_specs()[0])
    assert result["selected_days"] == 0
    assert result["behavioral_ledger"] == [{"climate_date": d, "abstention": True} for d in ctx["dates"][20:]]
    assert result["behavioral_fingerprint"] == digest(result["behavioral_ledger"])


def test_behavioral_ledger_trade_rows_match_scored_trades():
    result = evaluate_candidate(context(), specs.reachable_specs()[0])
    trades = {t["climate_date"]: t for t in result["trades"]}
    for row in result["behavioral_ledger"]:
        if row["abstention"]:
            assert row["climate_date"] not in trades
            continue
        assert row["quantity"] == 1
        assert set(row) == {"climate_date", "market_ticker", "contract_side", "quantity", "entry_price_cents", "fee_dollars", "execution_evidence_grade", "abstention"}
        trade = trades[row["climate_date"]]
        for key in ("market_ticker", "contract_side", "entry_price_cents", "fee_dollars", "execution_evidence_grade"):
            assert row[key] == trade[key]


def test_duplicate_day_trades_are_rejected(monkeypatch):
    import v6.evaluation as evaluation
    original = evaluation.score_market
    def duplicate_trade(ctx, policy):
        result = original(ctx, policy)
        if result["trades"]:
            result["trades"].append(dict(result["trades"][0]))
        return result
    monkeypatch.setattr(evaluation, "score_market", duplicate_trade)
    with pytest.raises(ValueError, match="at most one trade"):
        evaluation.evaluate_candidate(context(), specs.reachable_specs()[0])
