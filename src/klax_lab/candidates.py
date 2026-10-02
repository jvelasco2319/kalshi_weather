"""Closed, host-owned calibration recipes that agents may propose as JSON.

No supplied source code, expressions, paths or callables are accepted. Recipe
parameters choose finite numerical methods fitted on the training year only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import cos, isfinite, pi, sin, sqrt
from statistics import fmean

import numpy as np

from .models import seasonal_position
from .provenance import canonical_hash


@dataclass(frozen=True)
class CandidateSpec:
    gfs_weight: float
    bias_mode: str
    spread_mode: str
    spread_scale: float
    disagreement_coefficient: float

    def __post_init__(self):
        if type(self.gfs_weight) not in (int, float) or self.gfs_weight not in (0, .25, .5, .75, 1):
            raise ValueError("GFS weight is outside the closed recipe set")
        if self.bias_mode not in ("global", "monthly_shrinkage", "seasonal_harmonic"):
            raise ValueError("Unsupported bias recipe")
        if self.spread_mode not in ("global", "monthly_shrinkage", "disagreement"):
            raise ValueError("Unsupported spread recipe")
        if type(self.spread_scale) not in (int, float) or self.spread_scale not in (.85, 1, 1.15, 1.3):
            raise ValueError("Unsupported spread scale")
        allowed = (.25, .5) if self.spread_mode == "disagreement" else (0,)
        if type(self.disagreement_coefficient) not in (int, float) or self.disagreement_coefficient not in allowed:
            raise ValueError("Disagreement coefficient is inconsistent with spread mode")

    @classmethod
    def from_dict(cls, value: dict) -> "CandidateSpec":
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("Candidate must contain exactly the five declared recipe fields")
        return cls(**value)

    @property
    def identity(self) -> str:
        # Normalize exact numeric enums so JSON0 and0.0 cannot create two trials.
        params = asdict(self)
        for field in ("gfs_weight", "spread_scale", "disagreement_coefficient"):
            params[field] = float(params[field])
        return canonical_hash({"recipe_version": "gaussian-calibration-v1", **params})


def _basis(day: str) -> list[float]:
    angle = 2 * pi * (seasonal_position(day) - 1) / 366
    return [1., sin(angle), cos(angle)]


def _bias(model: dict, day: str) -> float:
    mode = model["spec"]["bias_mode"]
    if mode == "global":
        return model["global_bias"]
    if mode == "monthly_shrinkage":
        return model["monthly_bias"][str(date.fromisoformat(day).month)]
    return sum(a * b for a, b in zip(model["harmonic_coefficients"], _basis(day)))


def fit_candidate(spec: CandidateSpec, rows: list[dict], minimum_days: int = 100) -> dict:
    if len(rows) < minimum_days or len({r["climate_date"] for r in rows}) != len(rows):
        raise ValueError("Training requires enough unique weather days")
    if any(not r["climate_date"].startswith("2024-") for r in rows):
        raise ValueError("Candidate fit is restricted to2024")
    if any(not all(isfinite(r[k]) for k in ("gfs", "nbm", "tmax_f")) for r in rows):
        raise ValueError("Training temperatures must be finite")
    blend = lambda r: spec.gfs_weight * r["gfs"] + (1 - spec.gfs_weight) * r["nbm"]
    residual = [r["tmax_f"] - blend(r) for r in rows]
    global_bias = fmean(residual)
    months = {month: [i for i, r in enumerate(rows) if date.fromisoformat(r["climate_date"]).month == month] for month in range(1, 13)}
    monthly_bias = {str(month): (sum(residual[i] for i in indices) + 30 * global_bias) / (len(indices) + 30) for month, indices in months.items()}
    design = np.asarray([_basis(r["climate_date"]) for r in rows])
    coeff, _, rank, _ = np.linalg.lstsq(design, residual, rcond=None)
    if rank != 3:
        raise ValueError("Seasonal design lacks full rank")
    model = {"candidate_id": spec.identity, "recipe_version": "gaussian-calibration-v1", "spec": asdict(spec),
             "training_days": len(rows), "global_bias": global_bias, "monthly_bias": monthly_bias,
             "harmonic_coefficients": coeff.tolist(), "monthly_prior_count": 30,
             "fitting_note": "Mean methods and population residual variances fitted solely on2024; spread floor1F"}
    errors = [residual[i] - _bias(model, row["climate_date"]) for i, row in enumerate(rows)]
    variance = max(1., fmean(error * error for error in errors))
    model["global_variance"] = variance
    model["monthly_variance"] = {str(month): max(1., (sum(errors[i] ** 2 for i in indices) + 30 * variance) / (len(indices) + 30)) for month, indices in months.items()}
    return model


def predict_candidate(model: dict, row: dict) -> tuple[float, float]:
    spec = CandidateSpec.from_dict(model["spec"])
    if model["candidate_id"] != spec.identity:
        raise ValueError("Candidate identity mismatch")
    mean = spec.gfs_weight * row["gfs"] + (1 - spec.gfs_weight) * row["nbm"] + _bias(model, row["climate_date"])
    variance = model["monthly_variance"][str(date.fromisoformat(row["climate_date"]).month)] if spec.spread_mode == "monthly_shrinkage" else model["global_variance"]
    if spec.spread_mode == "disagreement":
        variance += (spec.disagreement_coefficient * (row["gfs"] - row["nbm"])) ** 2
    sd = max(1., sqrt(variance) * spec.spread_scale)
    if not isfinite(mean) or not isfinite(sd):
        raise ValueError("Invalid candidate distribution")
    return mean, sd
