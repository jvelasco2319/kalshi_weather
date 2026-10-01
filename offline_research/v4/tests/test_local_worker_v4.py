from pathlib import Path

from v4.local_worker_v4 import run_v4_worker_protocol_self_test


ROOT = Path(__file__).resolve().parents[2]


def test_v4_worker_protocol_preserves_exact_plan_and_typed_synthesis() -> None:
    result = run_v4_worker_protocol_self_test(ROOT)
    assert result["status"] == "PASS"
    assert result["protected_final_read"] is False
    assert result["network_used"] is False
    assert all(result["checks"].values())

