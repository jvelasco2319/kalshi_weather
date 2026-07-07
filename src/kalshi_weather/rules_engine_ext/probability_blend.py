from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbabilityBlend:
    raw_model_probability: float
    calibrated_model_probability: float
    market_implied_probability: float
    model_weight: float
    market_weight: float
    final_trade_probability: float
    probability_blend_reason: str

PROFILE_MODEL_WEIGHTS = {
    "overnight_next_day": 0.35,
    "morning_pre_observation": 0.45,
    "active_nowcast": 0.60,
    "late_day_risk_manage": 0.50,
    "risk_reduce": 0.35,
    "close_only": 0.30,
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def choose_model_weight(
    profile: str,
    model_disagreement_level: str = "low",
    observation_confirms: bool = False,
    station_skill_score: float | None = None,
) -> tuple[float, str]:
    w = PROFILE_MODEL_WEIGHTS.get(profile, 0.35)
    reasons = [f"profile={profile}:{w:.2f}"]
    if model_disagreement_level == "high":
        w -= 0.15
        reasons.append("high_disagreement:-0.15")
    elif model_disagreement_level == "extreme":
        w -= 0.30
        reasons.append("extreme_disagreement:-0.30")
    if observation_confirms:
        w += 0.10
        reasons.append("observation_confirms:+0.10")
    if station_skill_score is not None:
        if station_skill_score > 0.05:
            w += 0.10
            reasons.append("positive_station_skill:+0.10")
        elif station_skill_score < -0.05:
            w -= 0.10
            reasons.append("negative_station_skill:-0.10")
    w = clamp(w, 0.20, 0.75)
    return w, "; ".join(reasons)


def blend_probability(
    raw_model_probability: float,
    market_implied_probability: float,
    profile: str,
    calibrated_model_probability: float | None = None,
    model_disagreement_level: str = "low",
    observation_confirms: bool = False,
    station_skill_score: float | None = None,
) -> ProbabilityBlend:
    calibrated = raw_model_probability if calibrated_model_probability is None else calibrated_model_probability
    model_weight, reason = choose_model_weight(profile, model_disagreement_level, observation_confirms, station_skill_score)
    market_weight = 1.0 - model_weight
    final_p = clamp(model_weight * calibrated + market_weight * market_implied_probability)
    return ProbabilityBlend(
        raw_model_probability=raw_model_probability,
        calibrated_model_probability=calibrated,
        market_implied_probability=market_implied_probability,
        model_weight=model_weight,
        market_weight=market_weight,
        final_trade_probability=final_p,
        probability_blend_reason=reason,
    )
