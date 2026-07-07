from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


def parse_iso_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class FreshnessConfig:
    max_market_age_seconds: int = 90
    max_model_age_seconds: int = 1800
    max_observation_age_seconds: int = 600


@dataclass(frozen=True)
class FreshnessReport:
    market_age_seconds: Optional[float]
    model_age_seconds: Optional[float]
    observation_age_seconds: Optional[float]
    market_stale: bool
    model_stale: bool
    observation_stale: bool

    @property
    def all_fresh(self) -> bool:
        return not (self.market_stale or self.model_stale or self.observation_stale)

    def as_candidate_metadata(self) -> Dict[str, object]:
        return {
            "market_age_seconds": self.market_age_seconds,
            "model_age_seconds": self.model_age_seconds,
            "observation_age_seconds": self.observation_age_seconds,
            "market_stale": self.market_stale,
            "model_stale": self.model_stale,
            "observation_stale": self.observation_stale,
        }


def age_seconds(now: datetime, ts: Optional[str]) -> Optional[float]:
    dt = parse_iso_ts(ts)
    if dt is None:
        return None
    return max(0.0, (now.astimezone(timezone.utc) - dt).total_seconds())


def assess_freshness(
    *,
    now: datetime,
    market_ts: Optional[str],
    model_ts: Optional[str],
    observation_ts: Optional[str],
    config: FreshnessConfig = FreshnessConfig(),
) -> FreshnessReport:
    market_age = age_seconds(now, market_ts)
    model_age = age_seconds(now, model_ts)
    obs_age = age_seconds(now, observation_ts)
    return FreshnessReport(
        market_age_seconds=market_age,
        model_age_seconds=model_age,
        observation_age_seconds=obs_age,
        market_stale=market_age is None or market_age > config.max_market_age_seconds,
        model_stale=model_age is None or model_age > config.max_model_age_seconds,
        observation_stale=obs_age is None or obs_age > config.max_observation_age_seconds,
    )
