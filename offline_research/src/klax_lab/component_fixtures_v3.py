"""Publish deterministic engineering evidence for V3 numerical components.

These fixtures prove that the registered operators execute and preserve their
local invariants.  They are deliberately synthetic and cannot authorize a
campaign, support a performance claim, or substitute for real-data validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from math import cos, sin
from pathlib import Path
from typing import Any

from .domain import ContractBounds
from .evaluation import FeeScenario
from .forecast_models_v3 import fit_distribution_model
from .market_policy_v3 import (
    SelectiveControls,
    latest_completed_quote,
    quote_from_normalized_candle,
    screen_minute_purchase,
)
from .ordinal_v3 import fit_ordered_logistic_brackets
from .probability_v3 import (
    GaussianComponent,
    GaussianMixtureDistribution,
    QuantileSettlementDistribution,
    calibrate_bracket_vector,
    fit_beta_calibrator,
    fit_bracket_isotonic,
    fit_conformal_abstention,
    fit_market_residual_logit,
    probability_vector,
)
from .provenance import canonical_hash, inventory, write_json
from .regimes_v3 import WEATHER_REGIMES, fit_six_regime_classifier


MODEL_MANIFEST = Path("data/manifests/v3_model_fixtures.json")
MARKET_MANIFEST = Path("data/manifests/v3_market_policy_fixtures.json")


def _rounded(values: Any) -> Any:
    if isinstance(values, float):
        return round(values, 12)
    if isinstance(values, tuple):
        return [_rounded(value) for value in values]
    if isinstance(values, list):
        return [_rounded(value) for value in values]
    if isinstance(values, dict):
        return {key: _rounded(value) for key, value in values.items()}
    return values


def _finish(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "evidence_sha256": canonical_hash(body)}


def build_model_fixture_evidence(root: Path) -> dict[str, Any]:
    """Execute every registered numerical family on deterministic fixtures."""
    root = root.resolve()
    bounds = (
        ContractBounds(None, 73), ContractBounds(74, 76),
        ContractBounds(77, 79), ContractBounds(80, None),
    )
    mixture = GaussianMixtureDistribution((
        GaussianComponent(75.0, 1.4, .45),
        GaussianComponent(79.0, 2.1, .55),
    ))
    mixture_vector = mixture.contract_vector(bounds)
    quantile = QuantileSettlementDistribution(
        (0.0, .10, .25, .50, .75, .90, 1.0),
        (66.0, 71.0, 74.0, 77.0, 80.0, 84.0, 92.0),
    )
    quantile_vector = probability_vector(quantile.probability(item) for item in bounds)

    calibration_rows: list[list[float]] = []
    calibration_labels: list[int] = []
    for index in range(60):
        label = index % 4
        row = [.08, .08, .08, .08]
        row[label] = .76
        calibration_rows.append(row)
        calibration_labels.append(label)
    isotonic = fit_bracket_isotonic(calibration_rows, calibration_labels)
    calibrated = calibrate_bracket_vector((.65, .15, .10, .10), isotonic)
    binary_scores = [.05 + .90 * index / 59 for index in range(60)]
    binary_labels = [int(value > .52) for value in binary_scores]
    beta = fit_beta_calibrator(binary_scores, binary_labels)
    conformal = fit_conformal_abstention(
        [[value, 1.0 - value] for value in binary_scores],
        [0 if value > .52 else 1 for value in binary_scores],
    )
    market = [.35 if index % 2 == 0 else .65 for index in range(60)]
    model = [min(.95, max(.05, value + (.20 if index % 3 else -.20)))
             for index, value in enumerate(market)]
    residual = fit_market_residual_logit(
        model, market, [int(left > right) for left, right in zip(model, market)])

    ordered_features: list[list[float]] = []
    ordered_labels: list[int] = []
    for index in range(180):
        signal = -3.0 + 6.0 * index / 179
        secondary = sin(index * .37)
        latent = signal + .15 * secondary
        ordered_features.append([signal, secondary])
        ordered_labels.append(sum(latent > threshold for threshold in (-2, -1, 0, 1, 2)))
    ordered = fit_ordered_logistic_brackets(
        ordered_features, ordered_labels,
        feature_names=("forecast_temperature", "forecast_disagreement"),
        bracket_names=("low", "cool", "mild", "warm", "hot", "very_hot"),
        maximum_iterations=3000, deterministic_seed=17,
    )
    ordered_probabilities = ordered.predict_proba(((-2.5, 0.0), (0.0, 0.0), (2.5, 0.0)))

    centers = ((-3, 0), (-1.5, 2.6), (1.5, 2.6), (3, 0), (1.5, -2.6), (-1.5, -2.6))
    regime_features: list[list[float]] = []
    regime_labels: list[str] = []
    for regime, center in zip(WEATHER_REGIMES, centers):
        for index in range(20):
            regime_features.append([
                center[0] + .18 * sin(index * .91),
                center[1] + .18 * cos(index * .91),
            ])
            regime_labels.append(regime)
    regimes = fit_six_regime_classifier(
        regime_features, regime_labels,
        feature_names=("marine_signal", "synoptic_signal"),
        minimum_confidence=.45, maximum_iterations=3000, deterministic_seed=23,
    )
    assignments = regimes.classify(regime_features)
    accuracy = sum(item.regime == expected for item, expected in zip(assignments, regime_labels)) / len(assignments)

    distribution_features, distribution_targets, distribution_regimes = [], [], []
    for index in range(120):
        signal = -2 + 4 * index / 119
        secondary = sin(index * .31)
        distribution_features.append([signal, secondary])
        distribution_targets.append(
            75 + 3.5 * signal + .4 * secondary + (index % 5 - 2) * .35)
        distribution_regimes.append(
            "ordinary_sea_breeze" if index % 2 else "persistent_marine_layer")
    fitted_distributions = {
        family: fit_distribution_model(
            distribution_features, distribution_targets, family=family,
            regimes=distribution_regimes, minimum_regime_training_days=30,
        )
        for family in ("gaussian_blend", "quantile_brackets", "gaussian_mixture")
    }
    distribution_bounds = (
        ContractBounds(None, 72), ContractBounds(73, 75),
        ContractBounds(76, 78), ContractBounds(79, None),
    )
    distribution_vectors = {
        family: model.contract_probabilities(
            distribution_features[0], distribution_bounds,
            regime="ordinary_sea_breeze",
        )[0]
        for family, model in fitted_distributions.items()
    }

    invariants = {
        "gaussian_mixture_probability_conservation": abs(sum(mixture_vector) - 1.0) <= 1e-12,
        "quantile_probability_conservation": abs(sum(quantile_vector) - 1.0) <= 1e-12,
        "calibrated_probability_conservation": abs(sum(calibrated) - 1.0) <= 1e-12,
        "beta_is_order_preserving_on_fixture": beta.predict(.8) > beta.predict(.2),
        "conformal_returns_boolean_abstention": isinstance(conformal.should_abstain((.5, .5)), bool),
        "market_residual_uses_incremental_signal": residual.predict(.75, .55) > residual.predict(.35, .55),
        "ordered_probabilities_conserve_mass": all(abs(sum(row) - 1.0) <= 1e-12 for row in ordered_probabilities),
        "six_regime_probabilities_conserve_mass": all(abs(sum(item.probabilities) - 1.0) <= 1e-12 for item in assignments),
        "six_regime_fixture_accuracy_above_95_percent": accuracy > .95,
        "pooled_regime_fallback_registered": regimes.classify(((0.0, 0.0),), minimum_confidence=.80)[0].use_pooled_fallback,
        "trained_distribution_families_conserve_mass": all(
            abs(sum(vector) - 1.0) <= 1e-12 for vector in distribution_vectors.values()),
    }
    if not all(invariants.values()):
        raise ValueError("A V3 numerical fixture invariant failed")
    code = inventory(root, [
        root / "src/klax_lab/probability_v3.py",
        root / "src/klax_lab/ordinal_v3.py",
        root / "src/klax_lab/regimes_v3.py",
        root / "src/klax_lab/features_v3.py",
        root / "src/klax_lab/forecast_models_v3.py",
        root / "src/klax_lab/candidate_model_v3.py",
    ])
    return _finish({
        "schema_version": 1,
        "status": "ENGINEERING_FIXTURES_PASS",
        "synthetic_engineering_fixtures": True,
        "campaign_or_profit_evidence": False,
        "protected_final_read": False,
        "code": code,
        "fixture_identity": canonical_hash({
            "calibration_rows": calibration_rows,
            "calibration_labels": calibration_labels,
            "ordered_features": ordered_features,
            "ordered_labels": ordered_labels,
            "regime_features": regime_features,
            "regime_labels": regime_labels,
            "distribution_features": distribution_features,
            "distribution_targets": distribution_targets,
            "distribution_regimes": distribution_regimes,
        }),
        "invariants": invariants,
        "outputs": _rounded({
            "mixture_vector": mixture_vector,
            "quantile_vector": quantile_vector,
            "calibrated_vector": calibrated,
            "beta_low_high": (beta.predict(.2), beta.predict(.8)),
            "conformal_threshold": conformal.nonconformity_threshold,
            "market_residual_coefficient": residual.residual_logit_coefficient,
            "ordered_predictions": ordered.predict_bracket(((-2.5, 0.0), (0.0, 0.0), (2.5, 0.0))),
            "regime_accuracy": accuracy,
            "trained_distribution_vectors": distribution_vectors,
        }),
        "limitations": [
            "Synthetic fixtures validate execution and invariants only.",
            "They do not validate historical forecast skill, market edge, or profitability.",
            "They cannot issue a V3 campaign ticket or authorize protected-final access.",
        ],
    })


def _candle(stamp: int, **overrides: Any) -> dict[str, Any]:
    row = {
        "ticker": "KXHIGHLAX-FIXTURE", "period_minutes": 1,
        "end_period_ts": stamp, "yes_bid_close": "0.40", "yes_ask_close": "0.42",
        "volume_contracts": "25", "evidence_grade": "B_aggregated_quote",
        "historical_depth_available": False, "hypothetical_fill_supported": False,
        "source_sha256": "a" * 64,
        "source_endpoint_type": "historical_market_candlesticks_1m",
    }
    row.update(overrides)
    return row


def build_market_fixture_evidence(root: Path) -> dict[str, Any]:
    """Exercise as-of minute selection and every selective market control."""
    root = root.resolve()
    decision = datetime(2025, 2, 1, 15, tzinfo=timezone.utc)
    stamp = int(decision.timestamp())
    selected = latest_completed_quote(
        [_candle(stamp - 60), _candle(stamp + 60, yes_ask_close="0.10")],
        "KXHIGHLAX-FIXTURE", decision,
    )
    fees = FeeScenario("fixture", "0.07", "synthetic fee fixture")

    def screen(row: dict[str, Any], *, controls: SelectiveControls | None = None,
               side: str = "YES"):
        return screen_minute_purchase(
            decision_id="fixture", information_cutoff=decision, decision_at=decision,
            yes_probability="0.75", side=side,
            quote=quote_from_normalized_candle(row),
            controls=controls or SelectiveControls(uncertainty_buffer="0.05"),
            fees=fees, dataset_version="synthetic-fixture", model_version="synthetic-fixture",
        )

    accepted = screen(_candle(stamp))
    decisions = {
        "accepted": accepted.status,
        "low_volume": screen(_candle(stamp, volume_contracts="1")).reason,
        "wide_spread": screen(_candle(stamp, yes_bid_close="0.20")).reason,
        "side_policy": screen(
            _candle(stamp), controls=SelectiveControls(allowed_sides=("NO",)),
        ).reason,
        "price_band": screen(
            _candle(stamp, yes_bid_close="0.94", yes_ask_close="0.96"),
        ).reason,
    }
    expected = {
        "accepted": "ACCEPTED",
        "low_volume": "MINUTE_VOLUME_BELOW_CONTROL",
        "wide_spread": "MINUTE_SPREAD_ABOVE_CONTROL",
        "side_policy": "SIDE_POLICY",
        "price_band": "ENTRY_PRICE_OUTSIDE_CONTROL_BAND",
    }
    invariants = {
        "future_candle_excluded": int(selected.observed_at.timestamp()) == stamp - 60,
        "all_selective_controls_exercised": decisions == expected,
        "uncertainty_buffer_applied": str(accepted.purchased_probability) == "0.70",
        "one_contract_only": accepted.quantity == 1,
        "grade_b_assumed_fill_only": accepted.evidence_grade == "B",
        "historical_fee_not_claimed_verified": accepted.historical_fees_verified is False,
    }
    if not all(invariants.values()):
        raise ValueError("A V3 market-policy fixture invariant failed")
    code = inventory(root, [root / "src/klax_lab/market_policy_v3.py"])
    return _finish({
        "schema_version": 1,
        "status": "ENGINEERING_FIXTURES_PASS",
        "synthetic_engineering_fixtures": True,
        "campaign_or_profit_evidence": False,
        "protected_final_read": False,
        "code": code,
        "fixture_identity": canonical_hash({"candles": [_candle(stamp - 60), _candle(stamp), _candle(stamp + 60)]}),
        "invariants": invariants,
        "outputs": {
            "decision_reasons": decisions,
            "accepted_expected_net_return": str(accepted.expected_net_return),
            "selected_quote_timestamp": selected.observed_at.isoformat(),
        },
        "limitations": [
            "Synthetic candles validate deterministic policy behavior only.",
            "Grade-B aggregated quotes do not prove depth, queue position, or hypothetical fills.",
            "The fixture is not campaign, return, or protected-final evidence.",
        ],
    })


def publish_component_fixtures(root: Path, output_root: Path | None = None) -> tuple[dict, dict]:
    root = root.resolve()
    destination = root if output_root is None else output_root.resolve()
    model = build_model_fixture_evidence(root)
    market = build_market_fixture_evidence(root)
    write_json(destination / MODEL_MANIFEST, model)
    write_json(destination / MARKET_MANIFEST, market)
    return model, market


def verify_component_fixtures(root: Path, manifest_root: Path | None = None) -> dict[str, Any]:
    """Re-execute the fixtures and require byte-level semantic equivalence."""
    root = root.resolve()
    source = root if manifest_root is None else manifest_root.resolve()
    expected = {
        "model": build_model_fixture_evidence(root),
        "market_policy": build_market_fixture_evidence(root),
    }
    paths = {
        "model": source / MODEL_MANIFEST,
        "market_policy": source / MARKET_MANIFEST,
    }
    for name, path in paths.items():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise ValueError(f"Missing or invalid V3 {name} fixture manifest") from exc
        if saved != expected[name]:
            raise ValueError(f"V3 {name} fixture manifest is stale or modified")
    return {
        "status": "PASS",
        "protected_final_read": False,
        "campaign_authorized": False,
        "model_fixture_sha256": expected["model"]["evidence_sha256"],
        "market_fixture_sha256": expected["market_policy"]["evidence_sha256"],
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Publish deterministic V3 component fixtures")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    model, market = publish_component_fixtures(args.root)
    print(json.dumps({
        "model_fixture_sha256": model["evidence_sha256"],
        "market_fixture_sha256": market["evidence_sha256"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
