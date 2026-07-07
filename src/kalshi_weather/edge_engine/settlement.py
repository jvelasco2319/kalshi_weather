from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Mapping, Optional, Tuple

from .types import Bracket, canonicalize_label


class ObservationStatus(str, Enum):
    """Cautious status for intraday observation-based bracket elimination.

    Public ASOS/METAR observations are useful for nowcasting, but they are not
    guaranteed to equal the final NWS climate-report settlement value. Treat
    these statuses as trade signals, not settlement truth.
    """

    LIVE = "live"
    ELIMINATED_PROBABLE = "eliminated_probable"
    UNKNOWN_STALE = "unknown_stale"
    UNKNOWN_NO_OBSERVATION = "unknown_no_observation"


@dataclass(frozen=True)
class SettlementRecord:
    station: str
    target_date: str
    official_high_f: float
    source: str = "NWS Daily Climate Report"
    issued_ts: Optional[str] = None


@dataclass(frozen=True)
class BracketSet:
    brackets: Tuple[Bracket, ...]

    def by_label(self) -> Dict[str, Bracket]:
        return {canonicalize_label(b.label): b for b in self.brackets}

    def bracket_for_temp(self, temp_f: float) -> Optional[Bracket]:
        matches = [b for b in self.brackets if b.contains(temp_f)]
        if len(matches) > 1:
            labels = ", ".join(b.label for b in matches)
            raise ValueError(f"temperature {temp_f} matched multiple brackets: {labels}")
        return matches[0] if matches else None

    def label_for_temp(self, temp_f: float) -> Optional[str]:
        bracket = self.bracket_for_temp(temp_f)
        return None if bracket is None else canonicalize_label(bracket.label)

    def validate_complete_distribution(self, probabilities: Mapping[str, float], tolerance: float = 1e-6) -> None:
        missing = set(self.by_label()) - {canonicalize_label(k) for k in probabilities}
        if missing:
            raise ValueError(f"probabilities missing brackets: {sorted(missing)}")
        total = sum(float(v) for v in probabilities.values())
        if abs(total - 1.0) > tolerance:
            raise ValueError(f"probabilities must sum to 1.0, got {total:.6f}")


def default_high_temp_bracket_set() -> BracketSet:
    """Default bracket set used by the user's KLAX example.

    Codex should adapt this to the repo's market metadata because actual Kalshi
    brackets vary by event.
    """
    return BracketSet(
        brackets=(
            Bracket("<66", lower_f=None, upper_f=65),
            Bracket("66-67", lower_f=66, upper_f=67),
            Bracket("68-69", lower_f=68, upper_f=69),
            Bracket("70-71", lower_f=70, upper_f=71),
            Bracket("72-73", lower_f=72, upper_f=73),
            Bracket("> 73", lower_f=74, upper_f=None),
        )
    )


def observation_status_for_bracket(
    bracket: Bracket,
    *,
    observed_high_f: Optional[float],
    observation_age_seconds: Optional[int] = None,
    max_observation_age_seconds: int = 900,
) -> ObservationStatus:
    if observed_high_f is None:
        return ObservationStatus.UNKNOWN_NO_OBSERVATION
    if observation_age_seconds is not None and observation_age_seconds > max_observation_age_seconds:
        return ObservationStatus.UNKNOWN_STALE
    if bracket.eliminated_by_observed_high(observed_high_f):
        return ObservationStatus.ELIMINATED_PROBABLE
    return ObservationStatus.LIVE


def settle_bracket_result(side: str, bracket_label: str, official_final_bracket: str) -> int:
    """Return 1 for a winning binary side, 0 for a losing side."""
    side_u = side.upper()
    won_bracket = canonicalize_label(bracket_label) == canonicalize_label(official_final_bracket)
    if side_u == "YES":
        return 1 if won_bracket else 0
    if side_u == "NO":
        return 0 if won_bracket else 1
    raise ValueError(f"side must be YES or NO, got {side!r}")
