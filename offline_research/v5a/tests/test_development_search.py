from decimal import Decimal

from v5a.development_search import _fee, _transform


def test_fee_rounding_transition_is_date_exact():
    assert _fee("2026-07-06", 50) == Decimal("0.02")
    assert _fee("2026-07-07", 50) == Decimal("0.0175")


def test_probability_transform_conserves_mass():
    values = _transform([0.05, 0.10, 0.20, 0.30, 0.25, 0.10], 1.25, 0.07)
    assert abs(sum(values) - 1.0) < 1e-12
    assert all(0 < value < 1 for value in values)
