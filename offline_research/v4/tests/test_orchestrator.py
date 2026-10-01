from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from v4.execution_coverage import V4CoverageError
from v4.orchestrator import (
    complete_epoch_v4, load_state_v4, prepare_v4, resume_v4, start_v4,
    status_v4,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)


def _negative_result(queue_row):
    return {
        "candidate_id": "result-" + queue_row["plan_sha256"][:20],
        "plan": queue_row["plan"],
        "evaluation": {
            "candidate": {
                "forecast_scores": {
                    "crps_f": 0.66, "brier": 0.83,
                    "probability_conservation_passed": True,
                },
                "historical_assumed_fill": {
                    "trade_count": 0, "selected_settlement_days": [],
                },
            },
            "reference": {"forecast_scores": {"crps_f": 0.98, "brier": 1.10}},
        },
        "ledger": {"decisions": [
            {"reason": "EXPECTED_RETURN_BELOW_TARGET"} for _ in range(20)]},
        "promotion": {
            "passed": False,
            "reasons": [
                "development_return_below_10_percent_gate",
                "insufficient_simulated_trades",
            ],
            "capital_weighted_return": None,
            "stress_return": None,
            "bootstrap_lower_95": None,
            "profitable_fold_count": 0,
        },
        "replication": {"status": "PASS", "protected_final_evaluated": False},
        "critic": {"decision": "NONREJECT", "protected_final_evaluated": False},
    }


def test_prepare_start_resume_status_is_durable_and_idempotent(tmp_path):
    state_path = tmp_path / "v4-state.json"
    prepared = prepare_v4(ROOT, state_path)
    assert prepared["status"] == "PREPARED"
    assert prepared["active_queue"] == []
    started = start_v4(ROOT, state_path, now=NOW)
    assert started["status"] == "RUNNING"
    assert started["current_epoch"] == 1
    assert len(started["active_queue"]) == 12
    assert started["candidate_calls_used"] == 12
    assert all(row["followup_packet"]["evidence_digest_sha256"]
               == started["current_digest"]["digest_sha256"]
               for row in started["active_queue"])
    resumed = resume_v4(ROOT, state_path)
    assert resumed["state_sha256"] == started["state_sha256"]
    assert resumed["active_queue"] == started["active_queue"]
    status = status_v4(ROOT, state_path, now=NOW)
    assert status["status"] == "RUNNING"
    assert status["active_queue_items"] == 12
    assert status["seconds_remaining"] == 43200
    assert status["protected_final_read"] is False
    assert status["orders_authorized"] is False


def test_external_epoch_results_are_rejected_even_when_they_claim_passes(tmp_path):
    state_path = tmp_path / "v4-state.json"
    prepare_v4(ROOT, state_path)
    first = start_v4(ROOT, state_path, now=NOW)
    results = [_negative_result(row) for row in first["active_queue"]]
    results[0]["promotion"]["passed"] = True
    with pytest.raises(V4CoverageError, match="External V4 epoch results"):
        complete_epoch_v4(ROOT, state_path, results, now=NOW)


def test_state_tampering_fails_closed(tmp_path):
    state_path = tmp_path / "v4-state.json"
    prepare_v4(ROOT, state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["orders_authorized"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(V4CoverageError, match="identity or safety"):
        load_state_v4(state_path)


def test_state_path_inside_protected_final_is_rejected(tmp_path):
    with pytest.raises(V4CoverageError, match="protected-final"):
        prepare_v4(ROOT, tmp_path / "protected_final" / "state.json")
