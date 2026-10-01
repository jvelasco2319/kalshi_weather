"""Durable records shared by the task runtime and evidence graph."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .artifacts import IntegrityError, canonical, checked, digest, utcnow


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
 kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
 payload TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(kind,id,version));
CREATE TABLE IF NOT EXISTS dependencies (
 kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
 dependency_kind TEXT NOT NULL, dependency_id TEXT NOT NULL, dependency_version INTEGER NOT NULL,
 PRIMARY KEY(kind,id,version,dependency_kind,dependency_id,dependency_version));
CREATE TABLE IF NOT EXISTS events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL,
 previous_sha256 TEXT, sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applicability (
 kind TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,
 PRIMARY KEY(kind,id,version));
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY,payload TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,fence INTEGER NOT NULL DEFAULT 0,
 owner TEXT,attempt_id TEXT,expires_at TEXT,error_kind TEXT);
CREATE TABLE IF NOT EXISTS task_attempts (
 id TEXT PRIMARY KEY,task_id TEXT NOT NULL,fence INTEGER NOT NULL,owner TEXT NOT NULL,
 status TEXT NOT NULL,expires_at TEXT NOT NULL,reservation_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,
 accepted INTEGER NOT NULL,acknowledged INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reservations (
 id TEXT PRIMARY KEY,stage TEXT NOT NULL,reserved_units REAL NOT NULL,
 actual_units REAL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS allocations (stage TEXT PRIMARY KEY,units REAL NOT NULL);
"""


@contextmanager
def transaction(run_dir):
    path = Path(run_dir) / "runtime.sqlite3"
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def event(db, kind, payload):
    last = db.execute("SELECT sha256 FROM events ORDER BY sequence DESC LIMIT 1").fetchone()
    body = {"kind": kind, "payload": payload, "previous_sha256": last[0] if last else None,
            "created_at": utcnow().isoformat()}
    sha = digest(body)
    db.execute("INSERT INTO events(kind,payload,previous_sha256,sha256,created_at) VALUES(?,?,?,?,?)",
               (kind, canonical(payload), body["previous_sha256"], sha, body["created_at"]))
    return sha


def record(db, kind, identifier, payload, dependencies=()):
    last = db.execute("SELECT version FROM records WHERE kind=? AND id=? ORDER BY version DESC LIMIT 1", (kind, identifier)).fetchone()
    version = 1 if last is None else last[0] + 1
    for dep in dependencies:
        existing = db.execute("SELECT sha256 FROM records WHERE kind=? AND id=? AND version=?", tuple(dep)).fetchone()
        if existing is None:
            raise ValueError("Record dependency does not exist at the cited version")
    body = {"kind": kind, "id": identifier, "version": version, "payload": payload}
    sha = digest(body)
    db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (kind, identifier, version, canonical(payload), sha, utcnow().isoformat()))
    db.execute("INSERT INTO applicability VALUES(?,?,?,?)", (kind, identifier, version, "active"))
    for dep in dependencies:
        db.execute("INSERT INTO dependencies VALUES(?,?,?,?,?,?)", (kind, identifier, version, *dep))
    event(db, "record_created", {"kind": kind, "id": identifier, "version": version, "sha256": sha})
    return {"kind": kind, "id": identifier, "version": version, "sha256": sha}


def get_record(db, kind, identifier, version=None):
    clause = " AND version=?" if version is not None else " ORDER BY version DESC LIMIT 1"
    args = (kind, identifier, version) if version is not None else (kind, identifier)
    row = db.execute("SELECT * FROM records WHERE kind=? AND id=?" + clause, args).fetchone()
    if row is None:
        raise ValueError("Unknown record version")
    body = {"kind": row["kind"], "id": row["id"], "version": row["version"], "payload": json.loads(row["payload"])}
    if digest(body) != row["sha256"]:
        raise IntegrityError("Versioned record checksum changed")
    applicability = db.execute("SELECT status FROM applicability WHERE kind=? AND id=? AND version=?", (kind, identifier, row["version"])).fetchone()[0]
    return {**body, "sha256": row["sha256"], "applicability": applicability}


def audit(run_dir):
    with transaction(run_dir) as db:
        for row in db.execute("SELECT kind,id,version FROM records").fetchall():
            get_record(db, *tuple(row))
        previous, projections, bindings, costs, attempts = None, {}, {}, {}, {}
        allocations = dict(checked(Path(run_dir) / "registration.json")["spec"]["runtime"]["allocations"])
        for row in db.execute("SELECT * FROM events ORDER BY sequence"):
            body = {"kind": row["kind"], "payload": json.loads(row["payload"]), "previous_sha256": row["previous_sha256"], "created_at": row["created_at"]}
            if previous != row["previous_sha256"] or digest(body) != row["sha256"]:
                raise IntegrityError("Runtime event chain changed")
            previous = row["sha256"]
            payload = body["payload"]
            if row["kind"] == "record_created":
                key = (payload["kind"], payload["id"], payload["version"])
                projections[key], bindings[key] = "active", payload["sha256"]
            elif row["kind"] == "integrity_quarantine":
                for node in payload["affected"]:
                    projections[tuple(node)] = "quarantined"
            elif row["kind"] == "record_revalidated":
                projections[(payload["kind"], payload["id"], payload["version"])] = "active"
            elif row["kind"] == "budget_reserved":
                costs[payload["id"]] = {"stage": payload["stage"], "reserved_units": payload["units"], "actual_units": None}
            elif row["kind"] == "usage_reconciled":
                costs[payload["id"]]["actual_units"] = payload["actual_units"]
            elif row["kind"] == "budget_transferred":
                allocations[payload["source"]] -= payload["units"]
                allocations[payload["target"]] += payload["units"]
            elif row["kind"] == "task_claimed":
                attempts[payload["attempt_id"]] = payload
        actual_projection = {tuple(r[:3]): r[3] for r in db.execute("SELECT kind,id,version,status FROM applicability")}
        actual_bindings = {tuple(r[:3]): r[3] for r in db.execute("SELECT kind,id,version,sha256 FROM records")}
        if projections != actual_projection or bindings != actual_bindings:
            raise IntegrityError("Evidence applicability or version bindings diverged from the event history")
        if allocations != dict(db.execute("SELECT stage,units FROM allocations").fetchall()):
            raise IntegrityError("Budget allocations changed outside a recorded transfer")
        actual_costs = {row["id"]: {key: row[key] for key in ("stage", "reserved_units", "actual_units")} for row in db.execute("SELECT * FROM reservations")}
        if costs != actual_costs:
            raise IntegrityError("Usage reservations diverged from the recorded ledger")
        actual_attempts = db.execute("SELECT * FROM task_attempts").fetchall()
        if set(attempts) != {a["id"] for a in actual_attempts}:
            raise IntegrityError("Task attempts diverged from the claim record")
        for attempt in actual_attempts:
            claimed = attempts[attempt["id"]]
            if any(attempt[key] != claimed[key] for key in ("task_id", "fence", "owner", "expires_at", "reservation_id")):
                raise IntegrityError("Task attempt ownership or fencing changed")
        for task in db.execute("SELECT * FROM tasks"):
            if get_record(db, "task", task["id"])["payload"] != json.loads(task["payload"]) or digest(json.loads(task["payload"])) != task["sha256"]:
                raise IntegrityError("Task brief diverged from its immutable record")
            recorded_attempts = [a for a in actual_attempts if a["task_id"] == task["id"]]
            if task["attempts"] != len(recorded_attempts) or task["fence"] != max((a["fence"] for a in recorded_attempts), default=0):
                raise IntegrityError("Task counters or fencing were refunded")
        return {"records": db.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                "events": db.execute("SELECT COUNT(*) FROM events").fetchone()[0], "event_head": previous}
