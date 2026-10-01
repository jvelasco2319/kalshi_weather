"""Operational plans and investigation delivery stay separate from claim acceptance."""
from pathlib import Path

from .artifacts import checked, utcnow, write
from .resources import budget_status
from .store import event, get_record, record, transaction


def update_plan(run_dir, packet):
    required = {"rationale", "uncertainties", "dependencies", "next_tasks", "useful_progress", "next_discriminating_test"}
    if required - packet.keys() or not packet["rationale"] or not packet["next_discriminating_test"]:
        raise ValueError("Plan needs a rationale, uncertainties, dependencies, tasks and a discriminating next test")
    if {"contract", "gates", "deadline", "verifier", "budget", "acceptance_policy"} & packet.keys():
        raise ValueError("Operational plans cannot change the frozen target or acceptance controls")
    registration = checked(Path(run_dir) / "registration.json")
    with transaction(run_dir) as db:
        for identifier in packet["next_tasks"]:
            if not db.execute("SELECT 1 FROM tasks WHERE id=?", (identifier,)).fetchone():
                raise ValueError("Plan cites a nonexistent task")
        saved = record(db, "plan", "operational-plan", {**packet, "contract_id": registration["spec"]["contract"]["id"],
                       "contract_version": registration["spec"]["contract"]["version"], "created_at": utcnow().isoformat()})
    write(Path(run_dir) / "operational-plan.json", {**packet, "record": saved})
    return saved


def finish(run_dir, packet):
    from .campaign import verify
    verify(run_dir)
    required = {"completion_class", "rationale", "decision_ids", "unresolved", "excluded_runs", "next_action"}
    allowed = {"objective_satisfied_within_scope", "partial_progress", "inconclusive", "operationally_blocked"}
    if required - packet.keys() or packet["completion_class"] not in allowed or not packet["rationale"]:
        raise ValueError("Completion needs a separate delivery class, scoped rationale and unresolved-work record")
    registration = checked(Path(run_dir) / "registration.json")
    target = Path(run_dir) / "investigation-completion.json"
    if target.exists():
        raise ValueError("Completion record is immutable; use a separate successor investigation")
    with transaction(run_dir) as db:
        decisions = [get_record(db, "decision", identifier) for identifier in packet["decision_ids"]]
        if packet["completion_class"] == "objective_satisfied_within_scope":
            acceptable = []
            for decision in decisions:
                body = decision["payload"]
                candidate = get_record(db, "candidate", body["candidate_id"])
                question = next(q for q in registration["spec"]["research_questions"] if q["id"] == candidate["payload"]["hypothesis"]["question_id"])
                if decision["applicability"] == candidate["applicability"] == "active" and body["status"] == "accepted" and question["kind"] == "main" and body["accepted_scope"] == registration["spec"]["contract"]["acceptance_scope"]:
                    acceptable.append(decision)
            if not acceptable:
                raise ValueError("Budget exhaustion or check passes cannot satisfy the objective without an active accepted main-question decision")
        active = db.execute("SELECT COUNT(*) FROM task_attempts WHERE status IN ('leased','running','expired','released_unknown','cancellation_requested','submitted')").fetchone()[0]
        if active:
            raise ValueError("Account for or cancel outstanding task leases before final delivery")
        for exclusion in packet["excluded_runs"]:
            if not exclusion.get("run_id") or not exclusion.get("reason"):
                raise ValueError("Every excluded run needs a stable ID and explanation")
            get_record(db, "run", exclusion["run_id"])
        saved = record(db, "completion", "investigation", {**packet, "contract_version": registration["spec"]["contract"]["version"]},
                       [("decision", d["id"], d["version"]) for d in decisions])
        event(db, "investigation_delivered", {"completion_class": packet["completion_class"], "record": saved})
    return write(target, {**packet, "record": saved, "resources": budget_status(Path(run_dir)), "completed_at": utcnow().isoformat(),
                 "claim_outcomes_remain_separate": True})
