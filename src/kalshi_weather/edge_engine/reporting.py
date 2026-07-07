from __future__ import annotations

from typing import Iterable

from .types import CandidateTrade


def compact_candidate_line(candidate: CandidateTrade) -> str:
    side = candidate.side.value if candidate.side else "-"
    label = candidate.bracket_label or "-"
    price = "--" if candidate.price_cents is None else f"{candidate.price_cents:.0f}c"
    edge = "--" if candidate.net_edge_cents is None else f"{candidate.net_edge_cents:+.1f}c"
    status = "OK" if candidate.eligible else f"REJECT:{candidate.rejection_reason}"
    return f"{side} {label} @ {price} edge {edge} {status}"


def summarize_candidates(candidates: Iterable[CandidateTrade], limit: int = 5) -> str:
    sorted_candidates = sorted(
        candidates,
        key=lambda c: c.net_edge_cents if c.net_edge_cents is not None else -9999.0,
        reverse=True,
    )
    return " | ".join(compact_candidate_line(c) for c in sorted_candidates[:limit])
