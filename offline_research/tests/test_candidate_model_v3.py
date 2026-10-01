from datetime import date, timedelta

import pytest

from klax_lab.candidate_model_v3 import CalibrationCase, fit_candidate_model_v3
from klax_lab.domain import ContractBounds
from klax_lab.research_plan_v3 import make_plan_v3


BOUNDS = (
    ContractBounds(None, 67), ContractBounds(68, 70), ContractBounds(71, 73),
    ContractBounds(74, 76), ContractBounds(77, 79), ContractBounds(80, None),
)


def row(day: date, signal: float, *, members: bool = False) -> dict:
    forecasts = []
    if members:
        for member in range(12):
            for lead in (9, 15, 21, 27):
                forecasts.append({
                    "model": "gefs", "field_id": "temperature_2m",
                    "member_id": f"p{member:02d}", "lead_hours": lead,
                    "value": 70 + signal + member * .25 + lead * .03,
                    "is_missing": False, "as_of_validated": True,
                })
    else:
        for model, base in (("gfs", 70), ("nbm", 71), ("hrrr", 72)):
            for lead in (8, 14, 20):
                forecasts.append({
                    "model": model, "field_id": "temperature_2m", "member_id": None,
                    "lead_hours": lead, "value": base + signal + lead * .05,
                    "is_missing": False, "as_of_validated": True,
                })
        for lead in (9, 15, 21, 27):
            forecasts.extend((
                {"model": "gefs", "field_id": "temperature_2m", "member_id": "avg",
                 "lead_hours": lead, "value": 71 + signal + lead * .04,
                 "is_missing": False, "as_of_validated": True},
                {"model": "gefs", "field_id": "temperature_2m", "member_id": "spr",
                 "lead_hours": lead, "value": 2 + (day.day % 3) * .1,
                 "is_missing": False, "as_of_validated": True},
            ))
    return {
        "climate_date": day.isoformat(), "decision_time_utc": "15:00",
        "as_of_join_validated": True, "contains_settlement_label": False,
        "forecasts": forecasts, "observations": [], "contracts": [],
    }


def training(members: bool = False):
    start = date(2024, 1, 1)
    rows, targets = [], []
    for index in range(120):
        signal = -3 + 6 * index / 119
        rows.append(row(start + timedelta(days=index), signal, members=members))
        targets.append(73 + signal * 1.2 + (index % 5 - 2) * .35)
    return rows, targets


def calibration(members: bool = False, count: int = 30):
    start = date(2025, 1, 5)
    cases = []
    for index in range(count):
        signal = -2 + 4 * index / max(1, count - 1)
        winner = index % 6
        market = [0.10] * 6
        market[winner] = .30
        cases.append(CalibrationCase(
            row(start + timedelta(days=index), signal, members=members),
            BOUNDS, winner, tuple(market),
        ))
    return cases


def test_full_candidate_fit_is_deterministic_and_conserves_probability() -> None:
    rows, targets = training()
    plan = make_plan_v3(
        colony="ensemble_probability", stage="economic_simulation",
        data_bundle_version="fixture", data_bundle_sha256="a" * 64,
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
        feature_set="temperature_only", regime_model="pooled",
        probability_family="gaussian_mixture", calibration_operator="beta_bracket",
        market_residual_model="regularized_logit", abstention_operator="split_conformal",
    )
    first = fit_candidate_model_v3(
        plan, weather_training_rows=rows, weather_training_targets_f=targets,
        calibration_cases=calibration(),
    )
    second = fit_candidate_model_v3(
        plan, weather_training_rows=rows, weather_training_targets_f=targets,
        calibration_cases=calibration(),
    )
    assert first.identity == second.identity
    predicted = first.predict(
        row(date(2025, 2, 4), .5), BOUNDS,
        market_probabilities=(.12, .14, .20, .22, .18, .14),
    )
    assert sum(predicted.probabilities) == pytest.approx(1.0, abs=1e-12)
    assert len(predicted.conformal_prediction_set) <= 6
    assert predicted.regime == "pooled"


def test_ordered_logistic_uses_prefix_labels_only() -> None:
    rows, targets = training()
    plan = make_plan_v3(
        colony="ensemble_probability", stage="probability_calibration",
        data_bundle_version="fixture", data_bundle_sha256="b" * 64,
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
        feature_set="temperature_only", regime_model="pooled",
        probability_family="ordered_logistic", calibration_operator="none",
        market_residual_model="none", abstention_operator="fixed_uncertainty_buffer",
    )
    fitted = fit_candidate_model_v3(
        plan, weather_training_rows=rows, weather_training_targets_f=targets,
        calibration_cases=calibration(count=30),
    )
    predicted = fitted.predict(row(date(2025, 2, 4), 0), BOUNDS)
    assert sum(predicted.probabilities) == pytest.approx(1.0, abs=1e-12)
    assert fitted.ordered is not None
    assert fitted.conformal is None


def test_empirical_ensemble_requires_and_uses_actual_member_rows() -> None:
    rows, targets = training(members=True)
    plan = make_plan_v3(
        colony="ensemble_probability", stage="probability_calibration",
        data_bundle_version="fixture", data_bundle_sha256="c" * 64,
        forecast_source_set="gefs", feature_set="temperature_only", regime_model="pooled",
        probability_family="empirical_ensemble", calibration_operator="none",
        market_residual_model="none", abstention_operator="fixed_uncertainty_buffer",
    )
    fitted = fit_candidate_model_v3(
        plan, weather_training_rows=rows, weather_training_targets_f=targets,
        calibration_cases=calibration(members=True),
    )
    predicted = fitted.predict(row(date(2025, 2, 4), .2, members=True), BOUNDS)
    assert sum(predicted.probabilities) == pytest.approx(1.0, abs=1e-12)
    with pytest.raises(ValueError, match="lacks archived GEFS members"):
        fitted.predict(row(date(2025, 2, 4), .2, members=False), BOUNDS)


def test_label_leakage_in_training_feature_is_rejected() -> None:
    rows, targets = training()
    rows[0]["reported_high_f"] = 72
    plan = make_plan_v3(
        colony="adversarial_alternatives", stage="forecast_skill",
        data_bundle_version="fixture", data_bundle_sha256="d" * 64,
        forecast_source_set="gfs_nbm_hrrr_gefs_summary",
        feature_set="temperature_only", regime_model="pooled",
        probability_family="gaussian_mixture", calibration_operator="none",
        market_residual_model="none", abstention_operator="fixed_uncertainty_buffer",
    )
    with pytest.raises(ValueError, match="labels"):
        fit_candidate_model_v3(
            plan, weather_training_rows=rows, weather_training_targets_f=targets,
            calibration_cases=calibration(),
        )
