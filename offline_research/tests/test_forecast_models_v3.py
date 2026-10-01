import math

import pytest

from klax_lab.domain import ContractBounds
from klax_lab.forecast_models_v3 import (
    fit_distribution_model,
    fit_residual_distribution,
    fit_ridge_location,
)


def training_fixture(count: int = 120):
    features, targets, regimes = [], [], []
    for index in range(count):
        x = -2 + 4 * index / (count - 1)
        y = math.sin(index * .31)
        features.append([x, y])
        targets.append(75 + 3.5 * x + .4 * y + (index % 5 - 2) * .35)
        regimes.append("ordinary_sea_breeze" if index % 2 else "persistent_marine_layer")
    return features, targets, regimes


def test_ridge_location_is_deterministic_and_predictive() -> None:
    features, targets, _ = training_fixture()
    first = fit_ridge_location(features, targets)
    second = fit_ridge_location(features, targets)
    assert first == second
    predictions = first.predict(features)
    mae = sum(abs(left - right) for left, right in zip(predictions, targets)) / len(targets)
    assert mae < 1.0


@pytest.mark.parametrize("family", ["gaussian_blend", "quantile_brackets", "gaussian_mixture"])
def test_registered_residual_families_conserve_contract_probability(family: str) -> None:
    features, targets, regimes = training_fixture()
    model = fit_distribution_model(
        features, targets, family=family, regimes=regimes,
        minimum_regime_training_days=30,
    )
    contracts = (
        ContractBounds(None, 72), ContractBounds(73, 75),
        ContractBounds(76, 78), ContractBounds(79, None),
    )
    probabilities, fallback, location = model.contract_probabilities(
        features[0], contracts, regime="ordinary_sea_breeze",
    )
    assert sum(probabilities) == pytest.approx(1.0, abs=1e-12)
    assert all(0 <= value <= 1 for value in probabilities)
    assert fallback is False
    assert math.isfinite(location)


def test_untrained_regime_uses_explicit_pooled_fallback() -> None:
    features, targets, regimes = training_fixture()
    model = fit_distribution_model(
        features, targets, family="gaussian_mixture", regimes=regimes,
        minimum_regime_training_days=90,
    )
    contracts = (ContractBounds(None, 75), ContractBounds(76, None))
    _, fallback, _ = model.contract_probabilities(
        features[0], contracts, regime="persistent_marine_layer",
    )
    assert fallback is True


def test_invalid_or_sparse_distribution_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="constant feature"):
        fit_ridge_location([[1.0, 2.0]] * 30, [70.0] * 30)
    with pytest.raises(ValueError, match="insufficient"):
        fit_residual_distribution([0.0] * 10, "gaussian_mixture")
    with pytest.raises(ValueError, match="Unsupported"):
        fit_residual_distribution([float(index) for index in range(30)], "invented")
