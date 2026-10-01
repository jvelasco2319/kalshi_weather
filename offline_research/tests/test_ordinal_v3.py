"""Numerical and boundary tests for the V3 ordered-logistic fitter."""
from __future__ import annotations

import builtins
from unittest.mock import patch

import numpy as np
import pytest

from klax_lab.ordinal_v3 import fit_ordered_logistic_brackets


def ordered_fixture() -> tuple[list[list[float]], list[int]]:
    features, labels = [], []
    for index in range(360):
        signal = -3.0 + 6.0 * index / 359
        secondary = np.sin(index * .37)
        latent = signal + .15 * secondary
        label = sum(latent > threshold for threshold in (-2, -1, 0, 1, 2))
        features.append([signal, secondary])
        labels.append(label)
    return features, labels


def test_fit_is_deterministic_inspectable_and_conserves_bracket_probability() -> None:
    features, labels = ordered_fixture()
    arguments = dict(
        feature_names=("forecast_temperature", "forecast_disagreement"),
        bracket_names=("low", "cool", "mild", "warm", "hot", "very_hot"),
        deterministic_seed=17,
        maximum_iterations=3000,
    )
    first = fit_ordered_logistic_brackets(features, labels, **arguments)
    second = fit_ordered_logistic_brackets(features, labels, **arguments)

    assert first == second
    assert len(first.thresholds) == 5
    assert len(first.raw_thresholds) == 5
    assert all(right > left for left, right in zip(first.thresholds, first.thresholds[1:]))
    assert first.coefficient_table()["forecast_temperature"] > 0
    assert first.category_counts == tuple(labels.count(index) for index in range(6))

    rows = [[-2.5, 0.0], [0.0, 0.0], [2.5, 0.0]]
    probabilities = first.predict_proba(rows)
    assert all(len(row) == 6 and sum(row) == pytest.approx(1.0, abs=1e-12)
               and all(value >= 0 for value in row) for row in probabilities)
    assert first.predict_bracket(rows) == ("low", "mild", "very_hot")


def test_fitter_uses_only_caller_rows_and_performs_no_io() -> None:
    features, labels = ordered_fixture()
    with patch.object(builtins, "open", side_effect=AssertionError("unexpected file access")):
        model = fit_ordered_logistic_brackets(features, labels)
        assert len(model.predict_proba([[0.1, 0.2]])[0]) == 6


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_training_and_prediction_features_are_rejected(invalid: float) -> None:
    features, labels = ordered_fixture()
    damaged = [row[:] for row in features]
    damaged[0][0] = invalid
    with pytest.raises(ValueError, match="finite"):
        fit_ordered_logistic_brackets(damaged, labels)
    model = fit_ordered_logistic_brackets(features, labels)
    with pytest.raises(ValueError, match="finite"):
        model.predict_proba([[invalid, 0.0]])


def test_missing_or_sparse_bracket_and_constant_features_are_rejected() -> None:
    features, labels = ordered_fixture()
    with pytest.raises(ValueError, match="every ordered bracket"):
        fit_ordered_logistic_brackets(features, [min(value, 4) for value in labels],
                                      bracket_names=tuple(str(i) for i in range(6)))
    sparse = labels[:]
    sparse[:] = [0] * 100 + [1] * 100 + [2] * 100 + [3] * 55 + [4] * 4 + [5]
    with pytest.raises(ValueError, match="insufficient rows"):
        fit_ordered_logistic_brackets(features, sparse, minimum_rows_per_bracket=5)
    with pytest.raises(ValueError, match="constant feature"):
        fit_ordered_logistic_brackets([[row[0], 1.0] for row in features], labels)


def test_alignment_feature_width_and_labels_are_strict() -> None:
    features, labels = ordered_fixture()
    with pytest.raises(ValueError, match="not aligned"):
        fit_ordered_logistic_brackets(features, labels[:-1])
    with pytest.raises(ValueError, match="nonnegative integers"):
        fit_ordered_logistic_brackets(features, [0.0, *labels[1:]])
    with pytest.raises(ValueError, match="seed"):
        fit_ordered_logistic_brackets(features, labels, deterministic_seed=-1)
    model = fit_ordered_logistic_brackets(features, labels)
    with pytest.raises(ValueError, match="width"):
        model.predict_proba([[1.0]])
