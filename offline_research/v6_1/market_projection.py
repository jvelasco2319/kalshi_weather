"""Outcome-blind projection of contemporaneous bracket quotes to one probability vector."""
from __future__ import annotations


def _bounded_simplex(midpoints: list[float], lower: list[float], upper: list[float]) -> list[float]:
    if not midpoints or not (len(midpoints) == len(lower) == len(upper)):
        raise ValueError("Projection vectors must have equal nonzero length")
    if any(not 0 <= lo <= hi <= 1 for lo, hi in zip(lower, upper)):
        raise ValueError("Probability interval is invalid")
    if sum(lower) > 1 + 1e-12 or sum(upper) < 1 - 1e-12:
        raise ValueError("Probability intervals have no coherent simplex intersection")
    left = min(value - hi for value, hi in zip(midpoints, upper)) - 1
    right = max(value - lo for value, lo in zip(midpoints, lower)) + 1
    for _ in range(100):
        shift = (left + right) / 2
        projected = [min(hi, max(lo, value - shift))
                     for value, lo, hi in zip(midpoints, lower, upper)]
        if sum(projected) > 1:
            left = shift
        else:
            right = shift
    result = [min(hi, max(lo, value - right))
              for value, lo, hi in zip(midpoints, lower, upper)]
    residual = 1 - sum(result)
    if abs(residual) > 1e-10:
        for index in range(len(result)):
            room = upper[index] - result[index] if residual > 0 else result[index] - lower[index]
            change = min(abs(residual), room)
            result[index] += change if residual > 0 else -change
            residual += -change if residual > 0 else change
            if abs(residual) <= 1e-12:
                break
    if abs(sum(result) - 1) > 1e-9:
        raise ValueError("Probability projection failed to sum to one")
    return result


def coherent_market_probabilities(intervals: list[dict]) -> dict:
    """Project fixed, outcome-blind bid/ask intervals using Euclidean distance.

    Each item must have `bracket_id`, `lower`, and `upper`. When spread intervals
    have no common simplex intersection, the registered fallback projects interval
    midpoints onto the ordinary simplex and records that relaxation.
    """
    ordered = sorted(intervals, key=lambda item: item["bracket_id"])
    if len({item["bracket_id"] for item in ordered}) != len(ordered):
        raise ValueError("Bracket identifiers must be unique")
    lower = [float(item["lower"]) for item in ordered]
    upper = [float(item["upper"]) for item in ordered]
    midpoint = [(lo + hi) / 2 for lo, hi in zip(lower, upper)]
    relaxed = sum(lower) > 1 + 1e-12 or sum(upper) < 1 - 1e-12
    if relaxed:
        values = _bounded_simplex(midpoint, [0.0] * len(midpoint), [1.0] * len(midpoint))
    else:
        values = _bounded_simplex(midpoint, lower, upper)
    return {
        "method": "fixed_euclidean_bounded_simplex_v1",
        "intervals_relaxed": relaxed,
        "probabilities": [
            {"bracket_id": item["bracket_id"], "probability": probability}
            for item, probability in zip(ordered, values)
        ],
        "sum": sum(values),
    }

