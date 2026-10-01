from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from past7_replay.audited import (
    TARGET_DATES,
    _apply_arrival,
    _classify_raw_row,
    _load_raw_snapshots,
)


ROOT = Path(__file__).resolve().parents[1]


def _raw(*, offset: int, ask_size: float = 1.0, ask: float = 0.40, bid: float = 0.39) -> dict:
    second = "00" if offset == 0 else "05"
    return {
        "climate_date": "2026-09-21",
        "market_platform_id": "KXHIGHLAX-26SEP21-B77.5",
        "outcome_name": "No",
        "target_offset_s": offset,
        "target_ts": f"2026-09-21 18:00:{second}.000000000",
        "before_observations": 1,
        "before_timestamp": f"2026-09-21 18:00:{second}.000000000",
        "before_state": "VERIFIED",
        "before_continuity": "CONTIGUOUS",
        "before_bids": [{"price": bid, "size": 2}],
        "before_asks": [{"price": ask, "size": ask_size}],
        "before_hash": 1,
    }


def test_raw_snapshot_coverage_and_offsets_are_exact():
    snapshots, bindings = _load_raw_snapshots(ROOT)
    assert set(snapshots) == {0, 5}
    assert len(snapshots[0]) == len(snapshots[5]) == 7 * 12
    assert len(bindings) == 7
    assert all(f"date={day}" in "|".join(bindings) for day in TARGET_DATES)


def test_fractional_ask_size_below_one_is_unavailable():
    result = _classify_raw_row(_raw(offset=5, ask_size=0.6))
    assert result["strict_usable"] is False
    assert result["evidence_grade"] == "UNAVAILABLE"
    assert "ASK_SIZE_BELOW_ONE_CONTRACT" in result["strict_failure_reasons"]


def test_exactly_one_contract_is_usable():
    result = _classify_raw_row(_raw(offset=5, ask_size=1.0))
    assert result["strict_usable"] is True
    assert result["displayed_ask_quantity_contracts"] == "1.0"


def test_arrival_does_not_reselect_when_frozen_order_misses_limit():
    order = {
        "decision_status": "ORDER_FROZEN", "decision_reason": None,
        "market_ticker": "A", "contract_side": "NO", "quantity": 1,
        "limit_price_cents": 40, "selection_probability": 0.60,
        "execution_status": "PENDING_ARRIVAL_CHECK", "execution_reason": None,
    }
    bad = _classify_raw_row(_raw(offset=5, ask=0.41, bid=0.40))
    bad["market_ticker"] = "A"
    alternative = _classify_raw_row(_raw(offset=5, ask=0.10, bid=0.09))
    alternative["market_ticker"] = "B"
    arrival = {
        ("2026-09-21", "A", "NO"): bad,
        ("2026-09-21", "B", "NO"): alternative,
    }
    result = _apply_arrival(order, "2026-09-21", arrival)
    assert result["market_ticker"] == "A"
    assert result["execution_status"] == "NOT_FILLED"
    assert result["execution_reason"] == "ARRIVAL_ASK_ABOVE_FROZEN_LIMIT"


def test_arrival_fractional_size_prevents_fill():
    order = {
        "decision_status": "ORDER_FROZEN", "decision_reason": None,
        "market_ticker": "A", "contract_side": "NO", "quantity": 1,
        "limit_price_cents": 45, "selection_probability": 0.60,
        "execution_status": "PENDING_ARRIVAL_CHECK", "execution_reason": None,
    }
    item = _classify_raw_row(_raw(offset=5, ask_size=0.6))
    result = _apply_arrival(order, "2026-09-21", {("2026-09-21", "A", "NO"): item})
    assert result["execution_status"] == "NOT_FILLED"
    assert result["execution_reason"] == "ARRIVAL_EVIDENCE_UNAVAILABLE"

