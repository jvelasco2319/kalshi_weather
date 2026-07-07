from __future__ import annotations

from dataclasses import dataclass, field

@dataclass
class CLVRecord:
    fill_id: str
    bracket: str
    side: str
    entry_price_cents: float
    marks: dict[str, float] = field(default_factory=dict)

    def clv(self, horizon: str) -> float | None:
        mark = self.marks.get(horizon)
        if mark is None:
            return None
        # Since we store side-specific marks, YES and NO both benefit when mark rises.
        return mark - self.entry_price_cents

    def adverse_selection(self, horizon: str = "15m") -> bool:
        v = self.clv(horizon)
        return bool(v is not None and v < 0)


def summarize_clv(records: list[CLVRecord], horizon: str = "15m") -> dict[str, float | int]:
    vals = [r.clv(horizon) for r in records]
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"fills_count": len(records), "observed_count": 0, "avg_clv_cents": 0.0, "percent_positive_clv": 0.0}
    return {
        "fills_count": len(records),
        "observed_count": len(vals),
        "avg_clv_cents": sum(vals) / len(vals),
        "percent_positive_clv": sum(1 for v in vals if v > 0) / len(vals),
        "adverse_selection_count": sum(1 for v in vals if v < 0),
    }
