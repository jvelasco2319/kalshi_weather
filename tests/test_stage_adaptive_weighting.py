from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from kalshi_weather.strategy_current.registry import CANONICAL_MODEL_KEYS
from kalshi_weather.strategy_current.stage_weighting import (
    WEIGHTING_BLOCKED_INSUFFICIENT_MODELS,
    StagePerformanceRow,
    build_stage_weight_snapshot,
    classify_market_stage,
    load_stage_weight_config,
    multiclass_brier_score,
    public_weight_snapshot,
    score_realized_probability,
    summarize_stage_scores,
    weighting_reason_code,
)

PT = ZoneInfo("America/Los_Angeles")
TARGET = date(2026, 7, 13)
SCHEMA = Path("implementation/klax_stage_adaptive_weighting/contracts/stage_weight_snapshot.schema.json")


def test_stage_weight_config_is_exact_safe_and_normalized() -> None:
    config = load_stage_weight_config()

    assert config.canonical_order == CANONICAL_MODEL_KEYS
    assert config.primary_mode == "stage_reliability"
    assert config.order_submission_reachable is False
    assert config.live_trading_enabled is False
    assert len(config.config_hash) == 64
    assert sum(config.fixed_prior.values()) == pytest.approx(1.0)
    for prior in config.stage_priors.values():
        assert sum(prior.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("evaluated_at", "expected"),
    [
        (datetime(2026, 7, 12, 7, 0, tzinfo=PT), "pre_target"),
        (datetime(2026, 7, 13, 1, 59, 59, tzinfo=PT), "pre_target"),
        (datetime(2026, 7, 13, 2, 0, tzinfo=PT), "target_02_10"),
        (datetime(2026, 7, 13, 11, 0, tzinfo=PT), "target_11_13"),
        (datetime(2026, 7, 13, 14, 0, tzinfo=PT), "target_14_16"),
        (datetime(2026, 7, 13, 17, 0, tzinfo=PT), "target_17_close"),
    ],
)
def test_stage_boundaries(evaluated_at: datetime, expected: str) -> None:
    assert classify_market_stage(evaluated_at, TARGET).stage_id == expected


def test_stage_transition_is_post_boundary_only() -> None:
    before = classify_market_stage(datetime(2026, 7, 13, 10, 59, 59, tzinfo=PT), TARGET)
    boundary = classify_market_stage(datetime(2026, 7, 13, 11, 0, tzinfo=PT), TARGET)
    halfway = classify_market_stage(datetime(2026, 7, 13, 11, 15, tzinfo=PT), TARGET)
    complete = classify_market_stage(datetime(2026, 7, 13, 11, 30, tzinfo=PT), TARGET)

    assert before.stage_id == "target_02_10"
    assert before.transition_from_stage is None
    assert boundary.transition_alpha == pytest.approx(0.0)
    assert boundary.transition_from_stage == "target_02_10"
    assert halfway.transition_alpha == pytest.approx(0.5)
    assert complete.transition_alpha == pytest.approx(1.0)
    assert complete.transition_from_stage is None


def test_stage_classifier_is_dst_aware() -> None:
    target = date(2026, 11, 1)
    before = datetime(2026, 11, 1, 1, 59, 59, tzinfo=PT)
    after = datetime(2026, 11, 1, 2, 0, 0, tzinfo=PT)

    assert classify_market_stage(before, target).stage_id == "pre_target"
    assert classify_market_stage(after, target).stage_id == "target_02_10"


def test_stage_history_excludes_current_date_and_unknown_settlements() -> None:
    evaluated_at = datetime(2026, 7, 13, 18, tzinfo=timezone.utc)
    rows = [
        StagePerformanceRow(
            "gfs013",
            date(2026, 7, 12),
            "target_11_13",
            0.4,
            datetime(2026, 7, 13, 17, tzinfo=timezone.utc),
            ("prior-a",),
        ),
        StagePerformanceRow(
            "gfs013",
            date(2026, 7, 12),
            "target_11_13",
            0.6,
            datetime(2026, 7, 13, 17, tzinfo=timezone.utc),
            ("prior-b",),
        ),
        StagePerformanceRow(
            "gfs013",
            TARGET,
            "target_11_13",
            0.01,
            datetime(2026, 7, 13, 17, tzinfo=timezone.utc),
            ("current",),
        ),
        StagePerformanceRow(
            "gfs013",
            date(2026, 7, 11),
            "target_11_13",
            0.01,
            datetime(2026, 7, 13, 19, tzinfo=timezone.utc),
            ("future-settlement",),
        ),
    ]

    summary = summarize_stage_scores(
        rows,
        model_key="gfs013",
        stage_id="target_11_13",
        target_date=TARGET,
        evaluated_at=evaluated_at,
        bracket_count=6,
    )

    assert summary.dates == 1
    assert summary.log_loss == pytest.approx(0.5)
    assert summary.source_evaluation_ids == ("prior-a", "prior-b")


def test_prior_only_snapshot_applies_caps_and_validates_schema() -> None:
    snapshot = _snapshot(rows=[])
    public = public_weight_snapshot(snapshot)

    Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    ).validate(public)
    assert public["status"] == "PRIOR_ONLY"
    assert weighting_reason_code(snapshot) == "WEIGHTING_STAGE_PRIOR_ONLY"
    for mode in public["counterfactuals"]:
        assert sum(mode["weights"].values()) == pytest.approx(1.0)
        assert (
            mode["weights"]["gfs013"] + mode["weights"]["gfs_seamless"]
            <= 0.45 + 1e-12
        )
        assert mode["weights"]["nbm"] == 0.0


def test_stage_reliability_is_ready_and_deterministically_capped() -> None:
    losses = {
        "ecmwf_ifs": 0.8,
        "gfs013": 0.2,
        "gfs_seamless": 0.25,
        "nam": 0.9,
        "nbm": 0.7,
    }
    rows = _history_rows(losses, count=40, stage_id="target_11_13")

    first = _snapshot(rows=rows)
    second = _snapshot(rows=rows)
    public = public_weight_snapshot(first)
    primary = next(item for item in public["counterfactuals"] if item["isPrimary"])

    assert first == second
    assert public["status"] == "READY"
    assert weighting_reason_code(first) == "WEIGHTING_STAGE_RELIABILITY_READY"
    assert sum(primary["weights"].values()) == pytest.approx(1.0)
    assert max(primary["weights"].values()) <= 0.35 + 1e-12
    assert primary["weights"]["gfs013"] + primary["weights"]["gfs_seamless"] <= 0.45 + 1e-12
    assert primary["weights"]["nbm"] <= 0.20 + 1e-12
    multipliers = {row["modelKey"]: row["reliabilityMultiplier"] for row in public["models"]}
    assert multipliers["gfs013"] == pytest.approx(1.0)
    assert all(0 < value <= 1 for value in multipliers.values())


def test_unavailable_model_has_zero_weight_but_four_feeds_remain_valid() -> None:
    available = {key: True for key in CANONICAL_MODEL_KEYS}
    available["gfs013"] = False
    snapshot = _snapshot(rows=[], available=available)

    for mode in snapshot["counterfactuals"]:
        assert mode["weights"]["gfs013"] == 0.0
        assert sum(mode["weights"].values()) == pytest.approx(1.0)
    gfs = next(row for row in snapshot["models"] if row["modelKey"] == "gfs013")
    assert gfs["weightingStatus"] == "unavailable"


def test_fewer_than_four_feeds_fails_closed() -> None:
    available = {key: key in {"ecmwf_ifs", "gfs013", "nam"} for key in CANONICAL_MODEL_KEYS}
    snapshot = _snapshot(rows=[], available=available)

    assert snapshot["status"] == "BLOCKED"
    assert weighting_reason_code(snapshot) == WEIGHTING_BLOCKED_INSUFFICIENT_MODELS
    assert all(sum(mode["weights"].values()) == 0 for mode in snapshot["counterfactuals"])


def test_transition_blends_stage_priors_after_boundary() -> None:
    snapshot = _snapshot(
        rows=[],
        evaluated_at=datetime(2026, 7, 13, 11, 15, tzinfo=PT),
    )
    model = next(row for row in snapshot["models"] if row["modelKey"] == "gfs013")

    assert snapshot["stage"]["transitionAlpha"] == pytest.approx(0.5)
    assert model["stagePrior"] == pytest.approx((0.27 + 0.21) / 2)


def test_proper_scoring_helpers_use_posterior_probability_vector() -> None:
    assert score_realized_probability(0.5) == pytest.approx(0.6931471805599453)
    assert score_realized_probability(0.0) == pytest.approx(4.605170185988091)
    assert multiclass_brier_score([0.1, 0.7, 0.2], 1) == pytest.approx(0.14)


def _snapshot(
    *,
    rows: list[StagePerformanceRow],
    available: dict[str, bool] | None = None,
    evaluated_at: datetime | None = None,
) -> dict[str, object]:
    return build_stage_weight_snapshot(
        evaluation_id="eval-stage-weight-test",
        evaluated_at=evaluated_at or datetime(2026, 7, 13, 12, 0, tzinfo=PT),
        target_date=TARGET,
        strategy_config_hash="strategy-config-hash",
        code_revision="test-revision",
        bracket_count=6,
        score_rows=rows,
        available=available or {key: True for key in CANONICAL_MODEL_KEYS},
    )


def _history_rows(
    losses: dict[str, float],
    *,
    count: int,
    stage_id: str,
) -> list[StagePerformanceRow]:
    rows: list[StagePerformanceRow] = []
    for model_key, loss in losses.items():
        for age in range(1, count + 1):
            target = TARGET - timedelta(days=age)
            settled_at = datetime.combine(target + timedelta(days=1), time(8), tzinfo=timezone.utc)
            rows.append(
                StagePerformanceRow(
                    model_key=model_key,
                    target_date=target,
                    stage_id=stage_id,
                    mean_log_loss=loss,
                    settled_at=settled_at,
                    source_evaluation_ids=(f"{model_key}-{target.isoformat()}",),
                )
            )
    return rows
