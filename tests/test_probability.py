import numpy as np

from kalshi_weather.model.probability import bracket_probability, normalize_probabilities, settlement_high_samples
from kalshi_weather.schemas import Bracket


def test_settlement_high_never_below_observed() -> None:
    samples = settlement_high_samples(70.0, observed_high_so_far_f=72.0, residual_sigma_f=0.1, sample_count=1000)
    assert float(np.min(samples)) >= 72.0


def test_bracket_probability_closed_range() -> None:
    samples = np.array([69.4, 69.5, 70.0, 71.4, 71.5, 72.0])
    bracket = Bracket(ticker="X", label="70 to 71", lo_f=70, hi_f=71)
    # [69.5, 71.5) includes 69.5, 70.0, 71.4 but excludes 69.4, 71.5, 72.0
    assert bracket_probability(samples, bracket) == 3 / 6


def test_normalize_probabilities() -> None:
    assert normalize_probabilities({"a": 2.0, "b": 2.0}) == {"a": 0.5, "b": 0.5}
