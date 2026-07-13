from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from kalshi_weather.strategy_current.economics import FeeSchedule, Side, trade_economics
from kalshi_weather.strategy_current.probabilities import BracketProbability
from kalshi_weather.strategy_current.reason_codes import (
    NO_TRADE_BOOK_INVALID,
    NO_TRADE_ROI_BELOW_HURDLE,
    SHADOW_CANDIDATE_NO,
    SHADOW_CANDIDATE_YES,
)


@dataclass(frozen=True)
class MarketQuote:
    bracket_id: str
    market_ticker: str
    yes_ask: Decimal | None
    no_ask: Decimal | None
    yes_depth: Decimal | None = None
    no_depth: Decimal | None = None


@dataclass(frozen=True)
class TradeCandidate:
    market_ticker: str
    bracket_id: str
    side: Side
    quantity: int
    limit_price: Decimal
    conservative_probability: Decimal
    expected_roi: Decimal
    expected_value: Decimal
    reason_code: str

    def to_dict(self) -> dict[str, str | int]:
        payload = asdict(self)
        for key in ("limit_price", "conservative_probability", "expected_roi", "expected_value"):
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class DecisionResult:
    reason_code: str
    candidate: TradeCandidate | None
    evaluated_candidates: tuple[TradeCandidate, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "reason_code": self.reason_code,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "evaluated_candidates": [candidate.to_dict() for candidate in self.evaluated_candidates],
        }


def choose_shadow_candidate(
    probabilities: Sequence[BracketProbability],
    quotes: Mapping[str, MarketQuote],
    *,
    quantity: int,
    hurdle: Decimal,
    fee_schedule: FeeSchedule = FeeSchedule(),
) -> DecisionResult:
    candidates: list[TradeCandidate] = []
    saw_quote = False
    for probability in probabilities:
        quote = quotes.get(probability.bracket_id)
        if quote is None:
            continue
        saw_quote = True
        candidates.extend(
            _candidate_for_side(
                probability=probability,
                quote=quote,
                side="yes",
                quantity=quantity,
                hurdle=hurdle,
                fee_schedule=fee_schedule,
            )
        )
        candidates.extend(
            _candidate_for_side(
                probability=probability,
                quote=quote,
                side="no",
                quantity=quantity,
                hurdle=hurdle,
                fee_schedule=fee_schedule,
            )
        )
    if not saw_quote:
        return DecisionResult(NO_TRADE_BOOK_INVALID, None, ())
    passing = [candidate for candidate in candidates if candidate.expected_roi >= hurdle]
    if not passing:
        return DecisionResult(NO_TRADE_ROI_BELOW_HURDLE, None, tuple(candidates))
    selected = max(
        passing,
        key=lambda candidate: (
            candidate.expected_value,
            candidate.expected_roi,
            candidate.market_ticker,
            candidate.side,
        ),
    )
    return DecisionResult(selected.reason_code, selected, tuple(candidates))


def _candidate_for_side(
    *,
    probability: BracketProbability,
    quote: MarketQuote,
    side: Side,
    quantity: int,
    hurdle: Decimal,
    fee_schedule: FeeSchedule,
) -> tuple[TradeCandidate, ...]:
    price = quote.yes_ask if side == "yes" else quote.no_ask
    if price is None:
        return ()
    q = Decimal(str(probability.safe_yes if side == "yes" else probability.safe_no))
    economics = trade_economics(
        side=side,
        probability=q,
        quantity=quantity,
        price=price,
        role="maker",
        schedule=fee_schedule,
    )
    return (
        TradeCandidate(
            market_ticker=quote.market_ticker,
            bracket_id=quote.bracket_id,
            side=side,
            quantity=quantity,
            limit_price=price,
            conservative_probability=q,
            expected_roi=economics.roi,
            expected_value=economics.expected_value,
            reason_code=SHADOW_CANDIDATE_YES if side == "yes" else SHADOW_CANDIDATE_NO,
        ),
    )
