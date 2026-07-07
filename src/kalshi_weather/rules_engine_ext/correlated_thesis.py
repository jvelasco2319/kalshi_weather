from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

@dataclass(frozen=True)
class ThesisPosition:
    bracket: str
    side: str
    risk_dollars: float

@dataclass(frozen=True)
class ThesisExposure:
    thesis_label: str
    correlated_positions: list[ThesisPosition]
    correlated_risk_dollars: float
    thesis_allowed: bool
    thesis_rejection_reason: str | None


def infer_thesis_label(position: ThesisPosition, top_bracket: str) -> str:
    if position.side.upper() == "YES" and position.bracket == top_bracket:
        return f"exact_center:{top_bracket}"
    if position.side.upper() == "YES":
        return "near_center_yes"
    if position.side.upper() == "NO" and position.bracket != top_bracket:
        return f"not:{position.bracket}"
    return "other"


def evaluate_thesis_exposure(positions: Iterable[ThesisPosition], top_bracket: str, max_risk_dollars: float = 75.0) -> list[ThesisExposure]:
    groups: dict[str, list[ThesisPosition]] = {}
    for p in positions:
        groups.setdefault(infer_thesis_label(p, top_bracket), []).append(p)
    out = []
    for label, vals in groups.items():
        risk = sum(v.risk_dollars for v in vals)
        allowed = risk <= max_risk_dollars
        out.append(ThesisExposure(label, vals, risk, allowed, None if allowed else "correlated_thesis_exposure_too_high"))
    return out
