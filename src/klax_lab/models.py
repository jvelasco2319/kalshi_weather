"""Small, deterministic Gaussian baselines; no language-model probabilities."""
from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import fmean, pstdev


BASELINE_DEFINITIONS = {
    "seasonal_climatology": "Circular day-of-year +/-45 days, 2024 NWS labels; population sd floor1F",
    "gfs_bias_corrected": "GFS sampled max plus training mean residual; population residual sd floor1F",
    "nbm_bias_corrected": "NBM sampled max plus training mean residual; population residual sd floor1F",
    "equal_blend_bias_corrected": "Equal GFS/NBM sampled-max blend, training mean residual and sd floor1F",
    "collector_style_fixed_2f": "Equal GFS/NBM sampled-max blend, no correction, fixed2F; diagnostic approximation, not exact original collector",
}


def seasonal_position(day: str) -> int:
    value = date.fromisoformat(day)
    # Fixed leap-year calendar avoids March shifts between training/evaluation.
    return date(2000, value.month, value.day).timetuple().tm_yday


def _feature(row: dict, model: str) -> float:
    if model == "gfs_bias_corrected":
        return row["gfs"]
    if model == "nbm_bias_corrected":
        return row["nbm"]
    return (row["gfs"] + row["nbm"]) / 2


def fit_baselines(training: list[dict], minimum: int = 100) -> dict:
    """All models use the same complete 2024 cases; no selection/final labels."""
    if len(training) < minimum:
        raise ValueError("Insufficient complete training days")
    if len({r["climate_date"] for r in training}) != len(training):
        raise ValueError("Duplicate training weather day")
    if any(not all(isfinite(r[k]) for k in ("gfs", "nbm", "tmax_f")) for r in training):
        raise ValueError("Nonfinite training data")
    if any(not r["climate_date"].startswith("2024-") for r in training):
        raise ValueError("Pilot baseline fitting is restricted to2024")
    models = {}
    for name, definition in BASELINE_DEFINITIONS.items():
        model = {"name": name, "definition": definition, "training_days": len(training)}
        if name == "seasonal_climatology":
            model["seasonal_labels"] = [{"position": seasonal_position(r["climate_date"]), "tmax_f": r["tmax_f"]} for r in training]
        elif name == "collector_style_fixed_2f":
            model.update(bias=0.0, sd=2.0)
        else:
            residual = [r["tmax_f"] - _feature(r, name) for r in training]
            model.update(bias=fmean(residual), sd=max(1.0, pstdev(residual)))
        models[name] = model
    return models


def predict(model: dict, row: dict) -> tuple[float, float]:
    if model["name"] == "seasonal_climatology":
        position = seasonal_position(row["climate_date"])
        labels = [r["tmax_f"] for r in model["seasonal_labels"] if min(abs(r["position"] - position), 366 - abs(r["position"] - position)) <= 45]
        if len(labels) < 30:
            raise ValueError("Seasonal baseline has fewer than30 local-window labels")
        return fmean(labels), max(1.0, pstdev(labels))
    return _feature(row, model["name"]) + model["bias"], model["sd"]
