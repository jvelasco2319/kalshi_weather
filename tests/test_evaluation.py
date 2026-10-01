import json

import pytest

from research_swarm.evaluation import compare


def fixture(topology, identifier):
    return {"run_id": identifier, "topology": topology, "model_id": "TEST-FIXTURE-NO-MODEL", "model_version": "fixture-v1",
        "contract_family": "fixture-tasks", "evaluation_task_id": "fixture-question", "prompt_version": "fixture-v1", "harness_version": "fixture-v1",
        "total_budget_cap": 100, "actual_units": None, "target_success": False, "accepted_claims": 0, "invalid_accepted_claims": 0,
        "checked_evidence_count": 0, "completed_tasks": 1, "duplicate_tasks": 0, "coordination_units": 0, "recovery_correct": None,
        "source_locators": ["TEST FIXTURE ONLY; no model call or real evaluation"]}


def paths(tmp_path, rows):
    files = []
    for i, row in enumerate(rows):
        path = tmp_path / f"fixture-{i}.json"
        path.write_text(json.dumps(row), encoding="utf-8")
        files.append(path)
    return files


def test_unknown_usage_and_single_repeat_are_not_reported_as_proven_quality(tmp_path):
    result = compare(paths(tmp_path, [fixture("single", "one"), fixture("full", "two")]))
    assert result["matched_actual_spending_claimed"] is False
    assert result["broad_reliability_established"] is False
    assert result["topologies"]["full"]["success_stdev"] is None
    assert result["topologies"]["full"]["checked_evidence_per_unit"] is None


def test_unmatched_model_or_task_set_comparison_is_rejected(tmp_path):
    rows = [fixture("single", "one"), fixture("full", "two")]
    rows[1]["model_version"] = "different"
    with pytest.raises(ValueError, match="matched model"):
        compare(paths(tmp_path, rows))
    rows[1]["model_version"] = "fixture-v1"
    rows[1]["evaluation_task_id"] = "different-question"
    with pytest.raises(ValueError, match="same evaluation task set"):
        compare(paths(tmp_path, rows))
