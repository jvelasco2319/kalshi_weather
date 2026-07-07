from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence, Set

from .types import CandidateTrade, MarketQuote, canonicalize_label


@dataclass(frozen=True)
class SnapshotConfig:
    show_snapshot: str = "changed"  # never, every, changed
    snapshot_every: int = 5
    blend_move_threshold_f: float = 0.5
    market_move_threshold_cents: int = 3


@dataclass(frozen=True)
class SnapshotState:
    iteration: int
    top_bracket: Optional[str]
    best_candidate_id: Optional[str]
    blend_temp_f: Optional[float]
    market_prices: Mapping[str, tuple] = field(default_factory=dict)
    rejection_reasons: Set[str] = field(default_factory=set)
    event: Optional[str] = None


@dataclass(frozen=True)
class SnapshotInputs:
    iteration: int
    timestamp_label: str
    blend_temp_f: Optional[float]
    top_bracket: Optional[str]
    model_temps_f: Mapping[str, float] = field(default_factory=dict)
    quotes: Sequence[MarketQuote] = ()
    best_candidate: Optional[CandidateTrade] = None
    rejection_reasons: Set[str] = field(default_factory=set)
    event: Optional[str] = None  # order_placed, position_closed, etc.

    def to_state(self) -> SnapshotState:
        return SnapshotState(
            iteration=self.iteration,
            top_bracket=self.top_bracket,
            best_candidate_id=self.best_candidate.candidate_id if self.best_candidate else None,
            blend_temp_f=self.blend_temp_f,
            market_prices=quote_fingerprint(self.quotes),
            rejection_reasons=set(self.rejection_reasons),
            event=self.event,
        )


def quote_fingerprint(quotes: Iterable[MarketQuote]) -> Mapping[str, tuple]:
    return {
        canonicalize_label(q.bracket_label): (q.yes_bid_cents, q.yes_ask_cents, q.no_bid_cents, q.no_ask_cents)
        for q in quotes
    }


def max_market_move_from_fingerprints(previous: Mapping[str, tuple], current: Mapping[str, tuple]) -> int:
    max_move = 0
    for label, vals in current.items():
        old = previous.get(label)
        if old is None:
            continue
        for a, b in zip(old, vals):
            if a is not None and b is not None:
                max_move = max(max_move, abs(int(b) - int(a)))
    return max_move


def max_market_move_cents(previous: Sequence[MarketQuote], current: Sequence[MarketQuote]) -> int:
    return max_market_move_from_fingerprints(quote_fingerprint(previous), quote_fingerprint(current))


def should_print_snapshot(*, config: SnapshotConfig, previous: Optional[SnapshotState], current: SnapshotState) -> bool:
    if config.show_snapshot == "never":
        return False
    if previous is None:
        return True
    if config.show_snapshot == "every":
        return config.snapshot_every > 0 and current.iteration % config.snapshot_every == 0
    if current.event in {"order_placed", "position_closed"}:
        return True
    if config.snapshot_every > 0 and current.iteration % config.snapshot_every == 0:
        return True
    if canonicalize_label(current.top_bracket or "") != canonicalize_label(previous.top_bracket or ""):
        return True
    if current.best_candidate_id != previous.best_candidate_id:
        return True
    if previous.blend_temp_f is not None and current.blend_temp_f is not None:
        if abs(current.blend_temp_f - previous.blend_temp_f) >= config.blend_move_threshold_f:
            return True
    if max_market_move_from_fingerprints(previous.market_prices, current.market_prices) >= config.market_move_threshold_cents:
        return True
    if not current.rejection_reasons.issubset(previous.rejection_reasons):
        return True
    return False


def should_emit_snapshot(
    previous: Optional[SnapshotInputs],
    current: SnapshotInputs,
    *,
    show_snapshot: str = "changed",
    snapshot_every: int = 5,
    blend_move_threshold_f: float = 0.5,
    market_move_threshold_cents: int = 3,
) -> bool:
    cfg = SnapshotConfig(show_snapshot, snapshot_every, blend_move_threshold_f, market_move_threshold_cents)
    return should_print_snapshot(config=cfg, previous=previous.to_state() if previous else None, current=current.to_state())


def _compact_from_inputs(inputs: SnapshotInputs, model_agreement: Optional[str] = None, outliers_note: str = "") -> str:
    blend = "--" if inputs.blend_temp_f is None else f"{inputs.blend_temp_f:.1f}°F"
    top = canonicalize_label(inputs.top_bracket or "--")
    best = "none"
    if inputs.best_candidate:
        c = inputs.best_candidate
        side = c.side.value if c.side else "-"
        edge = "--" if c.net_edge_cents is None else f"{c.net_edge_cents:+.1f}c"
        best = f"{side} {c.bracket_label} {edge}"
    model_parts = [f"Blend {blend}", f"Top {top}"]
    if model_agreement:
        model_parts.append(f"Agreement {model_agreement}")
    elif inputs.model_temps_f:
        temps = list(inputs.model_temps_f.values())
        spread = max(temps) - min(temps)
        agreement = "high" if spread < 1.5 else "medium" if spread < 3.5 else "low"
        model_parts.append(f"Agreement {agreement}")
    if outliers_note:
        model_parts.append(f"Outliers {outliers_note}")
    market_parts = []
    for q in inputs.quotes:
        y = f"{q.yes_bid_cents if q.yes_bid_cents is not None else '--'}/{q.yes_ask_cents if q.yes_ask_cents is not None else '--'}c"
        market_parts.append(f"{canonicalize_label(q.bracket_label)} Y {y}")
    return "\n".join([
        f"Snapshot {inputs.timestamp_label}",
        "",
        "Models: " + " | ".join(model_parts),
        "Market: " + " | ".join(market_parts[:6]) + f" | Best edge: {best}",
    ])


def render_compact_snapshot(
    inputs: Optional[SnapshotInputs] = None,
    *,
    timestamp: Optional[str] = None,
    blend_temp_f: Optional[float] = None,
    top_bracket: Optional[str] = None,
    model_agreement: Optional[str] = None,
    outliers_note: str = "",
    quotes: Sequence[MarketQuote] = (),
    candidates: Sequence[CandidateTrade] = (),
) -> str:
    """Render a compact non-wrapping snapshot.

    Accepts either a SnapshotInputs object or keyword arguments for easier CLI
    integration and backward-compatible tests.
    """
    if inputs is None:
        best = sorted(candidates, key=lambda c: c.net_edge_cents if c.net_edge_cents is not None else -9999, reverse=True)[0] if candidates else None
        inputs = SnapshotInputs(
            iteration=0,
            timestamp_label=timestamp or "--",
            blend_temp_f=blend_temp_f,
            top_bracket=top_bracket,
            quotes=quotes,
            best_candidate=best,
        )
    return _compact_from_inputs(inputs, model_agreement=model_agreement, outliers_note=outliers_note)


def render_market_table_snapshot(inputs: SnapshotInputs) -> str:
    lines = [f"Snapshot {inputs.timestamp_label}", "", "Market", "Bracket   YES bid/ask   NO bid/ask"]
    seen = set()
    for q in inputs.quotes:
        label = canonicalize_label(q.bracket_label)
        if label in seen:
            continue
        seen.add(label)
        y = f"{q.yes_bid_cents if q.yes_bid_cents is not None else '--'}/{q.yes_ask_cents if q.yes_ask_cents is not None else '--'}c"
        n = f"{q.no_bid_cents if q.no_bid_cents is not None else '--'}/{q.no_ask_cents if q.no_ask_cents is not None else '--'}c"
        lines.append(f"{label:<8} {y:<13} {n:<13}")
    return "\n".join(lines)
