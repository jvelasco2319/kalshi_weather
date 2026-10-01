from __future__ import annotations

import math

import pytest

from v8.economic_replay import V8ReplayError, _fit_mapping, _repaired
from v8.probability_repair import Example


def test_fit_mapping_uses_modal_position_and_dirichlet_two_prior() -> None:
    examples = [
        Example("2025-01-01", (0.7, 0.1, 0.05, 0.05, 0.05, 0.05), 1),
        Example("2025-01-02", (0.6, 0.1, 0.1, 0.1, 0.05, 0.05), 1),
        Example("2025-01-03", (0.1, 0.6, 0.1, 0.1, 0.05, 0.05), 0),
    ]
    counts, distributions = _fit_mapping(examples)
    assert counts[0] == [2.0, 4.0, 2.0, 2.0, 2.0, 2.0]
    assert counts[1] == [3.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    assert math.isclose(sum(distributions[0]), 1.0)


def test_repair_has_strictly_positive_mass_and_fixed_weights() -> None:
    distributions = [[1 / 6] * 6 for _ in range(6)]
    repaired = _repaired([1.0, 0, 0, 0, 0, 0], distributions)
    assert repaired[0] == pytest.approx(0.375)
    assert repaired[1:] == pytest.approx([0.125] * 5)
    assert min(repaired) > 0
    assert sum(repaired) == pytest.approx(1.0)


def test_repair_rejects_invalid_probability_vector() -> None:
    with pytest.raises(V8ReplayError):
        _repaired([0.5, 0.5], [[1 / 6] * 6 for _ in range(6)])
