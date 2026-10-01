from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/acquire_v5b_untouched_probalytics.py"
SPEC = importlib.util.spec_from_file_location("v5b_paid_acquisition", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_registered_dates_are_exactly_the_two_untouched_windows() -> None:
    plan = {
        "confirmation_windows": [
            {"date_start": "2026-05-09", "date_end": "2026-05-31", "role": "pre-development-level-2"},
            {"date_start": "2026-08-04", "date_end": "2026-08-31", "role": "sealed-v5a-holdout"},
            {"date_start": "2026-09-01", "date_end": "2026-09-27", "role": "post-development-level-2"},
        ]
    }
    days = MODULE.registered_dates(plan)
    assert len(days) == 50
    assert days[0] == date(2026, 5, 9)
    assert days[22] == date(2026, 5, 31)
    assert days[23] == date(2026, 9, 1)
    assert days[-1] == date(2026, 9, 27)
    assert date(2026, 8, 4) not in days


def test_queries_are_historical_outcome_blind_and_depth_bounded() -> None:
    day = date(2026, 9, 27)
    coverage = MODULE.coverage_query(day)
    target = MODULE.target_query(day)
    for query in (coverage, target):
        assert "orderbook_snapshots" in query
        assert "KXHIGHLAX-26SEP27-%" in query
        assert "2026-09-27 17:55:00" in query
        assert "2026-09-27 18:06:00" in query
        assert "result" not in query.lower()
        assert "settlement" not in query.lower()
    assert "[0, 5, 30, 60]" in target
    assert "argMaxIf(bids" in target
    assert "argMinIf(asks" in target


def test_event_prefix_uses_kalshi_daily_lax_ticker() -> None:
    assert MODULE.event_prefix(date(2026, 5, 9)) == "KXHIGHLAX-26MAY09-%"
