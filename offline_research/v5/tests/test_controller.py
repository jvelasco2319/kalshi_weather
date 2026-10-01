from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from v5.orchestrator import _task_register
from v5.readiness import build_readiness
from v5.verifier import prelabel_conclusion


ROOT = Path(__file__).resolve().parents[2]


def test_readiness_binds_four_colonies_and_keeps_confirmation_sealed():
    readiness, ticket = build_readiness(ROOT)
    assert readiness["status"] == "READY_FOR_V5_OFFLINE_EVIDENCE_CAMPAIGN"
    assert len(readiness["colony_results"]) == 4
    assert all(
        row["status"] == "PASS"
        for row in readiness["cross_reviews"].values()
    )
    assert readiness["confirmation_eligible"] is False
    assert readiness["protected_confirmation_labels_read"] is False
    assert readiness["actual_orders_placed"] is False
    assert ticket["maximum_wall_seconds"] == 43_200


def test_execution_evidence_is_the_registered_first_terminal_blocker():
    readiness, _ = build_readiness(ROOT)
    assert prelabel_conclusion(readiness["colony_results"]) == (
        "INSUFFICIENT_EXECUTION_EVIDENCE")
    changed = deepcopy(readiness["colony_results"])
    changed["execution_evidence"]["promotion_ready"] = True
    assert prelabel_conclusion(changed) == (
        "FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE")


def test_task_register_has_four_colonies_across_five_epochs():
    tasks = _task_register()
    assert len(tasks) == 20
    assert len({row["colony"] for row in tasks}) == 4
    assert len({row["epoch"] for row in tasks}) == 5
    assert sum(row["status"] == "COMPLETED" for row in tasks) == 4
    assert all(row["actual_orders_placed"] is False for row in tasks)
