"""
distribution.py

Generate probability distributions from weather model forecasts.

This module converts a predicted daily high temperature into
a probability distribution over possible realized temperatures.

Currently uses a Gaussian uncertainty model.

Future improvements:
- model-specific calibration
- historical forecast error distributions
- bias correction
- seasonal variance models

Author: weather_data_collector
"""

import numpy as np


def gaussian_temperature_distribution(
    forecast_temp_f: float,
    uncertainty_f: float = 2.0,
    min_temp: int = None,
    max_temp: int = None
):
    """
    Generate temperature probability distribution.

    Parameters
    ----------
    forecast_temp_f : float
        Model predicted daily high (Fahrenheit).

    uncertainty_f : float
        Forecast standard deviation.

    min_temp : int
        Minimum temperature bin.

    max_temp : int
        Maximum temperature bin.

    Returns
    -------
    dict
        Temperature probability distribution.

    Example
    -------
    {
        88: 0.08,
        89: 0.22,
        90: 0.40,
        91: 0.22,
        92: 0.08
    }
    """

    if min_temp is None:
        min_temp = int(np.floor(
            forecast_temp_f - 5 * uncertainty_f
        ))

    if max_temp is None:
        max_temp = int(np.ceil(
            forecast_temp_f + 5 * uncertainty_f
        ))

    temperatures = np.arange(
        min_temp,
        max_temp + 1
    )

    probabilities = np.exp(
        -0.5 *
        (
            (temperatures - forecast_temp_f)
            / uncertainty_f
        ) ** 2
    )

    probabilities /= probabilities.sum()

    return {
        int(temp): float(prob)
        for temp, prob in zip(
            temperatures,
            probabilities
        )
    }


def combine_distributions(
    distributions,
    weights=None
):
    """
    Combine multiple model distributions.

    Parameters
    ----------
    distributions : list[dict]
        List of temperature distributions.

    weights : list[float]
        Model weights.

    Returns
    -------
    dict
        Weighted ensemble distribution.

    Example:

    GFS 0.4
    NAM 0.2
    NBM 0.4
    """

    if weights is None:
        weights = [
            1 / len(distributions)
            for _ in distributions
        ]

    all_temperatures = set()

    for dist in distributions:
        all_temperatures.update(
            dist.keys()
        )

    ensemble = {}

    for temp in all_temperatures:

        probability = 0.0

        for weight, dist in zip(
            weights,
            distributions
        ):
            probability += (
                weight *
                dist.get(temp, 0)
            )

        ensemble[int(temp)] = probability


    total = sum(
        ensemble.values()
    )

    if total > 0:
        ensemble = {
            temp: prob / total
            for temp, prob in ensemble.items()
        }

    return dict(
        sorted(
            ensemble.items()
        )
    )


def most_likely_temperature(
    distribution
):
    """
    Return maximum probability temperature.
    """

    return max(
        distribution,
        key=distribution.get
    )


def expected_temperature(
    distribution
):
    """
    Compute expected temperature.
    """

    return sum(
        temp * probability
        for temp, probability
        in distribution.items()
    )