from __future__ import annotations

from decimal import Decimal

from typer.testing import CliRunner

from kalshi_weather.cli import app
from kalshi_weather.strategy_current.promotion import (
    PromotionEvidence,
    build_promotion_report,
    render_promotion_report,
)


def test_promotion_report_fails_closed_until_evidence_is_complete() -> None:
    report = build_promotion_report(
        PromotionEvidence(
            settled_forecast_dates=29,
            joined_market_dates=60,
            probability_calibrated=True,
            execution_validated=True,
            aggregate_roi=Decimal("0.20"),
        )
    )
    assert report.decision == "NO_GO_DATA_INCOMPLETE"

    report = build_promotion_report(
        PromotionEvidence(
            settled_forecast_dates=30,
            joined_market_dates=60,
            probability_calibrated=False,
            execution_validated=True,
            aggregate_roi=Decimal("0.20"),
        )
    )
    assert report.decision == "NO_GO_PROBABILITY_UNCALIBRATED"

    report = build_promotion_report(
        PromotionEvidence(
            settled_forecast_dates=30,
            joined_market_dates=60,
            probability_calibrated=True,
            execution_validated=True,
            aggregate_roi=Decimal("0.09"),
        )
    )
    assert report.decision == "NO_GO_RETURN_TARGET_NOT_SUPPORTED"


def test_promotion_ready_still_requires_human_review() -> None:
    report = build_promotion_report(
        PromotionEvidence(
            settled_forecast_dates=30,
            joined_market_dates=60,
            probability_calibrated=True,
            execution_validated=True,
            aggregate_roi=Decimal("0.12"),
        )
    )

    assert report.decision == "READY_FOR_HUMAN_CANARY_REVIEW"
    assert report.to_dict()["human_approval_required"] is True
    assert "No automatic promotion" in render_promotion_report(report)


def test_strategy_promotion_report_cli_defaults_to_no_go() -> None:
    result = CliRunner().invoke(app, ["strategy-promotion-report", "--json"])

    assert result.exit_code == 0
    assert '"decision": "NO_GO_DATA_INCOMPLETE"' in result.output
    assert '"human_approval_required": true' in result.output
