from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

PromotionDecision = Literal[
    "NO_GO_DATA_INCOMPLETE",
    "NO_GO_PROBABILITY_UNCALIBRATED",
    "NO_GO_EXECUTION_NOT_VALIDATED",
    "NO_GO_RETURN_TARGET_NOT_SUPPORTED",
    "READY_FOR_HUMAN_CANARY_REVIEW",
]


@dataclass(frozen=True)
class PromotionEvidence:
    settled_forecast_dates: int
    joined_market_dates: int
    probability_calibrated: bool
    execution_validated: bool
    aggregate_roi: Decimal | None
    minimum_settled_forecast_dates: int = 30
    preferred_joined_market_dates: int = 60
    long_run_target_roi: Decimal = Decimal("0.10")


@dataclass(frozen=True)
class PromotionReport:
    decision: PromotionDecision
    evidence: PromotionEvidence
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "settled_forecast_dates": self.evidence.settled_forecast_dates,
            "joined_market_dates": self.evidence.joined_market_dates,
            "probability_calibrated": self.evidence.probability_calibrated,
            "execution_validated": self.evidence.execution_validated,
            "aggregate_roi": None
            if self.evidence.aggregate_roi is None
            else str(self.evidence.aggregate_roi),
            "minimum_settled_forecast_dates": self.evidence.minimum_settled_forecast_dates,
            "preferred_joined_market_dates": self.evidence.preferred_joined_market_dates,
            "long_run_target_roi": str(self.evidence.long_run_target_roi),
            "summary": self.summary,
            "human_approval_required": True,
        }


def build_promotion_report(evidence: PromotionEvidence) -> PromotionReport:
    if (
        evidence.settled_forecast_dates < evidence.minimum_settled_forecast_dates
        or evidence.joined_market_dates < evidence.preferred_joined_market_dates
    ):
        return PromotionReport(
            "NO_GO_DATA_INCOMPLETE",
            evidence,
            "Not enough settled forecast dates or joined market days for canary review.",
        )
    if not evidence.probability_calibrated:
        return PromotionReport(
            "NO_GO_PROBABILITY_UNCALIBRATED",
            evidence,
            "Probability calibration has not passed.",
        )
    if not evidence.execution_validated:
        return PromotionReport(
            "NO_GO_EXECUTION_NOT_VALIDATED",
            evidence,
            "Execution simulation has not been validated from joined orderbook/trade data.",
        )
    if evidence.aggregate_roi is None or evidence.aggregate_roi < evidence.long_run_target_roi:
        return PromotionReport(
            "NO_GO_RETURN_TARGET_NOT_SUPPORTED",
            evidence,
            "Aggregate ROI does not support the long-run target.",
        )
    return PromotionReport(
        "READY_FOR_HUMAN_CANARY_REVIEW",
        evidence,
        "Evidence is sufficient for a human canary review. No automatic promotion is allowed.",
    )


def render_promotion_report(report: PromotionReport) -> str:
    payload = report.to_dict()
    lines = [
        "# Promotion Readiness Report",
        "",
        f"Decision: {payload['decision']}",
        "",
        "## Evidence",
        "",
        f"- Settled forecast dates: {payload['settled_forecast_dates']}",
        f"- Joined market dates: {payload['joined_market_dates']}",
        f"- Probability calibrated: {payload['probability_calibrated']}",
        f"- Execution validated: {payload['execution_validated']}",
        f"- Aggregate ROI: {payload['aggregate_roi']}",
        f"- Human approval required: {payload['human_approval_required']}",
        "",
        "## Summary",
        "",
        str(payload["summary"]),
    ]
    return "\n".join(lines) + "\n"
