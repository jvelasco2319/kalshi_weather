from __future__ import annotations

import math
from pathlib import Path

from v8 import probability_repair as repair


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_is_finite_and_unique() -> None:
    candidates = repair.catalog()
    assert len(candidates) == 104
    assert len({candidate.candidate_id for candidate in candidates}) == len(candidates)
    assert candidates[0] == repair.Candidate("identity", "identity")


def test_every_repair_family_returns_probability_mass() -> None:
    base = (0.0, 0.0, 0.1, 0.8, 0.1, 0.0)
    history = [repair.Example("2025-01-01", base, 0)]
    representatives = {}
    for candidate in repair.catalog():
        representatives.setdefault(candidate.family, candidate)
    for family, candidate in representatives.items():
        result = repair.transform(base, candidate, history)
        assert len(result) == 6
        assert math.isclose(sum(result), 1.0, abs_tol=1e-12)
        assert all(math.isfinite(value) and value >= 0 for value in result)
        if family != "identity":
            assert min(result) > 0


def test_heavy_tail_kernel_spreads_mass_across_all_brackets() -> None:
    point = (0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    for family in ("cauchy", "exponential"):
        candidate = repair.Candidate(family, family, (("scale", 1.0), ("weight", 1.0)))
        result = repair.transform(point, candidate)
        assert all(value > 0 for value in result)
        assert result[2] == max(result)
        assert result[1] > result[0]
        assert result[3] > result[5]


def test_rolling_calibrator_cannot_see_future_outcome() -> None:
    candidate = repair.Candidate(
        "rolling", "rolling_climatology", (("prior", 6.0), ("weight", 0.5))
    )
    base = (0.05, 0.1, 0.2, 0.4, 0.2, 0.05)
    past = [repair.Example("2025-01-01", base, 0)]
    future_a = repair.Example("2025-01-03", base, 1)
    future_b = repair.Example("2025-01-03", base, 5)
    assert repair.transform(base, candidate, past) == repair.transform(base, candidate, past)
    assert repair.transform(base, candidate, past + [future_a]) != repair.transform(base, candidate, past + [future_b])


def test_evaluation_prediction_does_not_use_same_day_outcome() -> None:
    candidate = repair.Candidate(
        "rolling", "rolling_confusion", (("prior", 12.0), ("weight", 0.75))
    )
    base = (0.05, 0.1, 0.2, 0.4, 0.2, 0.05)
    prefix = [repair.Example("2025-01-01", base, 0), repair.Example("2025-01-02", base, 1)]
    sequence_a = prefix + [repair.Example("2025-01-03", base, 2)]
    sequence_b = prefix + [repair.Example("2025-01-03", base, 5)]
    scored_a = repair.evaluate(candidate, sequence_a)
    scored_b = repair.evaluate(candidate, sequence_b)
    assert scored_a[2].probabilities == scored_b[2].probabilities
    assert scored_a[2].actual_index != scored_b[2].actual_index


def test_v7y_inputs_are_bound_and_have_fixed_partition() -> None:
    examples, bindings = repair._load_examples(ROOT)
    assert len(examples) == 359
    assert sum(row.climate_date < "2025-02-04" for row in examples) == 30
    assert sum(row.climate_date >= "2025-02-04" for row in examples) == 329
    assert set(bindings) == {
        repair.V7Y_FREEZE.as_posix(),
        repair.V7Y_SUMMARY.as_posix(),
        repair.v7y.CLIMATE_MANIFEST.as_posix(),
        "data/raw/climate/CLILAX_2024-01-01_2026-01-08.zip",
    }

