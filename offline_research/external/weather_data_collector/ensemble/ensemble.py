"""
ensemble.py

Combine weather model probability distributions into
a single weighted ensemble distribution.

Models:
- GFS
- GFS Seamless
- NAM
- NBM

This module only handles forecast combination.
It contains no trading logic.

Author: weather_data_collector
"""

from probability.distribution import combine_distributions


DEFAULT_MODEL_WEIGHTS = {
    "gfs": 0.30,
    "gfs_seamless": 0.20,
    "nam": 0.20,
    "nbm": 0.30
}


def create_weight_vector(
    available_models,
    model_weights=None
):
    """
    Convert model weight dictionary into ordered list.

    Parameters
    ----------
    available_models : list[str]
        Names of models.

    model_weights : dict
        Model weights.

    Returns
    -------
    list[float]
    """

    if model_weights is None:
        model_weights = DEFAULT_MODEL_WEIGHTS

    weights = []

    for model in available_models:
        weights.append(
            model_weights.get(
                model,
                0
            )
        )

    total = sum(weights)

    if total == 0:
        raise ValueError(
            "Model weights sum to zero"
        )

    return [
        weight / total
        for weight in weights
    ]


def build_ensemble_distribution(
    model_distributions,
    model_weights=None
):
    """
    Build weighted ensemble forecast.

    Parameters
    ----------
    model_distributions : dict

        Example:

        {
            "gfs": {
                89: 0.2,
                90: 0.5,
                91: 0.3
            },

            "nam": {
                89: 0.3,
                90: 0.4,
                91: 0.3
            }
        }


    model_weights : dict

        Example:

        {
            "gfs":0.4,
            "nam":0.6
        }


    Returns
    -------
    dict

        Combined probability distribution.
    """

    models = list(
        model_distributions.keys()
    )

    distributions = [
        model_distributions[model]
        for model in models
    ]

    weights = create_weight_vector(
        models,
        model_weights
    )

    return combine_distributions(
        distributions,
        weights
    )


def ensemble_statistics(
    distribution
):
    """
    Calculate summary statistics.

    Returns
    -------
    dict

    {
        "expected_temperature": 90.2,
        "most_likely_temperature": 90,
        "confidence": 0.35
    }
    """

    expected = sum(
        temperature * probability
        for temperature, probability
        in distribution.items()
    )

    most_likely = max(
        distribution,
        key=distribution.get
    )

    confidence = distribution[
        most_likely
    ]

    return {
        "expected_temperature": expected,
        "most_likely_temperature": most_likely,
        "confidence": confidence
    }