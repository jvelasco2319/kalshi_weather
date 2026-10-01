from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/acquire_v5b_untouched_economics.py"
SPEC = importlib.util.spec_from_file_location("v5b_economics", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_economics_plan_is_finite_and_outcome_blind() -> None:
    days = MODULE.registered_dates()
    assert len(days) == 50
    assert MODULE.MAX_REQUESTS == 60
    assert MODULE.MAX_BYTES == 20_000_000
    source = MODULE_PATH.read_text(encoding="utf-8").lower()
    assert "/series/fee_changes" in source
    assert "/events/fee_changes" in source
    assert "settlement_outcomes_read\": false" in source
    assert "/historical/markets" not in source
    assert "/historical/trades" not in source
