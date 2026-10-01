"""Deterministic offline scores and hypothetical buy-and-hold replay primitives.

No network, account connection, or order submission is implemented. Fee schedules
are named input scenarios, never inferred from today's exchange schedule. Grade C
prices can be screened for sensitivity but cannot generate a filled-trade ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from math import erf, exp, fsum, isfinite, pi, sqrt
from typing import Iterable

from .domain import _aware, require_as_of


Money = Decimal | str | int
ZERO = Decimal("0")
ONE = Decimal("1")
SIMULATION_LABEL = "Historical simulation only; not actual account gains."


def _decimal(value: Money, name: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError(f"{name} must be Decimal, a decimal string, or an integer")
    try:
        number = Decimal(value)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"{name} must be a finite decimal number") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    return number


def _pairs(predictions: Iterable[float], outcomes: Iterable[float]) -> list[tuple[float, float]]:
    predictions, outcomes = list(predictions), list(outcomes)
    if not predictions or len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have the same nonzero length")
    pairs = list(zip(predictions, outcomes))
    if not all(isfinite(p) and isfinite(y) for p, y in pairs):
        raise ValueError("scores require finite predictions and outcomes")
    return pairs


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = _pairs(probabilities, outcomes)
    if any(not 0 <= p <= 1 or y not in (0, 1) for p, y in pairs):
        raise ValueError("Brier score requires probabilities and binary outcomes")
    return fsum((p - y) ** 2 for p, y in pairs) / len(pairs)


def mean_absolute_error(predictions: Iterable[float], outcomes: Iterable[float]) -> float:
    pairs = _pairs(predictions, outcomes)
    return fsum(abs(p - y) for p, y in pairs) / len(pairs)


def mean_bias(predictions: Iterable[float], outcomes: Iterable[float]) -> float:
    """Mean predicted minus observed temperature, in the inputs' common unit."""
    pairs = _pairs(predictions, outcomes)
    return fsum(p - y for p, y in pairs) / len(pairs)


def gaussian_crps(mean: float, sd: float, outcome: float) -> float:
    """Continuous Gaussian CRPS in Fahrenheit if inputs are Fahrenheit.

    This scores the continuous distribution; label precision must be disclosed.
    It is not a discrete CRPS score on rounded Fahrenheit outcomes.
    """
    if not all(isfinite(x) for x in (mean, sd, outcome)) or sd <= 0:
        raise ValueError("CRPS requires finite parameters and positive sd")
    z = (outcome - mean) / sd
    density = exp(-0.5 * z * z) / sqrt(2 * pi)
    return sd * (z * erf(z / sqrt(2)) + 2 * density - 1 / sqrt(pi))


@dataclass(frozen=True)
class FeeScenario:
    """Rounded quadratic per-order fee scenario: ceil(rate*n*c*(1-c), quantum).

    No rate is provided by default. Use a documented historical schedule only
    after independent verification, or retain historical_verified=False. This
    formula does not represent every possible historical or market-specific fee.
    """

    name: str
    rate: Money
    provenance: str
    rounding: Money = Decimal("0.01")
    historical_verified: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.provenance.strip():
            raise ValueError("fee scenario needs a name and provenance")
        rate, rounding = _decimal(self.rate, "rate"), _decimal(self.rounding, "rounding")
        if not ZERO <= rate <= ONE or rounding <= ZERO:
            raise ValueError("fee rate must be in [0,1] and rounding must be positive")
        object.__setattr__(self, "rate", rate)
        object.__setattr__(self, "rounding", rounding)

    def entry_fee(self, price: Money, quantity: int) -> Decimal:
        price = _price(price)
        _quantity(quantity)
        raw = self.rate * quantity * price * (ONE - price)
        return (raw / self.rounding).to_integral_value(rounding=ROUND_CEILING) * self.rounding


def _price(value: Money) -> Decimal:
    price = _decimal(value, "price")
    if not ZERO < price < ONE:
        raise ValueError("entry price must be strictly between $0 and $1")
    return price


def _quantity(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("quantity must be a positive integer")


@dataclass(frozen=True)
class ExecutionEvidence:
    """Historical price evidence; observed_at is its time, not retrieval time.

    A = historical quotes with size; B = bid/ask summaries, assumed fill;
    C = prints or aggregate prices, sensitivity only. Grade A still does not prove
    an actual fill. Price must already include modeled spread/slippage, once.
    """

    grade: str
    source_id: str
    observed_at: datetime
    available_at: datetime
    price_convention: str
    available_size: int | None = None
    block_trade: bool = False

    def __post_init__(self) -> None:
        if self.grade not in ("A", "B", "C"):
            raise ValueError("evidence grade must be A, B, or C")
        if not self.source_id.strip() or not self.price_convention.strip():
            raise ValueError("price evidence needs source and price convention")
        _aware(self.observed_at, "observed_at")
        _aware(self.available_at, "available_at")
        if self.available_size is not None:
            _quantity(self.available_size)


@dataclass(frozen=True)
class ReplayPolicy:
    target_return: Money = Decimal("0.10")
    max_quote_age_seconds: int = 300
    execution_delay_seconds: int = 0
    max_quantity: int = 1
    settlement_cost_per_contract: Money = ZERO

    def __post_init__(self) -> None:
        target = _decimal(self.target_return, "target_return")
        settlement = _decimal(self.settlement_cost_per_contract, "settlement_cost")
        if target < Decimal("0.10"):
            raise ValueError("research screen must require at least 10% expected net ROI")
        if settlement < ZERO:
            raise ValueError("settlement cost cannot be negative")
        for name in ("max_quote_age_seconds", "execution_delay_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        _quantity(self.max_quantity)
        object.__setattr__(self, "target_return", target)
        object.__setattr__(self, "settlement_cost_per_contract", settlement)


@dataclass(frozen=True)
class ReplayDecision:
    decision_id: str
    status: str
    reason: str
    information_cutoff: datetime
    decision_at: datetime
    dataset_version: str
    model_version: str
    side: str
    purchased_probability: Decimal
    quantity: int
    entry_price: Decimal
    entry_fee: Decimal
    settlement_cost: Decimal
    entry_outlay: Decimal
    expected_net_profit: Decimal
    expected_net_return: Decimal
    target_return: Decimal
    evidence_grade: str
    price_source: str
    price_convention: str
    fee_scenario: str
    fee_provenance: str
    historical_fees_verified: bool
    simulation_label: str = SIMULATION_LABEL


def evaluate_purchase(
    *,
    decision_id: str,
    information_cutoff: datetime,
    decision_at: datetime,
    yes_probability: float | Decimal,
    side: str,
    quantity: int,
    entry_price: Money,
    fees: FeeScenario,
    evidence: ExecutionEvidence,
    dataset_version: str,
    model_version: str,
    policy: ReplayPolicy | None = None,
) -> ReplayDecision:
    """Screen one hypothetical purchase at the simulated entry decision time.

    information_cutoff is the forecast's frozen input cutoff; decision_at is the
    post-delay entry time. A caller must separately validate every model input as
    of information_cutoff. Size, duplicate entries and shared-day exposure across
    multiple decisions require an outer replay coordinator. This primitive must
    not be presented as a complete portfolio/event replay engine.
    """
    policy = policy or ReplayPolicy()
    cutoff = _aware(information_cutoff, "information_cutoff")
    decision = _aware(decision_at, "decision_at")
    if cutoff > decision:
        raise ValueError("forecast information cutoff follows entry decision")
    if not all(value.strip() for value in (decision_id, dataset_version, model_version)):
        raise ValueError("decision, dataset and model identifiers are required")
    if side not in ("YES", "NO"):
        raise ValueError("side must be YES or NO")
    probability = _decimal(str(yes_probability), "yes_probability")
    if not ZERO <= probability <= ONE:
        raise ValueError("yes_probability must lie in [0,1]")
    probability = probability if side == "YES" else ONE - probability
    price = _price(entry_price)
    _quantity(quantity)
    fee = fees.entry_fee(price, quantity)
    settlement = policy.settlement_cost_per_contract * quantity
    outlay = quantity * price + fee
    profit = quantity * probability - outlay - settlement
    roi = profit / outlay
    reason = "EXPECTED_RETURN_BELOW_TARGET"
    status = "SKIPPED"
    try:
        require_as_of(evidence.observed_at, evidence.available_at, decision)
        evidence_timely = True
    except ValueError:
        evidence_timely = False
    quote_age = (decision - _aware(evidence.observed_at, "observed_at")).total_seconds()
    if not evidence_timely:
        reason = "PRICE_NOT_AVAILABLE_AS_OF_DECISION"
    elif quote_age > policy.max_quote_age_seconds:
        reason = "STALE_PRICE"
    elif (decision - cutoff).total_seconds() < policy.execution_delay_seconds:
        reason = "EXECUTION_DELAY_NOT_MET"
    elif evidence.block_trade:
        reason = "BLOCK_TRADE_EXCLUDED"
    elif quantity > policy.max_quantity:
        reason = "QUANTITY_LIMIT_EXCEEDED"
    elif evidence.grade == "C":
        reason = "PRICE_SENSITIVITY_ONLY"
    elif evidence.grade == "A" and evidence.available_size is None:
        reason = "GRADE_A_REQUIRES_QUOTED_SIZE"
    elif evidence.available_size is not None and quantity > evidence.available_size:
        reason = "INSUFFICIENT_QUOTED_SIZE"
    elif roi >= policy.target_return:
        status = "ACCEPTED"
        reason = "EXECUTION_AWARE_SIMULATION" if evidence.grade == "A" else "ASSUMED_FILL_SCENARIO"
    return ReplayDecision(
        decision_id, status, reason, cutoff, decision, dataset_version, model_version,
        side, probability, quantity, price, fee, settlement, outlay, profit, roi,
        policy.target_return, evidence.grade, evidence.source_id, evidence.price_convention,
        fees.name, fees.provenance, fees.historical_verified,
    )


@dataclass(frozen=True)
class SimulatedSettlement:
    decision_id: str
    side: str
    quantity: int
    payout: Decimal
    entry_outlay: Decimal
    settlement_cost: Decimal
    net_profit: Decimal
    net_return: Decimal
    evidence_grade: str
    simulation_label: str = SIMULATION_LABEL


def settle_purchase(decision: ReplayDecision, yes_outcome: int) -> SimulatedSettlement:
    """Settle an accepted hypothetical purchase; cancellation/refunds unsupported.

    yes_outcome must be the actual settled market result, not a model or a proxy
    weather label. Canceled/exceptional outcomes require a separate explicit path.
    """
    if decision.status != "ACCEPTED":
        raise ValueError("cannot settle a skipped decision")
    if isinstance(yes_outcome, bool) or not isinstance(yes_outcome, int) or yes_outcome not in (0, 1):
        raise ValueError("yes_outcome must be integer 0 or 1")
    side_won = yes_outcome if decision.side == "YES" else 1 - yes_outcome
    payout = Decimal(decision.quantity * side_won)
    profit = payout - decision.entry_outlay - decision.settlement_cost
    return SimulatedSettlement(
        decision.decision_id, decision.side, decision.quantity, payout,
        decision.entry_outlay, decision.settlement_cost, profit,
        profit / decision.entry_outlay, decision.evidence_grade,
    )


def summarize_settlements(rows: Iterable[SimulatedSettlement]) -> dict[str, object]:
    """Aggregate capital-weighted ROI separately from the mean trade ROI.

    Empty ledgers have undefined (None) returns, not a fabricated zero return.
    No independence, confidence interval, drawdown or account-return claim is made.
    """
    rows = list(rows)
    if len({row.decision_id for row in rows}) != len(rows):
        raise ValueError("duplicate decision in settlement ledger")
    profit = sum((row.net_profit for row in rows), ZERO)
    outlay = sum((row.entry_outlay for row in rows), ZERO)
    return {
        "trade_count": len(rows),
        "total_net_profit": profit,
        "total_entry_outlay": outlay,
        "capital_weighted_return": profit / outlay if rows else None,
        "mean_trade_return": sum((row.net_return for row in rows), ZERO) / len(rows) if rows else None,
        "evidence_grades": sorted({row.evidence_grade for row in rows}),
        "simulation_label": SIMULATION_LABEL,
    }
