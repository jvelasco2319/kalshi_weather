import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts/acquire_v5b_untouched_confirmation_labels.py"
SPEC = importlib.util.spec_from_file_location("confirmation_labels", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_label_query_is_finite_and_resolution_only() -> None:
    query = MODULE.label_query(["KXHIGHLAX-26SEP01-T76", "KXHIGHLAX-26SEP01-B76.5"])
    assert "platform_id IN" in query
    assert "resolution_winning_outcome_id" in query
    assert "resolution_outcome_payouts" in query
    assert "FORMAT JSONEachRow" in query
    assert "orderbook" not in query.lower()
