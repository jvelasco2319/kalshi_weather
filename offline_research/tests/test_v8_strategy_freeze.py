from copy import deepcopy
from pathlib import Path

import pytest

from v8.strategy_freeze import StrategyFreezeError, build, verify

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v8_strategy_freeze_binds_exact_candidate_and_policy():
    value = build(PROJECT_ROOT)
    assert value["probability_model"]["candidate_id"] == "rolling_confusion-alpha-2-w-0.75"
    assert value["probability_model"]["future_outcome_updates_allowed"] is False
    assert value["probability_model"]["regime_overlay_enabled"] is False
    assert value["trade_policy"]["execution_evidence_required"] == "GRADE_A_FULL_BOOK"
    assert value["confirmation_protocol"]["minimum_genuinely_new_grade_a_days"] == 100
    assert value["confirmation_protocol"]["minimum_filled_trades"] == 40
    assert value["safety"]["live_orders_authorized"] is False


def test_v8_strategy_freeze_rejects_tampering():
    value = build(PROJECT_ROOT)
    tampered = deepcopy(value)
    tampered["probability_model"]["conditional_confusion_weight"] = 0.8
    with pytest.raises(ValueError):
        verify(PROJECT_ROOT, tampered)


def test_v8_strategy_freeze_rejects_weakened_policy_even_when_resealed():
    from past7_replay.engine import _seal

    value = build(PROJECT_ROOT)
    weakened = deepcopy(value)
    weakened.pop("self_sha256")
    weakened["trade_policy"]["execution_evidence_required"] = "GRADE_B_PROXY"
    with pytest.raises(StrategyFreezeError):
        verify(PROJECT_ROOT, _seal(weakened))
