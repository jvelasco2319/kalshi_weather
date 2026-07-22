from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from math import exp, log
from typing import Callable, Iterable, Mapping, Sequence

from scipy.stats import beta as beta_distribution

D = Decimal
ZERO = D("0")
ONE = D("1")
CENTICENT = D("0.0001")

CANONICAL_MODELS = ("ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm")
MODEL_FAMILY = {
    "ecmwf_ifs": "ECMWF",
    "gfs013": "GFS",
    "gfs_seamless": "GFS",
    "nam": "NAM",
    "nbm": "NBM",
}
PRIOR_WEIGHTS = {
    "ecmwf_ifs": 0.20,
    "gfs013": 0.25,
    "gfs_seamless": 0.20,
    "nam": 0.15,
    "nbm": 0.20,
}


def dec(value) -> Decimal:
    return value if isinstance(value, Decimal) else D(str(value))


def ceil_increment(value, increment=CENTICENT) -> Decimal:
    value = dec(value)
    increment = dec(increment)
    return (value / increment).to_integral_value(rounding=ROUND_CEILING) * increment


@dataclass(frozen=True)
class ForecastPoint:
    point_id: str
    model_key: str
    run_id: str
    valid_time: datetime
    temperature_f: float
    source_available_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class ResidualRecord:
    target_date: str
    residual_f: float
    settlement_gap_f: float
    age_target_dates: int


@dataclass(frozen=True)
class ModelDistribution:
    model_key: str
    bracket_counts: tuple[float, ...]
    effective_sample_size: float
    corrected_point_f: float
    component_mean_yes: tuple[float, ...]
    component_safe_yes: tuple[float, ...]
    component_mean_no: tuple[float, ...]
    component_safe_no: tuple[float, ...]


def validate_model_set(model_keys: Iterable[str]) -> tuple[str, ...]:
    keys = tuple(model_keys)
    if tuple(keys) != CANONICAL_MODELS:
        raise ValueError(f"strategy model set must be exactly {CANONICAL_MODELS}")
    return keys


def future_max_asof(
    points: Sequence[ForecastPoint],
    evaluated_at: datetime,
    day_end: datetime,
) -> float:
    eligible = [
        p.temperature_f
        for p in points
        if p.source_available_at <= evaluated_at
        and p.received_at <= evaluated_at
        and evaluated_at <= p.valid_time <= day_end
    ]
    if not eligible:
        raise ValueError("no eligible remaining forecast points")
    return max(eligible)


def live_state(future_max_f: float | None, observed_max_f: float | None) -> float:
    if future_max_f is None and observed_max_f is None:
        raise ValueError("forecast and observation are both missing")
    if future_max_f is None:
        return float(observed_max_f)
    if observed_max_f is None:
        return float(future_max_f)
    return max(float(future_max_f), float(observed_max_f))


def recency_weights(ages: Sequence[int], half_life: float = 21.0) -> list[float]:
    if not ages or half_life <= 0 or any(a < 0 for a in ages):
        raise ValueError("invalid ages or half-life")
    raw = [2.0 ** (-float(a) / half_life) for a in ages]
    total = sum(raw)
    return [x / total for x in raw]


def effective_sample_size(normalized_weights: Sequence[float]) -> float:
    if not normalized_weights:
        raise ValueError("weights required")
    total = sum(normalized_weights)
    if abs(total - 1.0) > 1e-9:
        raise ValueError("weights must sum to one")
    return 1.0 / sum(w * w for w in normalized_weights)


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    if len(values) != len(weights) or not values or not (0 <= q <= 1):
        raise ValueError("invalid weighted quantile input")
    pairs = sorted(zip(values, weights), key=lambda x: x[0])
    total = sum(w for _, w in pairs)
    if total <= 0:
        raise ValueError("positive weight required")
    threshold = q * total
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= threshold:
            return float(value)
    return float(pairs[-1][0])


def dirichlet_bounds(
    counts: Sequence[float], alpha: float = 0.5, q: float = 0.10
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    if len(counts) < 2 or alpha <= 0 or not (0 < q < 0.5):
        raise ValueError("invalid Dirichlet parameters")
    if any(c < 0 for c in counts):
        raise ValueError("counts must be nonnegative")
    n = float(sum(counts))
    if n <= 0:
        raise ValueError("positive evidence required")
    j = len(counts)
    denominator = n + j * alpha
    mean_yes, safe_yes, mean_no, safe_no = [], [], [], []
    for k in counts:
        ay = float(k) + alpha
        by = n - float(k) + (j - 1) * alpha
        an, bn = by, ay
        mean_yes.append(ay / denominator)
        safe_yes.append(float(beta_distribution.ppf(q, ay, by)))
        mean_no.append(an / denominator)
        safe_no.append(float(beta_distribution.ppf(q, an, bn)))
    return tuple(mean_yes), tuple(safe_yes), tuple(mean_no), tuple(safe_no)


def build_model_distribution(
    model_key: str,
    raw_live_state_f: float,
    observed_max_f: float | None,
    residual_records: Sequence[ResidualRecord],
    quantizer: Callable[[float], int],
    bracket_count: int,
    half_life: float = 21.0,
) -> ModelDistribution:
    if model_key not in CANONICAL_MODELS:
        raise ValueError("non-canonical model")
    if not residual_records:
        raise ValueError("residual history required")
    weights = recency_weights([r.age_target_dates for r in residual_records], half_life)
    neff = effective_sample_size(weights)
    counts = [0.0] * bracket_count
    residuals = [r.residual_f for r in residual_records]
    correction = weighted_quantile(residuals, weights, 0.5)
    corrected_point = live_state(raw_live_state_f + correction, observed_max_f)
    for record, weight in zip(residual_records, weights):
        physical = raw_live_state_f + record.residual_f
        if observed_max_f is not None:
            physical = max(physical, observed_max_f)
        official = physical + record.settlement_gap_f
        bracket = int(quantizer(official))
        if not 0 <= bracket < bracket_count:
            raise ValueError("quantizer returned invalid bracket")
        counts[bracket] += weight * neff
    means_y, safe_y, means_n, safe_n = dirichlet_bounds(counts)
    return ModelDistribution(
        model_key=model_key,
        bracket_counts=tuple(counts),
        effective_sample_size=neff,
        corrected_point_f=corrected_point,
        component_mean_yes=means_y,
        component_safe_yes=safe_y,
        component_mean_no=means_n,
        component_safe_no=safe_n,
    )


def nbm_maturity_cap(completed_dates: int) -> float:
    if completed_dates < 10:
        return 0.0
    if completed_dates < 30:
        return 0.10
    if completed_dates < 60:
        return 0.20
    return 0.25


def _waterfill(raw: Mapping[str, float], caps: Mapping[str, float], total: float = 1.0) -> dict[str, float]:
    active = {k for k, v in raw.items() if v > 0 and caps.get(k, 1.0) > 0}
    out = {k: 0.0 for k in raw}
    remaining = total
    while active:
        raw_total = sum(raw[k] for k in active)
        if raw_total <= 0:
            share = remaining / len(active)
            proposed = {k: share for k in active}
        else:
            proposed = {k: remaining * raw[k] / raw_total for k in active}
        capped = [k for k in active if proposed[k] > caps.get(k, 1.0) + 1e-12]
        if not capped:
            for k in active:
                out[k] = proposed[k]
            remaining = 0.0
            break
        for k in capped:
            cap = caps.get(k, 1.0)
            out[k] = cap
            remaining -= cap
            active.remove(k)
        if remaining < -1e-10:
            raise ValueError("infeasible caps")
    if remaining > 1e-9:
        raise ValueError("caps cannot allocate total weight")
    return out


def reliability_weights(
    completed_dates: Mapping[str, int],
    mean_log_loss: Mapping[str, float | None],
    bracket_count: int,
    prior_weights: Mapping[str, float] = PRIOR_WEIGHTS,
    kappa: float = 30.0,
    eta: float = 1.0,
) -> dict[str, float]:
    eligible = {}
    scores = {}
    uniform_loss = log(bracket_count)
    for model in CANONICAL_MODELS:
        n = int(completed_dates.get(model, 0))
        if model == "nbm":
            is_eligible = n >= 10
        else:
            is_eligible = n >= 30
        eligible[model] = is_eligible
        observed = mean_log_loss.get(model)
        if observed is None or n <= 0:
            observed = uniform_loss
        scores[model] = (n * float(observed) + kappa * uniform_loss) / (n + kappa)
    if sum(eligible.values()) < 4:
        raise ValueError("at least four eligible feeds required")
    families = {MODEL_FAMILY[m] for m, ok in eligible.items() if ok}
    if len(families) < 3:
        raise ValueError("at least three eligible families required")
    best = min(scores[m] for m, ok in eligible.items() if ok)
    raw = {
        m: (prior_weights[m] * exp(-eta * (scores[m] - best)) if eligible[m] else 0.0)
        for m in CANONICAL_MODELS
    }
    caps = {m: 0.35 for m in CANONICAL_MODELS}
    caps["nbm"] = min(caps["nbm"], nbm_maturity_cap(completed_dates.get("nbm", 0)))
    weights = _waterfill(raw, caps)

    gfs_models = [m for m in CANONICAL_MODELS if MODEL_FAMILY[m] == "GFS"]
    gfs_total = sum(weights[m] for m in gfs_models)
    family_cap = 0.45
    if gfs_total > family_cap + 1e-12:
        scale = family_cap / gfs_total
        excess = gfs_total - family_cap
        for m in gfs_models:
            weights[m] *= scale
        outside = [m for m in CANONICAL_MODELS if m not in gfs_models and weights[m] > 0]
        capacity = {m: caps[m] - weights[m] for m in outside}
        raw_out = {m: raw[m] for m in outside}
        if sum(capacity.values()) + 1e-12 < excess:
            raise ValueError("family cap redistribution infeasible")
        increments = _waterfill(raw_out, capacity, total=excess)
        for m, inc in increments.items():
            weights[m] += inc
    total = sum(weights.values())
    return {m: weights[m] / total for m in CANONICAL_MODELS}


def combine_distributions(
    distributions: Mapping[str, ModelDistribution],
    weights: Mapping[str, float],
    alpha: float = 0.5,
    q: float = 0.10,
) -> dict[str, tuple[float, ...] | float]:
    positive = [m for m, w in weights.items() if w > 0]
    if not positive:
        raise ValueError("positive model weight required")
    bracket_count = len(distributions[positive[0]].bracket_counts)
    if any(len(distributions[m].bracket_counts) != bracket_count for m in positive):
        raise ValueError("bracket count mismatch")
    p_raw = [0.0] * bracket_count
    component_safe_y = [0.0] * bracket_count
    component_safe_n = [0.0] * bracket_count
    for m in positive:
        dist = distributions[m]
        w = weights[m]
        total = sum(dist.bracket_counts)
        for j in range(bracket_count):
            p_raw[j] += w * dist.bracket_counts[j] / total
            component_safe_y[j] += w * dist.component_safe_yes[j]
            component_safe_n[j] += w * dist.component_safe_no[j]
    n_mix = min(distributions[m].effective_sample_size for m in positive)
    mix_counts = [p * n_mix for p in p_raw]
    mean_y, safe_y_mix, mean_n, safe_n_mix = dirichlet_bounds(mix_counts, alpha, q)
    safe_y = tuple(min(a, b) for a, b in zip(safe_y_mix, component_safe_y))
    safe_n = tuple(min(a, b) for a, b in zip(safe_n_mix, component_safe_n))
    return {
        "effective_sample_size": n_mix,
        "posterior_mean_yes": mean_y,
        "safe_yes": safe_y,
        "posterior_mean_no": mean_n,
        "safe_no": safe_n,
        "raw_mixture_frequency": tuple(p_raw),
    }


def model_spread(raw_live_states: Mapping[str, float]) -> float:
    values = [float(raw_live_states[m]) for m in CANONICAL_MODELS if m in raw_live_states]
    if len(values) < 4:
        raise ValueError("at least four models required")
    families = {MODEL_FAMILY[m] for m in raw_live_states if m in MODEL_FAMILY}
    if len(families) < 3:
        raise ValueError("at least three families required")
    return max(values) - min(values)


def drift_flag(recent_median: float, long_median: float) -> bool:
    absolute = abs(float(recent_median) - float(long_median)) >= 1.5
    reversal = (
        recent_median * long_median < 0
        and abs(recent_median) >= 1.0
        and abs(long_median) >= 1.0
    )
    return bool(absolute or reversal)


@dataclass(frozen=True)
class FeeSchedule:
    taker_rate: Decimal = D("0.07")
    maker_rate: Decimal = D("0.0175")
    taker_multiplier: Decimal = D("1")
    maker_multiplier: Decimal = D("0")


def fee(count: int, price, role: str, schedule: FeeSchedule = FeeSchedule()) -> Decimal:
    c = dec(count)
    p = dec(price)
    if c <= 0 or not (ZERO < p < ONE):
        raise ValueError("invalid count or price")
    if role == "taker":
        rate, multiplier = schedule.taker_rate, schedule.taker_multiplier
    elif role == "maker":
        rate, multiplier = schedule.maker_rate, schedule.maker_multiplier
    else:
        raise ValueError("role must be maker or taker")
    position = c * p
    raw = multiplier * rate * c * p * (ONE - p)
    return ceil_increment(position + raw) - position


def trade_roi(
    probability,
    count: int,
    price,
    role: str,
    slippage=ZERO,
    schedule: FeeSchedule = FeeSchedule(),
) -> Decimal:
    q = dec(probability)
    c = dec(count)
    p = dec(price)
    s = dec(slippage)
    if not (ZERO <= q <= ONE):
        raise ValueError("invalid probability")
    cost = c * p + fee(count, p, role, schedule) + s
    return (c * q - cost) / cost


def max_price(
    probability,
    count: int,
    role: str,
    hurdle,
    levels: Iterable[Decimal],
    slippage=ZERO,
    schedule: FeeSchedule = FeeSchedule(),
):
    passing = [
        dec(p)
        for p in sorted({dec(x) for x in levels})
        if trade_roi(probability, count, p, role, slippage, schedule) >= dec(hurdle)
    ]
    return max(passing) if passing else None


def full_kelly_fraction(probability, all_in_cost_per_contract) -> Decimal:
    q = dec(probability)
    k = dec(all_in_cost_per_contract)
    if not (ZERO <= q <= ONE) or not (ZERO < k < ONE):
        raise ValueError("invalid probability or cost")
    return max(ZERO, (q - k) / (ONE - k))


def validate_trade_rows(rows: Sequence[Mapping]) -> None:
    if rows and any("count_fp" not in r or dec(r["count_fp"]) <= 0 for r in rows):
        raise ValueError("nonempty trade pull contains missing/nonpositive count_fp")


class BookSequence:
    def __init__(self) -> None:
        self.valid = False
        self.sequence: int | None = None

    def snapshot(self, sequence: int) -> None:
        self.valid = True
        self.sequence = int(sequence)

    def delta(self, sequence: int) -> None:
        if not self.valid or self.sequence is None or int(sequence) != self.sequence + 1:
            self.valid = False
            raise ValueError("orderbook sequence gap")
        self.sequence = int(sequence)


def event_outcome_pnl(
    bracket_count: int,
    positions: Sequence[Mapping[str, object]],
) -> tuple[Decimal, ...]:
    outcomes = [ZERO for _ in range(bracket_count)]
    for pos in positions:
        bracket = int(pos["bracket_index"])
        side = str(pos["side"])
        count = dec(pos["count"])
        cost = dec(pos["all_in_cost"])
        for outcome in range(bracket_count):
            win = outcome == bracket
            if side == "yes":
                payoff = count if win else ZERO
            elif side == "no":
                payoff = count if not win else ZERO
            else:
                raise ValueError("invalid side")
            outcomes[outcome] += payoff - cost
    return tuple(outcomes)
