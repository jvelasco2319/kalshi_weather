"""Selective V3 replay policy for normalized one-minute Kalshi candles.

Candles remain grade-B aggregate quote evidence.  They support a conservative
assumed-fill scenario, never a claim of historical depth or an actual fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from .evaluation import (
    ExecutionEvidence, FeeScenario, ReplayDecision, ReplayPolicy, evaluate_purchase,
)


def _decimal(value, name: str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError(f"{name} must retain fixed-point decimal semantics")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SelectiveControls:
    target_return: Decimal | str = "0.10"
    uncertainty_buffer: Decimal | str = "0.05"
    minimum_candle_volume: Decimal | str = "10"
    maximum_price_age_minutes: int = 5
    maximum_spread_cents: int = 10
    entry_price_floor_cents: int = 10
    entry_price_ceiling_cents: int = 90
    allowed_sides: tuple[str, ...] = ("YES", "NO")

    def __post_init__(self) -> None:
        target = _decimal(self.target_return, "target_return")
        buffer = _decimal(self.uncertainty_buffer, "uncertainty_buffer")
        volume = _decimal(self.minimum_candle_volume, "minimum_candle_volume")
        if target < Decimal("0.10"):
            raise ValueError("Selective policy cannot weaken the 10% target")
        if not Decimal("0") <= buffer < Decimal("0.50") or volume < 0:
            raise ValueError("Invalid uncertainty or volume control")
        for name in ("maximum_price_age_minutes", "maximum_spread_cents",
                     "entry_price_floor_cents", "entry_price_ceiling_cents"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not 0 < self.entry_price_floor_cents < self.entry_price_ceiling_cents < 100:
            raise ValueError("Entry price band must lie strictly inside 0..100 cents")
        sides = tuple(self.allowed_sides)
        if not sides or len(set(sides)) != len(sides) or any(side not in {"YES", "NO"} for side in sides):
            raise ValueError("Invalid allowed-side policy")
        object.__setattr__(self, "target_return", target)
        object.__setattr__(self, "uncertainty_buffer", buffer)
        object.__setattr__(self, "minimum_candle_volume", volume)
        object.__setattr__(self, "allowed_sides", sides)


@dataclass(frozen=True)
class MinuteQuote:
    ticker: str
    observed_at: datetime
    yes_bid: Decimal
    yes_ask: Decimal
    volume_contracts: Decimal
    source_sha256: str
    source_endpoint_type: str

    def __post_init__(self) -> None:
        if not self.ticker or not self.source_sha256 or self.source_endpoint_type != "historical_market_candlesticks_1m":
            raise ValueError("Minute quote lacks one-minute historical provenance")
        observed = _utc(self.observed_at, "observed_at")
        bid, ask = _decimal(self.yes_bid, "yes_bid"), _decimal(self.yes_ask, "yes_ask")
        volume = _decimal(self.volume_contracts, "volume_contracts")
        if not Decimal("0") <= bid <= ask <= Decimal("1") or volume < 0:
            raise ValueError("Minute quote is crossed or outside the contract price range")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "yes_bid", bid)
        object.__setattr__(self, "yes_ask", ask)
        object.__setattr__(self, "volume_contracts", volume)

    @property
    def spread_cents(self) -> Decimal:
        return (self.yes_ask - self.yes_bid) * 100

    def purchase_price(self, side: str) -> Decimal:
        if side == "YES":
            return self.yes_ask
        if side == "NO":
            return Decimal("1") - self.yes_bid
        raise ValueError("side must be YES or NO")


def quote_from_normalized_candle(row: dict) -> MinuteQuote:
    if row.get("period_minutes") != 1 or row.get("evidence_grade") != "B_aggregated_quote":
        raise ValueError("V3 minute policy requires normalized one-minute grade-B candles")
    if row.get("historical_depth_available") is not False or row.get("hypothetical_fill_supported") is not False:
        raise ValueError("Minute candles may not be promoted to historical depth or fill evidence")
    if row.get("yes_bid_close") is None or row.get("yes_ask_close") is None:
        raise ValueError("Minute candle lacks a closing bid/ask pair")
    if row.get("volume_contracts") is None:
        raise ValueError("Minute candle lacks volume")
    return MinuteQuote(
        ticker=row["ticker"],
        observed_at=datetime.fromtimestamp(row["end_period_ts"], timezone.utc),
        yes_bid=_decimal(row["yes_bid_close"], "yes_bid_close"),
        yes_ask=_decimal(row["yes_ask_close"], "yes_ask_close"),
        volume_contracts=_decimal(row["volume_contracts"], "volume_contracts"),
        source_sha256=row["source_sha256"],
        source_endpoint_type=row["source_endpoint_type"],
    )


def latest_completed_quote(rows: Iterable[dict], ticker: str, decision_at: datetime) -> MinuteQuote:
    decision = _utc(decision_at, "decision_at")
    candidates = []
    for row in rows:
        if row.get("ticker") != ticker:
            continue
        stamp = row.get("end_period_ts")
        if type(stamp) is not int or stamp < 0:
            raise ValueError("Minute candle has an invalid timestamp")
        # Future rows are outside the as-of view and must not influence even
        # validation or candidate selection at this decision point.
        if datetime.fromtimestamp(stamp, timezone.utc) > decision:
            continue
        quote = quote_from_normalized_candle(row)
        candidates.append(quote)
    if not candidates:
        raise ValueError("No completed one-minute quote exists at the decision time")
    return max(candidates, key=lambda quote: quote.observed_at)


def screen_minute_purchase(
    *,
    decision_id: str,
    information_cutoff: datetime,
    decision_at: datetime,
    yes_probability: float | Decimal,
    side: str,
    quote: MinuteQuote,
    controls: SelectiveControls,
    fees: FeeScenario,
    dataset_version: str,
    model_version: str,
) -> ReplayDecision:
    """Apply all selective controls before the ordinary replay calculation."""
    cutoff = _utc(information_cutoff, "information_cutoff")
    decision = _utc(decision_at, "decision_at")
    if cutoff > decision:
        raise ValueError("forecast information cutoff follows entry decision")
    if not all(isinstance(value, str) and value.strip()
               for value in (decision_id, dataset_version, model_version)):
        raise ValueError("decision, dataset and model identifiers are required")
    probability = _decimal(str(yes_probability), "yes_probability")
    if not Decimal("0") <= probability <= Decimal("1"):
        raise ValueError("yes_probability must lie inside 0..1")
    price = quote.purchase_price(side)
    purchased_probability = probability if side == "YES" else Decimal("1") - probability
    conservative_purchased = max(Decimal("0"), purchased_probability - controls.uncertainty_buffer)
    conservative_yes = conservative_purchased if side == "YES" else Decimal("1") - conservative_purchased
    # Apply the registered selective controls before the ordinary purchase
    # calculation.  Historical binary contracts may legitimately publish 0/1
    # boundary quotes; those observations must be skipped rather than sent to
    # an ROI calculation where a zero outlay is undefined.
    reason = None
    if side not in controls.allowed_sides:
        reason = "SIDE_POLICY"
    elif quote.volume_contracts < controls.minimum_candle_volume:
        reason = "MINUTE_VOLUME_BELOW_CONTROL"
    elif quote.spread_cents > controls.maximum_spread_cents:
        reason = "MINUTE_SPREAD_ABOVE_CONTROL"
    elif not (Decimal(controls.entry_price_floor_cents) / 100
              <= price <= Decimal(controls.entry_price_ceiling_cents) / 100):
        reason = "ENTRY_PRICE_OUTSIDE_CONTROL_BAND"
    if reason is not None:
        zero = Decimal("0")
        return ReplayDecision(
            decision_id, "SKIPPED", reason,
            cutoff, decision, dataset_version, model_version, side,
            conservative_purchased, 1, price, zero, zero, zero, zero, zero,
            controls.target_return, "B", quote.source_sha256,
            "last completed one-minute candle close; assumed fill without depth",
            fees.name, fees.provenance, fees.historical_verified,
        )
    evidence = ExecutionEvidence(
        "B", quote.source_sha256, quote.observed_at, quote.observed_at,
        "last completed one-minute candle close; assumed fill without depth",
    )
    replay = ReplayPolicy(
        target_return=controls.target_return,
        max_quote_age_seconds=controls.maximum_price_age_minutes * 60,
        execution_delay_seconds=0,
        max_quantity=1,
    )
    result = evaluate_purchase(
        decision_id=decision_id, information_cutoff=cutoff,
        decision_at=decision, yes_probability=conservative_yes, side=side,
        quantity=1, entry_price=str(price), fees=fees, evidence=evidence,
        dataset_version=dataset_version, model_version=model_version, policy=replay,
    )
    return result
