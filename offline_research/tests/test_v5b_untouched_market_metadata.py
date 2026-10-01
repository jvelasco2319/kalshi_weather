from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/acquire_v5b_untouched_market_metadata.py"
SPEC = importlib.util.spec_from_file_location("v5b_market_metadata", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_market_metadata_query_is_registered_and_outcome_blind() -> None:
    query = MODULE.metadata_query([date(2026, 5, 9), date(2026, 9, 27)])
    assert "KXHIGHLAX-26MAY09-" in query
    assert "KXHIGHLAX-26SEP27-" in query
    assert "resolution_winning_outcome_id" not in query
    assert "resolution_outcome_payouts" not in query
    assert "status" not in query.lower()
    assert "settlement" not in query.lower()
    assert "outcomes AS contract_sides" in query
