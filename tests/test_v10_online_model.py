"""Frozen-model equivalence and prospective input boundaries, without outcomes."""
import builtins
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from v10_online.model import (
    FrozenModelError, FrozenV10, V10_PATH, canonical_contracts,
    pressure_state, probability_vector, validated_forecasts,
)


ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-10-02"


@pytest.fixture(scope="module")
def model():
    return FrozenV10.load(ROOT)


def contracts(day=DAY):
    target = datetime.fromisoformat(day)
    event = "KXHIGHLAX-" + target.strftime("%y%b%d").upper()
    result = [{"market_ticker": event + "-T70", "strike_type": "less", "floor_strike": None, "cap_strike": 70}]
    for lower in (70, 72, 74, 76):
        result.append({"market_ticker": event + f"-B{lower + .5}", "strike_type": "between", "floor_strike": lower, "cap_strike": lower + 1})
    result.append({"market_ticker": event + "-T77", "strike_type": "greater", "floor_strike": 77, "cap_strike": None})
    return result


def forecasts(day=DAY):
    midnight = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    result = []
    for model, member, leads, units in (
        ("hrrr", None, (2, 8, 14, 20), "degF"),
        ("gefs", "avg", range(9, 31, 3), "degF"),
        ("gefs", "spr", range(9, 31, 3), "delta_degF"),
    ):
        nominal = midnight + timedelta(hours=6 if model == "hrrr" else 0)
        for lead in leads:
            result.append({
                "climate_date": day, "model": model, "field_id": "temperature_2m",
                "member_id": member, "nominal_issue_time_utc": nominal.isoformat(),
                "lead_hours": lead, "valid_time_utc": (nominal + timedelta(hours=lead)).isoformat(),
                "information_available_at_utc": (nominal + timedelta(hours=1)).isoformat(),
                "effective_information_available_at_utc": (nominal + timedelta(hours=6)).isoformat(),
                "as_of_validated": True, "contains_settlement_label": False,
                "is_missing": False, "units": units,
                "value": 1.5 if member == "spr" else 69.0 + lead / 5,
            })
    return result


def observation(station, time, pressure, day=DAY, delay=15):
    observed = datetime.fromisoformat(day + "T" + time + ":00+00:00")
    return {"station": station, "observed_at": observed.isoformat(),
            "available_at": (observed + timedelta(minutes=delay)).isoformat(),
            "pressure_hpa": pressure}


@pytest.mark.skipif(
    not (ROOT / "data/normalized/v5p_probability_features").is_dir(),
    reason="Optional historical replay needs the private local archive; it is not published to GitHub.",
)
def test_exact_frozen_static_chain_matches_all_60_saved_economic_vectors(model):
    """Saved pre-outcome vectors are a distinct, sealed implementation reference."""
    path = ROOT / "runs/v10/fixed-economic-replay-20261001/forecast-order-freeze.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in frozen.items() if key != "self_sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    assert digest == frozen["self_sha256"] and frozen["outcomes_read"] is False
    reference = {row["climate_date"]: row for row in frozen["methods"]["V10"]}
    v8_by_day = {row["climate_date"]: row for row in frozen["methods"]["V8"]}
    base_records = frozen["methods"]["V5B"]
    observations = pd.read_parquet(ROOT / "runs/v10/fixed-economic-replay-20261001/observations.parquet")
    compared = 0
    for record in base_records:
        day = record["climate_date"]
        rows = record["probabilities"]
        canonical = [{key: value for key, value in row.items() if key != "yes_probability"} for row in rows]
        folder = ROOT / "data/normalized/v5p_probability_features" / ("date=" + day)
        weather = pd.concat([pd.read_parquet(folder / "hrrr_points.parquet"), pd.read_parquet(folder / "gefs_summary_points.parquet")], ignore_index=True).to_dict("records")
        observed_rows = []
        for _, row in observations.loc[observations["climate_date"] == day].iterrows():
            observed_rows.append({"station": row["station"], "observed_at": row["observed_at"],
                                  "available_at": row["available_at"],
                                  "pressure_hpa": None if pd.isna(row["pressure_hpa"]) else float(row["pressure_hpa"])})
        prediction = model.predict(day, canonical, weather, observed_rows)
        assert prediction["base_probabilities"] == [row["yes_probability"] for row in rows]
        assert prediction["v8_probabilities"] == [row["yes_probability"] for row in v8_by_day[day]["probabilities"]]
        assert prediction["probabilities"] == [row["yes_probability"] for row in reference[day]["probabilities"]]
        assert prediction["pressure_state"] == reference[day]["pressure_state"]
        assert prediction["pressure_evidence"]["observed_pressure_gradient_hpa"] == reference[day]["pressure_evidence"]["observed_pressure_gradient_hpa"]
        compared += 1
    assert compared == 60


def test_inherited_modal_tie_rules_are_kept_separately(model):
    base = [.4, .4, .05, .05, .05, .05]
    tickers = ["Z", "A", "B", "C", "D", "E"]
    actual = model.from_base(base, tickers, "onshore")
    assert actual["base_modal_position_for_v8"] == 1
    assert actual["base_modal_position_for_v10"] == 0
    expected_v8 = [.25 * p + .75 * q for p, q in zip(base, model.confusion[1])]
    expected = [.25 * p + .75 * q for p, q in zip(expected_v8, model.pressure_tables[0, "onshore"])]
    assert actual["probabilities"] == pytest.approx(expected, abs=1e-15)


def test_missing_pressure_uses_neutral_table_and_inland_last_valid(model):
    rows = [observation("KLAX", "16:00", 1015), observation("KLAX", "17:00", None),
            observation("KDAG", "16:00", 1010), observation("KDAG", "17:00", None)]
    actual = pressure_state(DAY, rows)
    assert actual["pressure_and_flow"] == "neutral"
    assert actual["pressure_missing"] and actual["observed_pressure_gradient_hpa"] is None
    assert actual["kdag_selected_observed_at"] == DAY + "T16:00:00+00:00"
    predicted = model.predict(DAY, contracts(), forecasts(), rows)
    assert predicted["probabilities"] == model.from_base(predicted["base_probabilities"], predicted["tickers"], "neutral")["probabilities"]
    rows.pop(1)
    assert pressure_state(DAY, rows)["pressure_and_flow"] == "onshore"


@pytest.mark.parametrize("gradient,state", [(-2.0, "offshore"), (-1.999, "neutral"), (1.999, "neutral"), (2.0, "onshore")])
def test_pressure_thresholds_are_inclusive_and_late_reports_excluded(gradient, state):
    rows = [observation("KLAX", "17:45", 1010 + gradient), observation("KDAG", "17:45", 1010),
            observation("KLAX", "17:46", 1090), observation("KDAG", "18:00", 990),
            observation("KLAX", "11:45", 1100)]
    actual = pressure_state(DAY, rows)
    assert actual["pressure_and_flow"] == state
    assert actual["observed_pressure_gradient_hpa"] == pytest.approx(gradient)
    assert actual["klax_asof_report_count"] == actual["kdag_asof_report_count"] == 1


@pytest.mark.parametrize("mutation", ["duplicate", "delay", "nonfinite", "station", "naive", "label"])
def test_rejects_ambiguous_or_malformed_pressure(mutation):
    rows = [observation("KLAX", "17:45", 1010)]
    if mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "delay":
        rows = [observation("KLAX", "17:45", 1010, delay=0)]
    elif mutation == "nonfinite":
        rows[0]["pressure_hpa"] = float("nan")
    elif mutation == "station":
        rows[0]["station"] = "KSMO"
    elif mutation == "naive":
        rows[0]["observed_at"] = DAY + "T17:45:00"
    else:
        rows[0]["outcome"] = 1
    with pytest.raises(FrozenModelError):
        pressure_state(DAY, rows)


@pytest.mark.parametrize("mutation", ["short", "duplicate", "cycle", "future", "lag", "units", "spread", "missing", "station", "label", "valid", "member"])
def test_rejects_incomplete_or_changed_forecast_surface(mutation):
    rows = forecasts()
    if mutation == "short":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "cycle":
        rows[0]["nominal_issue_time_utc"] = DAY + "T12:00:00+00:00"
    elif mutation == "future":
        rows[0]["information_available_at_utc"] = DAY + "T18:00:01+00:00"
    elif mutation == "lag":
        rows[0]["effective_information_available_at_utc"] = DAY + "T07:00:00+00:00"
    elif mutation == "units":
        rows[0]["units"] = "K"
    elif mutation == "spread":
        rows[-1]["value"] = -1
    elif mutation == "missing":
        rows[0]["is_missing"] = True
    elif mutation == "station":
        rows[0]["point_id"] = "KDAG"
    elif mutation == "label":
        rows[0]["nested"] = {"reported_high_f": 75}
    elif mutation == "valid":
        rows[0]["valid_time_utc"] = DAY + "T10:00:00+00:00"
    else:
        rows[0]["member_id"] = "avg"
    with pytest.raises(FrozenModelError):
        validated_forecasts(DAY, rows)


@pytest.mark.parametrize("mutation", ["short", "duplicate", "order", "gap", "width", "ticker", "tail", "label"])
def test_rejects_noncanonical_or_changed_bracket_shape(mutation):
    rows = contracts()
    if mutation == "short":
        rows.pop()
    elif mutation == "duplicate":
        rows[2]["market_ticker"] = rows[1]["market_ticker"]
    elif mutation == "order":
        rows[1], rows[2] = rows[2], rows[1]
    elif mutation == "gap":
        rows[0]["cap_strike"] = 69
    elif mutation == "width":
        rows[1]["cap_strike"] = 72
    elif mutation == "ticker":
        rows[1]["market_ticker"] = "KXHIGHCHI-26OCT02-B70.5"
    elif mutation == "tail":
        rows[-1]["strike_type"] = "between"
    else:
        rows[0]["settlement_label"] = 1
    with pytest.raises(FrozenModelError):
        canonical_contracts(DAY, rows)


@pytest.mark.parametrize("values", [[.5] * 6, [1, 0, 0], [float("nan")] + [0] * 5, [True, 0, 0, 0, 0, 0], [-.1, .1, .2, .2, .2, .4]])
def test_invalid_probability_vectors_fail_closed(values):
    with pytest.raises(FrozenModelError):
        probability_vector(values)


def test_predict_is_readonly_io_free_and_json_serializable(model, monkeypatch):
    rows, weather = contracts(), forecasts()
    original = copy.deepcopy((rows, weather))
    def deny(*args, **kwargs):
        raise AssertionError("prediction attempted file IO")
    monkeypatch.setattr(builtins, "open", deny)
    result = model.predict(DAY, rows, weather)
    assert (rows, weather) == original
    assert result["target_updates_used"] is False
    assert result["outcomes_read"] is False
    assert result["live_orders_placed"] == result["paper_orders_placed"] == 0
    assert len(result["prediction_rows"]) == 6
    json.dumps(result, allow_nan=False)


def test_loader_rejects_even_resealed_fitted_artifact_tamper(tmp_path):
    path = tmp_path / V10_PATH
    path.parent.mkdir(parents=True)
    original = json.loads((ROOT / V10_PATH).read_text(encoding="utf-8"))
    original["fitted_parameters"]["pressure_conditioned_tables"]["1"]["onshore"]["hierarchical_posterior"][0] += .001
    body = {key: value for key, value in original.items() if key != "self_sha256"}
    original["self_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    path.write_text(json.dumps(original), encoding="utf-8")
    with pytest.raises(FrozenModelError, match="frozen model binding changed"):
        FrozenV10.load(tmp_path)
