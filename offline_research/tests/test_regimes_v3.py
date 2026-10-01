"""Numerical and boundary tests for the registered six-regime classifier."""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from klax_lab.regimes_v3 import (
    POOLED_FALLBACK,
    WEATHER_REGIMES,
    fit_six_regime_classifier,
)


PROJECT = Path(__file__).resolve().parents[1]


def regime_fixture() -> tuple[list[list[float]], list[str]]:
    centers = ((-3, 0), (-1.5, 2.6), (1.5, 2.6), (3, 0), (1.5, -2.6), (-1.5, -2.6))
    features, labels = [], []
    for regime, center in zip(WEATHER_REGIMES, centers):
        for index in range(30):
            angle = index * .91
            features.append([
                center[0] + .18 * np.sin(angle),
                center[1] + .18 * np.cos(angle),
            ])
            labels.append(regime)
    return features, labels


def test_regimes_match_registration_exactly() -> None:
    config = json.loads((PROJECT / "configs/v3_goal.json").read_text(encoding="utf-8"))
    assert tuple(config["model_architecture"]["weather_regimes"]) == WEATHER_REGIMES


def test_classifier_is_deterministic_inspectable_and_accurate() -> None:
    features, labels = regime_fixture()
    kwargs = dict(feature_names=("marine_signal", "synoptic_signal"),
                  deterministic_seed=23, maximum_iterations=3000,
                  minimum_confidence=.45)
    first = fit_six_regime_classifier(features, labels, **kwargs)
    second = fit_six_regime_classifier(features, labels, **kwargs)

    assert first == second
    assert first.regimes == WEATHER_REGIMES
    assert set(first.coefficient_table()) == set(WEATHER_REGIMES)
    assert len(first.raw_intercepts) == 6
    predictions = first.classify(features)
    accuracy = sum(item.regime == expected for item, expected in zip(predictions, labels)) / len(labels)
    assert accuracy > .95
    assert all(sum(item.probabilities) == pytest.approx(1.0, abs=1e-12)
               for item in predictions)


def test_low_confidence_assignment_uses_pooled_fallback() -> None:
    features, labels = regime_fixture()
    model = fit_six_regime_classifier(features, labels, minimum_confidence=.80)
    assignment = model.classify([[0.0, 0.0]])[0]
    assert assignment.regime == POOLED_FALLBACK
    assert assignment.use_pooled_fallback is True
    assert assignment.confidence < .80


def test_fitter_uses_only_caller_rows_and_performs_no_io() -> None:
    features, labels = regime_fixture()
    with patch.object(builtins, "open", side_effect=AssertionError("unexpected file access")):
        model = fit_six_regime_classifier(features, labels)
        assert len(model.predict_proba([[1.0, 2.0]])[0]) == 6


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_training_and_prediction_features_are_rejected(invalid: float) -> None:
    features, labels = regime_fixture()
    damaged = [row[:] for row in features]
    damaged[0][1] = invalid
    with pytest.raises(ValueError, match="finite"):
        fit_six_regime_classifier(damaged, labels)
    model = fit_six_regime_classifier(features, labels)
    with pytest.raises(ValueError, match="finite"):
        model.classify([[0.0, invalid]])


def test_missing_sparse_unknown_and_constant_regime_inputs_are_rejected() -> None:
    features, labels = regime_fixture()
    with pytest.raises(ValueError, match="insufficient rows"):
        fit_six_regime_classifier(features[:150], labels[:150])
    unknown = labels[:]
    unknown[0] = "invented_regime"
    with pytest.raises(ValueError, match="unregistered"):
        fit_six_regime_classifier(features, unknown)
    with pytest.raises(ValueError, match="constant feature"):
        fit_six_regime_classifier([[row[0], 1.0] for row in features], labels)


def test_alignment_width_and_confidence_are_strict() -> None:
    features, labels = regime_fixture()
    with pytest.raises(ValueError, match="not aligned"):
        fit_six_regime_classifier(features, labels[:-1])
    with pytest.raises(ValueError, match="seed"):
        fit_six_regime_classifier(features, labels, deterministic_seed=-1)
    model = fit_six_regime_classifier(features, labels)
    with pytest.raises(ValueError, match="width"):
        model.classify([[1.0, 2.0, 3.0]])
    with pytest.raises(ValueError, match="threshold"):
        model.classify([[1.0, 2.0]], minimum_confidence=1 / 6)
