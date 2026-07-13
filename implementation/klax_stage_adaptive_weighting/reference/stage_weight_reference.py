from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import exp, log
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

MODELS = ("ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm")
FAMILY = {
    "ecmwf_ifs": "ECMWF",
    "gfs013": "GFS",
    "gfs_seamless": "GFS",
    "nam": "NAM",
    "nbm": "NBM",
}
STAGE_PRIORS = {
    "pre_target": {"ecmwf_ifs": .23, "gfs013": .28, "gfs_seamless": .17, "nam": .14, "nbm": .18},
    "target_02_10": {"ecmwf_ifs": .22, "gfs013": .27, "gfs_seamless": .18, "nam": .15, "nbm": .18},
    "target_11_13": {"ecmwf_ifs": .20, "gfs013": .21, "gfs_seamless": .24, "nam": .17, "nbm": .18},
    "target_14_16": {"ecmwf_ifs": .20, "gfs013": .18, "gfs_seamless": .20, "nam": .27, "nbm": .15},
    "target_17_close": {"ecmwf_ifs": .21, "gfs013": .19, "gfs_seamless": .23, "nam": .22, "nbm": .15},
}
FIXED_PRIOR = {"ecmwf_ifs": .20, "gfs013": .25, "gfs_seamless": .20, "nam": .15, "nbm": .20}
STAGE_ORDER = ("pre_target", "target_02_10", "target_11_13", "target_14_16", "target_17_close")


@dataclass(frozen=True)
class StageState:
    stage_id: str
    transition_from_stage: str | None
    transition_alpha: float
    transition_minutes: int


@dataclass(frozen=True)
class ScoreSummary:
    dates: int
    n_eff: float
    log_loss: float | None
    shrunk_log_loss: float | None
    reliability_multiplier: float
    ready: bool


def classify_stage(evaluated_at: datetime, target_date: date, transition_minutes: int = 30) -> StageState:
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    local = evaluated_at.astimezone(ZoneInfo("America/Los_Angeles"))
    target_start = datetime.combine(target_date, time(0, 0), tzinfo=local.tzinfo)
    boundaries = [
        (target_start + timedelta(hours=2), "target_02_10", "pre_target"),
        (target_start + timedelta(hours=11), "target_11_13", "target_02_10"),
        (target_start + timedelta(hours=14), "target_14_16", "target_11_13"),
        (target_start + timedelta(hours=17), "target_17_close", "target_14_16"),
    ]
    if local < boundaries[0][0]:
        return StageState("pre_target", None, 1.0, transition_minutes)
    current = "pre_target"
    previous = None
    alpha = 1.0
    for boundary, stage, prior_stage in boundaries:
        if local >= boundary:
            current = stage
            elapsed = (local - boundary).total_seconds() / 60.0
            if transition_minutes > 0 and elapsed < transition_minutes:
                previous = prior_stage
                alpha = max(0.0, min(1.0, elapsed / transition_minutes))
            else:
                previous = None
                alpha = 1.0
        else:
            break
    return StageState(current, previous, alpha, transition_minutes)


def _effective_n(weights: list[float]) -> float:
    s = sum(weights)
    ss = sum(w * w for w in weights)
    return (s * s / ss) if ss > 0 else 0.0


def summarize_scores(
    losses_by_age: Iterable[tuple[float, int]],
    *,
    half_life: float = 45.0,
    shrinkage_dates: float = 30.0,
    uniform_loss: float = log(6),
    min_dates: int = 15,
    min_neff: float = 10.0,
) -> ScoreSummary:
    rows = list(losses_by_age)
    if not rows:
        return ScoreSummary(0, 0.0, None, None, 1.0, False)
    weights = [2.0 ** (-age / half_life) for _, age in rows]
    total = sum(weights)
    loss = sum(w * value for w, (value, _) in zip(weights, rows)) / total
    neff = _effective_n(weights)
    shrunk = (neff * loss + shrinkage_dates * uniform_loss) / (neff + shrinkage_dates)
    ready = len(rows) >= min_dates and neff >= min_neff
    return ScoreSummary(len(rows), neff, loss, shrunk, 1.0, ready)


def _nbm_cap(completed_dates: int) -> float:
    if completed_dates < 10:
        return 0.0
    if completed_dates < 30:
        return 0.10
    if completed_dates < 60:
        return 0.20
    return 0.25


def _normalize(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(values.get(m, 0.0))) for m in MODELS)
    if total <= 0:
        raise ValueError("cannot normalize zero model weight")
    return {m: max(0.0, float(values.get(m, 0.0))) / total for m in MODELS}


def _apply_caps(
    raw: Mapping[str, float],
    *,
    nbm_completed_dates: int,
    individual_cap: float = .35,
    gfs_cap: float = .45,
    max_iterations: int = 100,
) -> tuple[dict[str, float], dict[str, dict[str, bool | float]]]:
    caps = {m: (individual_cap if float(raw.get(m, 0.0)) > 0 else 0.0) for m in MODELS}
    caps["nbm"] = min(caps["nbm"], _nbm_cap(nbm_completed_dates))
    weights = _normalize(raw)
    flags = {m: {"individual": False, "family": False, "maturity": False, "maturity_cap": caps[m] if m == "nbm" else 1.0} for m in MODELS}

    for _ in range(max_iterations):
        changed = False
        # Individual and NBM caps.
        excess = 0.0
        uncapped: list[str] = []
        for m in MODELS:
            if weights[m] > caps[m] + 1e-12:
                excess += weights[m] - caps[m]
                weights[m] = caps[m]
                flags[m]["individual"] = caps[m] == individual_cap
                flags[m]["maturity"] = m == "nbm" and caps[m] < individual_cap
                changed = True
            elif weights[m] < caps[m] - 1e-12:
                uncapped.append(m)
        if excess > 1e-12:
            room = {m: max(0.0, caps[m] - weights[m]) for m in uncapped}
            room_total = sum(room.values())
            if room_total <= 1e-12:
                raise ValueError("caps leave no capacity to normalize weights")
            base = {m: max(weights[m], 1e-12) for m in uncapped}
            base_total = sum(base.values())
            remaining = excess
            for m in uncapped:
                add = min(room[m], excess * base[m] / base_total)
                weights[m] += add
                remaining -= add
            # Any rounding/room remainder is distributed deterministically.
            for m in uncapped:
                if remaining <= 1e-12:
                    break
                add = min(room[m] - max(0.0, weights[m] - (caps[m] - room[m])), remaining)
                if add > 0:
                    weights[m] += add
                    remaining -= add

        # GFS family cap.
        gfs_total = weights["gfs013"] + weights["gfs_seamless"]
        if gfs_total > gfs_cap + 1e-12:
            removed = gfs_total - gfs_cap
            scale = gfs_cap / gfs_total
            weights["gfs013"] *= scale
            weights["gfs_seamless"] *= scale
            flags["gfs013"]["family"] = True
            flags["gfs_seamless"]["family"] = True
            recipients = [m for m in MODELS if FAMILY[m] != "GFS" and weights[m] < caps[m] - 1e-12]
            room = {m: caps[m] - weights[m] for m in recipients}
            if sum(room.values()) + 1e-12 < removed:
                raise ValueError("GFS cap redistribution cannot be absorbed")
            base_total = sum(max(weights[m], 1e-12) for m in recipients)
            remaining = removed
            for m in recipients:
                add = min(room[m], removed * max(weights[m], 1e-12) / base_total)
                weights[m] += add
                remaining -= add
            for m in recipients:
                if remaining <= 1e-12:
                    break
                add = min(caps[m] - weights[m], remaining)
                weights[m] += add
                remaining -= add
            changed = True

        total = sum(weights.values())
        if abs(total - 1.0) > 1e-12:
            # Redistribute small residual without violating caps.
            residual = 1.0 - total
            recipients = [m for m in MODELS if residual < 0 or weights[m] < caps[m] - 1e-12]
            if not recipients:
                raise ValueError("unable to normalize capped weights")
            if residual > 0:
                room_total = sum(caps[m] - weights[m] for m in recipients)
                for m in recipients:
                    weights[m] += residual * (caps[m] - weights[m]) / room_total
            else:
                positive_total = sum(weights[m] for m in recipients)
                for m in recipients:
                    weights[m] += residual * weights[m] / positive_total
            changed = True

        if not changed:
            break
    else:
        raise ValueError("cap algorithm did not converge")

    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise AssertionError("weights do not sum to one")
    if any(v > individual_cap + 1e-9 for v in weights.values()):
        raise AssertionError("individual cap violated")
    if weights["gfs013"] + weights["gfs_seamless"] > gfs_cap + 1e-9:
        raise AssertionError("GFS family cap violated")
    if weights["nbm"] > _nbm_cap(nbm_completed_dates) + 1e-9:
        raise AssertionError("NBM cap violated")
    return weights, flags


def compute_weights(
    *,
    stage_state: StageState,
    score_summaries: Mapping[str, Mapping[str, ScoreSummary]],
    available: Mapping[str, bool],
    eligible: Mapping[str, bool],
    nbm_completed_dates: int,
    eta: float = 1.0,
) -> dict:
    def raw_for_stage(stage: str) -> tuple[dict[str, float], dict[str, float]]:
        summaries = score_summaries.get(stage, {})
        ready_losses = [s.shrunk_log_loss for m, s in summaries.items() if m in MODELS and s.ready and s.shrunk_log_loss is not None]
        best = min(ready_losses) if ready_losses else None
        multipliers: dict[str, float] = {}
        raw: dict[str, float] = {}
        for m in MODELS:
            summary = summaries.get(m)
            if summary and summary.ready and summary.shrunk_log_loss is not None and best is not None:
                mult = exp(-eta * (summary.shrunk_log_loss - best))
            else:
                mult = 1.0
            multipliers[m] = mult
            raw[m] = STAGE_PRIORS[stage][m] * mult if available.get(m, False) and eligible.get(m, False) else 0.0
        return raw, multipliers

    raw_current, mult_current = raw_for_stage(stage_state.stage_id)
    if stage_state.transition_from_stage and stage_state.transition_alpha < 1.0:
        raw_previous, mult_previous = raw_for_stage(stage_state.transition_from_stage)
        a = stage_state.transition_alpha
        raw = {m: (1 - a) * raw_previous[m] + a * raw_current[m] for m in MODELS}
        multipliers = {m: (1 - a) * mult_previous[m] + a * mult_current[m] for m in MODELS}
        stage_prior = {m: (1 - a) * STAGE_PRIORS[stage_state.transition_from_stage][m] + a * STAGE_PRIORS[stage_state.stage_id][m] for m in MODELS}
    else:
        raw = raw_current
        multipliers = mult_current
        stage_prior = dict(STAGE_PRIORS[stage_state.stage_id])

    weights, flags = _apply_caps(raw, nbm_completed_dates=nbm_completed_dates)
    return {
        "stage": stage_state,
        "stage_prior": stage_prior,
        "reliability_multiplier": multipliers,
        "pre_cap": _normalize(raw),
        "weights": weights,
        "cap_flags": flags,
    }


def fixed_weights(*, available: Mapping[str, bool], eligible: Mapping[str, bool], nbm_completed_dates: int) -> dict[str, float]:
    raw = {m: FIXED_PRIOR[m] if available.get(m, False) and eligible.get(m, False) else 0.0 for m in MODELS}
    return _apply_caps(raw, nbm_completed_dates=nbm_completed_dates)[0]


def stage_prior_weights(*, stage_state: StageState, available: Mapping[str, bool], eligible: Mapping[str, bool], nbm_completed_dates: int) -> dict[str, float]:
    if stage_state.transition_from_stage and stage_state.transition_alpha < 1.0:
        a = stage_state.transition_alpha
        raw = {m: ((1-a)*STAGE_PRIORS[stage_state.transition_from_stage][m] + a*STAGE_PRIORS[stage_state.stage_id][m]) if available.get(m, False) and eligible.get(m, False) else 0.0 for m in MODELS}
    else:
        raw = {m: STAGE_PRIORS[stage_state.stage_id][m] if available.get(m, False) and eligible.get(m, False) else 0.0 for m in MODELS}
    return _apply_caps(raw, nbm_completed_dates=nbm_completed_dates)[0]
