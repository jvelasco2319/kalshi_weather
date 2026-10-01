"""Compare supplied, source-linked architecture evaluations; no model calls."""
from collections import defaultdict
import math
from pathlib import Path
from statistics import mean, stdev

from .artifacts import filehash, read


def compare(paths):
    required = {"run_id", "topology", "model_id", "model_version", "contract_family", "evaluation_task_id", "prompt_version",
                "harness_version", "total_budget_cap", "actual_units", "target_success", "accepted_claims", "invalid_accepted_claims",
                "checked_evidence_count", "completed_tasks", "duplicate_tasks", "coordination_units", "recovery_correct", "source_locators"}
    rows = []
    for path in map(Path, paths):
        row = read(path)
        if required - row.keys() or not row["source_locators"] or row["topology"] not in ("single", "independent", "coordinator", "full"):
            raise ValueError("Evaluation manifest lacks source-linked outcomes or a supported topology")
        if type(row["target_success"]) is not bool or type(row["recovery_correct"]) not in (bool, type(None)):
            raise ValueError("Evaluation success/recovery must be observed booleans or explicitly unavailable")
        for key in ("total_budget_cap", "accepted_claims", "invalid_accepted_claims", "checked_evidence_count", "completed_tasks", "duplicate_tasks", "coordination_units"):
            if type(row[key]) not in (float, int) or not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError("Evaluation counts and costs must be nonnegative finite measurements")
        if row["actual_units"] is not None and (type(row["actual_units"]) not in (float, int) or not math.isfinite(row["actual_units"]) or row["actual_units"] < 0):
            raise ValueError("Actual usage must be known or explicitly null")
        if row["invalid_accepted_claims"] > row["accepted_claims"] or row["duplicate_tasks"] > row["completed_tasks"]:
            raise ValueError("Evaluation count denominators do not reconcile")
        rows.append({**row, "manifest_sha256": filehash(path)})
    if not rows or len({row["run_id"] for row in rows}) != len(rows):
        raise ValueError("Use distinct evaluation run IDs")
    conditions = {(r["model_id"], r["model_version"], r["contract_family"], r["total_budget_cap"]) for r in rows}
    if len(conditions) != 1:
        raise ValueError("First topology comparison requires matched model/version, task family and total budget cap")
    groups = defaultdict(list)
    for row in rows:
        groups[row["topology"]].append(row)
    task_sets = [{r["evaluation_task_id"] for r in group} for group in groups.values()]
    if any(tasks != task_sets[0] for tasks in task_sets):
        raise ValueError("Compare the same evaluation task set across topologies")
    report = {}
    for topology, group in groups.items():
        success = [int(r["target_success"]) for r in group]
        actual = [r["actual_units"] for r in group if r["actual_units"] is not None]
        accepted = sum(r["accepted_claims"] for r in group)
        completed = sum(r["completed_tasks"] for r in group)
        report[topology] = {"runs": len(group), "target_success_rate": mean(success),
            "success_stdev": stdev(success) if len(success) > 1 else None,
            "false_acceptance_rate": sum(r["invalid_accepted_claims"] for r in group) / accepted if accepted else None,
            "redundancy_rate": sum(r["duplicate_tasks"] for r in group) / completed if completed else None,
            "mean_actual_units": mean(actual) if actual else None, "usage_unknown_runs": len(group) - len(actual),
            "checked_evidence_per_unit": sum(r["checked_evidence_count"] for r in group) / sum(actual) if len(actual) == len(group) and sum(actual) > 0 else None,
            "coordination_units": sum(r["coordination_units"] for r in group),
            "recovery_success_rate": mean(r["recovery_correct"] for r in group if r["recovery_correct"] is not None) if any(r["recovery_correct"] is not None for r in group) else None}
    return {"comparison": "matched declared budget caps; actual spending is reported separately", "topologies": report,
            "sources": [{"run_id": r["run_id"], "sha256": r["manifest_sha256"], "locators": r["source_locators"]} for r in rows],
            "matched_actual_spending_claimed": False, "broad_reliability_established": False}
