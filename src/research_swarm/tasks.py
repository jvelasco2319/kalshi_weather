"""Provider-independent task admission, fencing and durable result acknowledgment.

The host launches real agents. This module enforces local leases and reservations;
it does not install filesystem isolation or meter a provider behind the host.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from .artifacts import checked, digest, filehash, utcnow, write
from .resources import reserve_db
from .store import audit, event, get_record, record, transaction


def _control(run_dir):
    registration = checked(Path(run_dir) / "registration.json")
    state = checked(Path(run_dir) / "state.json")
    if registration["campaign_id"] != state["campaign_id"]:
        raise ValueError("Task runtime campaign identity mismatch")
    from .campaign import code_bindings
    if code_bindings(Path(registration["project_root"])) != registration["code_bindings"] or filehash(Path(run_dir) / "development-input.json") != registration["development_sha256"]:
        raise ValueError("Task runtime source or input bindings changed")
    if any(not (Path(run_dir) / path).is_file() or filehash(Path(run_dir) / path) != sha for path, sha in registration.get("artifact_input_bindings", {}).items()):
        raise ValueError("Task runtime frozen artifact input changed")
    audit(run_dir)
    if (Path(run_dir) / "investigation-completion.json").exists():
        state = {**state, "status": "INVESTIGATION_COMPLETE"}
    return registration, state


def enqueue(run_dir, task):
    registration, state = _control(run_dir)
    required = {"id", "objective", "role", "colony", "dependencies", "evidence", "tools", "max_units", "depth", "completion_rule"}
    if required - task.keys() or not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", task["id"]):
        raise ValueError("Task needs an ID and complete bounded brief")
    if task["role"] not in ("research", "reviewer", "verifier", "editor") or task["colony"] not in state["colonies"]:
        raise ValueError("Unknown task role or colony")
    runtime = registration["spec"]["runtime"]
    if type(task["depth"]) is not int or not 0 <= task["depth"] <= runtime["max_delegation_depth"]:
        raise ValueError("Delegation depth exceeds the registered limit")
    if not set(task["tools"]) <= set(runtime["allowed_tools"]):
        raise ValueError("Task requested tools outside the registered allowlist")
    if not task["objective"] or not task["completion_rule"] or type(task["max_units"]) not in (int, float) or task["max_units"] <= 0:
        raise ValueError("Task objective, completion and cost bound must be explicit")
    task = {**task, "contract_id": registration["spec"]["contract"]["id"], "contract_version": registration["spec"]["contract"]["version"],
            "campaign_id": state["campaign_id"]}
    sha = digest(task)
    with transaction(run_dir) as db:
        old = db.execute("SELECT sha256 FROM tasks WHERE id=?", (task["id"],)).fetchone()
        if old:
            if old[0] != sha:
                raise ValueError("Task ID already has a different immutable brief")
            return {"id": task["id"], "created": False}
        for dep in task["dependencies"]:
            if not db.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone():
                raise ValueError("Task dependency is unknown")
        db.execute("INSERT INTO tasks(id,payload,sha256,status) VALUES(?,?,?,?)", (task["id"], json.dumps(task), sha, "queued"))
        record(db, "task", task["id"], task)
        return {"id": task["id"], "created": True}


def _expire(db, now):
    for task in db.execute("SELECT * FROM tasks WHERE status IN ('leased','running')").fetchall():
        if datetime.fromisoformat(task["expires_at"]) <= now:
            db.execute("UPDATE tasks SET status='queued',error_kind='transient' WHERE id=?", (task["id"],))
            db.execute("UPDATE task_attempts SET status='expired' WHERE id=?", (task["attempt_id"],))
            event(db, "lease_expired", {"task_id": task["id"], "attempt_id": task["attempt_id"], "usage_retained": True})


def claim(run_dir, task_id, owner):
    registration, state = _control(run_dir)
    runtime = registration["spec"]["runtime"]
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("A real task owner is required")
    now = utcnow()
    deadline = datetime.fromisoformat(registration["deadline"])
    with transaction(run_dir) as db:
        _expire(db, now)
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown task")
        task = json.loads(row["payload"])
        if digest(task) != row["sha256"]:
            raise ValueError("Task brief checksum changed")
        research = task["role"] == "research"
        allowed_states = ("READY", "AWAITING_REVIEW") if research else ("READY", "AWAITING_REVIEW", "AWAITING_REFLECTION", "DEVELOPMENT_COMPLETE", "BUDGET_EXHAUSTED", "NO_NEW_HYPOTHESES")
        cutoff = deadline - timedelta(seconds=runtime["reporting_seconds"] + (runtime["shutdown_seconds"] if research else 0))
        if state["status"] not in allowed_states or now >= cutoff:
            raise ValueError("Task admissions are paused, terminal, or inside the shutdown/report reserve")
        if research and now >= datetime.fromisoformat(state["next_reflection_at"]):
            raise ValueError("Hourly reflection is due; no new research task may start")
        if state["colonies"][task["colony"]]["status"] != "ACTIVE":
            raise ValueError("Retired colony task cannot start")
        if row["status"] != "queued" or row["attempts"] >= runtime["max_attempts"]:
            raise ValueError("Task is already owned, finished, or has exhausted its retry limit")
        if row["error_kind"] == "semantic":
            raise ValueError("An unchanged semantic failure requires a new plan, not a retry")
        for dep in task["dependencies"]:
            if db.execute("SELECT status FROM tasks WHERE id=?", (dep,)).fetchone()[0] != "completed":
                raise ValueError("Task dependencies must be durably acknowledged first")
        active = db.execute("SELECT t.payload FROM task_attempts a JOIN tasks t ON t.id=a.task_id WHERE a.status IN ('leased','running','expired','released_unknown','cancellation_requested')").fetchall()
        cap = 1 if runtime["topology"] == "single" else registration["spec"]["budget"]["max_agent_tasks"]
        if len(active) + runtime["coordinator_slots"] >= cap:
            raise ValueError("Total active task slot cap reached")
        if research and sum(json.loads(r[0])["role"] == "research" for r in active) >= runtime["max_explorers"]:
            raise ValueError("Explorer slot cap reached")
        attempt_id, fence = uuid4().hex, row["fence"] + 1
        reservation = "task-" + attempt_id
        stage = "research" if research else "reporting" if task["role"] == "editor" else "verification"
        reserve_db(db, reservation, stage, task["max_units"])
        expires = min(cutoff, now + timedelta(seconds=runtime["lease_seconds"])).isoformat()
        db.execute("UPDATE tasks SET status='leased',attempts=attempts+1,fence=?,owner=?,attempt_id=?,expires_at=? WHERE id=?", (fence, owner, attempt_id, expires, task_id))
        db.execute("INSERT INTO task_attempts VALUES(?,?,?,?,?,?,?)", (attempt_id, task_id, fence, owner, "leased", expires, reservation))
        workspace = Path(run_dir).resolve() / "workspaces" / task_id / str(fence)
        workspace.mkdir(parents=True, exist_ok=True)
        brief = write(workspace / "brief.json", {**task, "owner": owner, "attempt_id": attempt_id, "fence": fence,
                       "expires_at": expires, "workspace": str(workspace), "reservation_id": reservation,
                       "permission_boundary": runtime["permission_boundary"]})
        event(db, "task_claimed", {"task_id": task_id, "attempt_id": attempt_id, "fence": fence, "owner": owner,
                                 "expires_at": expires, "reservation_id": reservation})
        return brief


def start(run_dir, task_id, attempt_id, fence, owner):
    with transaction(run_dir) as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or (row["attempt_id"], row["fence"], row["owner"]) != (attempt_id, fence, owner) or row["status"] != "leased" or utcnow() >= datetime.fromisoformat(row["expires_at"]):
            raise ValueError("Task start needs the current unexpired owner/fencing token")
        db.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
        db.execute("UPDATE task_attempts SET status='running' WHERE id=?", (attempt_id,))
        event(db, "task_started", {"task_id": task_id, "attempt_id": attempt_id})
        return {"status": "running", "task_id": task_id}


def submit(run_dir, packet):
    required = {"task_id", "attempt_id", "fence", "owner", "finding", "evidence", "assumptions", "objections", "artifacts", "checks_actually_run", "remaining_gaps", "next_action", "execution", "research_outcome"}
    if required - packet.keys() or packet["execution"] not in ("completed", "error") or packet["research_outcome"] not in ("unknown", "supported", "refuted", "inconclusive"):
        raise ValueError("Worker result needs a complete evidence packet with separate execution and research outcomes")
    forbidden = {"acceptance", "decision", "trusted_checks", "verifier_configuration"}
    if forbidden & packet.keys():
        raise ValueError("Workers cannot submit acceptance decisions or alter trusted checking controls")
    result_id = "result-" + packet["attempt_id"]
    sha = digest(packet)
    with transaction(run_dir) as db:
        old = db.execute("SELECT * FROM submissions WHERE id=?", (result_id,)).fetchone()
        if old:
            if old["sha256"] != sha:
                raise ValueError("Retransmission changed a submitted result; preserve the original")
            return {"id": result_id, "accepted_submission": bool(old["accepted"]), "acknowledged": bool(old["acknowledged"]), "retransmission": True}
        attempt = db.execute("SELECT * FROM task_attempts WHERE id=?", (packet["attempt_id"],)).fetchone()
        row = db.execute("SELECT * FROM tasks WHERE id=?", (packet["task_id"],)).fetchone()
        if attempt is None or row is None or (attempt["task_id"], attempt["fence"], attempt["owner"]) != (packet["task_id"], packet["fence"], packet["owner"]):
            raise ValueError("Result does not bind a real execution attempt and owner")
        workspace = Path(run_dir).resolve() / "workspaces" / packet["task_id"] / str(packet["fence"])
        acknowledged_artifacts = []
        for artifact in packet["artifacts"]:
            path = (workspace / artifact["path"]).resolve()
            if workspace not in path.parents or not path.is_file() or filehash(path) != artifact["sha256"]:
                raise ValueError("Artifact must exist with its declared hash inside the attempt workspace")
            stored = Path(run_dir) / "evidence" / "artifacts" / artifact["sha256"]
            stored.parent.mkdir(parents=True, exist_ok=True)
            if stored.exists() and filehash(stored) != artifact["sha256"]:
                raise ValueError("Stored artifact hash collision or corruption")
            if not stored.exists():
                shutil.copyfile(path, stored)
            acknowledged_artifacts.append(record(db, "artifact", artifact["sha256"], {**artifact, "stored_path": str(stored.resolve()), "producer_task": packet["task_id"], "attempt_id": packet["attempt_id"]}))
        current = (row["attempt_id"], row["fence"], row["owner"]) == (packet["attempt_id"], packet["fence"], packet["owner"])
        accepted = current and row["status"] in ("leased", "running") and utcnow() < datetime.fromisoformat(row["expires_at"])
        db.execute("INSERT INTO submissions(id,attempt_id,payload,sha256,accepted) VALUES(?,?,?,?,?)", (result_id, packet["attempt_id"], json.dumps(packet), sha, int(accepted)))
        record(db, "submission", result_id, {**packet, "artifact_records": acknowledged_artifacts, "eligible_current_submission": accepted})
        if accepted:
            db.execute("UPDATE tasks SET status='submitted' WHERE id=?", (packet["task_id"],))
            db.execute("UPDATE task_attempts SET status='submitted' WHERE id=?", (packet["attempt_id"],))
        else:
            db.execute("UPDATE task_attempts SET status='returned_stale' WHERE id=?", (packet["attempt_id"],))
        event(db, "task_submitted", {"id": result_id, "current": accepted, "usage_retained": True})
        return {"id": result_id, "accepted_submission": accepted, "acknowledged": False, "retransmission": False,
                "acceptance": "pending", "reservation_id": attempt["reservation_id"]}


def acknowledge(run_dir, result_id):
    with transaction(run_dir) as db:
        row = db.execute("SELECT * FROM submissions WHERE id=?", (result_id,)).fetchone()
        if row is None or not row["accepted"]:
            raise ValueError("Only an eligible durable submission can be acknowledged")
        packet = json.loads(row["payload"])
        if digest(packet) != row["sha256"]:
            raise ValueError("Submission checksum changed")
        task = db.execute("SELECT * FROM tasks WHERE id=?", (packet["task_id"],)).fetchone()
        if task["attempt_id"] != row["attempt_id"] or task["fence"] != packet["fence"]:
            raise ValueError("A stale attempt cannot complete a newer task")
        for artifact in packet["artifacts"]:
            path = Path(run_dir) / "evidence" / "artifacts" / artifact["sha256"]
            if not path.is_file() or filehash(path) != artifact["sha256"]:
                raise ValueError("Submitted artifact is no longer durable at acknowledgment")
        db.execute("UPDATE submissions SET acknowledged=1 WHERE id=?", (result_id,))
        db.execute("UPDATE tasks SET status='completed' WHERE id=?", (packet["task_id"],))
        db.execute("UPDATE task_attempts SET status='completed' WHERE id=?", (row["attempt_id"],))
        event(db, "task_acknowledged", {"id": result_id, "execution": packet["execution"], "research_outcome": packet["research_outcome"], "claim_acceptance": "pending"})
        return {"task_delivery": "completed", "execution": packet["execution"], "research_outcome": packet["research_outcome"], "acceptance": "pending"}


def release(run_dir, task_id, *, cancel=False, error_kind="semantic", reason=""):
    if error_kind not in ("transient", "semantic") or not reason:
        raise ValueError("Release needs a failure category and reason")
    with transaction(run_dir) as db:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None or row["status"] not in ("leased", "running", "submitted"):
            raise ValueError("Only an owned task can be released")
        status = "cancelled" if cancel else "queued" if error_kind == "transient" else "failed"
        db.execute("UPDATE tasks SET status=?,error_kind=? WHERE id=?", (status, error_kind, task_id))
        db.execute("UPDATE task_attempts SET status=? WHERE id=?", ("cancellation_requested" if cancel else "released_unknown", row["attempt_id"]))
        event(db, "task_released", {"task_id": task_id, "reason": reason, "status": status, "provider_cancellation_confirmed": False, "usage_retained": True})
        return {"task_delivery": status, "provider_cancellation_confirmed": False}


def confirm_stop(run_dir, attempt_id, receipt):
    if not receipt.get("provider_handle") or not receipt.get("stopped_at") or receipt.get("outcome") not in ("completed", "cancelled", "error"):
        raise ValueError("Provider stop confirmation needs a real handle, time and terminal outcome")
    with transaction(run_dir) as db:
        attempt = db.execute("SELECT * FROM task_attempts WHERE id=?", (attempt_id,)).fetchone()
        if attempt is None:
            raise ValueError("Unknown provider attempt")
        db.execute("UPDATE task_attempts SET status='provider_stopped' WHERE id=?", (attempt_id,))
        current = db.execute("SELECT status,attempt_id FROM tasks WHERE id=?", (attempt["task_id"],)).fetchone()
        if current["attempt_id"] == attempt_id and current["status"] in ("leased", "running"):
            db.execute("UPDATE tasks SET status='failed',error_kind='semantic' WHERE id=?", (attempt["task_id"],))
        event(db, "provider_stop_receipt", {"attempt_id": attempt_id, "receipt": receipt, "usage_retained": True, "receipt_validation": "host-managed"})
        return {"provider_stopped": True, "usage_refunded": False}


def task_status(run_dir):
    with transaction(run_dir) as db:
        _expire(db, utcnow())
        rows = [dict(row) for row in db.execute("SELECT id,status,attempts,fence,owner,attempt_id,expires_at,error_kind FROM tasks ORDER BY id")]
        outstanding = db.execute("SELECT COUNT(*) FROM task_attempts WHERE status IN ('leased','running','expired','released_unknown','cancellation_requested')").fetchone()[0]
        return {"tasks": rows, "leased_or_running": sum(row["status"] in ("leased", "running") for row in rows),
                "outstanding_provider_attempts": outstanding, "provider_automatically_launched": False}
