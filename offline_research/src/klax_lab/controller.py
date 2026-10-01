"""Durable, bounded research bookkeeping for external/manual offline workers.

This module dispatches *records*, not processes or model API calls. A lease is
worker acceptance, not proof that an experiment ran. Artifact validation is not
an OS sandbox; the caller must supply isolated workers and frozen data access.
The fixture helper demonstrates engineering only and never starts Goal 2.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Iterator
import uuid


class ControllerError(ValueError):
    """Rejected operation; authoritative state is unchanged."""


class BudgetExceeded(ControllerError):
    pass


class InvalidTransition(ControllerError):
    pass


class InvalidResult(ControllerError):
    pass


RESOURCE_KEYS = ("tokens", "compute_seconds", "paid_micros", "experiments")
ROLES = {"explorer", "implementer", "critic", "replicator", "synthesizer", "allocator", "auditor"}
MESSAGE_TYPES = {"FINDING", "QUESTION", "CONTRADICTION", "BLOCKER", "REQUEST_REPLICATION",
                 "REQUEST_DATA", "REQUEST_CHECK", "REQUEST_FALSIFICATION"}
HYPOTHESIS_TRANSITIONS = {
    "PROPOSED": {"TESTED", "REJECTED"},
    "TESTED": {"SUPPORTED", "CHALLENGED", "REJECTED"},
    "SUPPORTED": {"REPLICATED", "CHALLENGED", "REJECTED"},
    "REPLICATED": {"CHALLENGED", "CANDIDATE", "REJECTED"},
    "CHALLENGED": {"TESTED", "REJECTED"},
    "CANDIDATE": {"CHALLENGED", "REJECTED"},
    "REJECTED": set(),
}


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ControllerError("Value must be finite JSON data") from exc


def _integer(value: Any, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ControllerError(f"{name} must be an integer >= {minimum}")


def _nonempty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ControllerError(f"{name} must be nonempty text")


def _relative(value: str) -> str:
    _nonempty(value, "artifact path")
    if "\\" in value or ":" in value:
        raise ControllerError("Artifact paths must use portable relative paths")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("/"):
        raise ControllerError("Artifact path escapes the artifact root")
    return path.as_posix()


def file_sha256(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise InvalidResult("Expected a lowercase SHA-256 digest")


@dataclass(frozen=True)
class CampaignLimits:
    max_hypotheses: int = 12
    max_epochs: int = 3
    max_concurrency: int = 4
    coordinator_slots: int = 1
    max_tasks: int = 60
    max_experiments: int = 36
    max_retries: int = 2
    wall_seconds: int = 21600
    compute_seconds: int = 21600
    token_budget: int = 0
    paid_budget_micros: int = 0

    def validate(self) -> None:
        for name, value in asdict(self).items():
            _integer(value, name, 0 if name in {"max_retries", "coordinator_slots", "token_budget", "paid_budget_micros"} else 1)
        if self.coordinator_slots >= self.max_concurrency:
            raise ControllerError("At least one slot must remain for a worker")

    def resources(self) -> dict[str, int]:
        return {"tokens": self.token_budget, "compute_seconds": self.compute_seconds,
                "paid_micros": self.paid_budget_micros, "experiments": self.max_experiments}


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    campaign_id: str
    idempotency_key: str
    epoch: int
    track: str
    role: str
    question: str
    permitted_inputs: list[str]
    prohibited_inputs: list[str]
    allowed_outputs: list[str]
    success_condition: str
    failure_condition: str
    dependencies: list[str] = field(default_factory=list)
    hypothesis_id: str | None = None
    source_evidence_ids: list[str] = field(default_factory=list)
    runtime_seconds: int = 300
    token_limit: int = 0
    paid_limit_micros: int = 0
    experiment_limit: int = 1

    def validate(self) -> None:
        for name in ("task_id", "campaign_id", "idempotency_key", "question", "success_condition", "failure_condition"):
            _nonempty(getattr(self, name), name)
        _integer(self.epoch, "epoch", 1)
        if self.track not in {f"R{i:02}" for i in range(1, 37)}:
            raise ControllerError("track must be R01 through R36")
        if self.role not in ROLES:
            raise ControllerError("Unknown worker role")
        for name in ("permitted_inputs", "prohibited_inputs", "allowed_outputs", "dependencies", "source_evidence_ids"):
            value = getattr(self, name)
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value) or len(value) != len(set(value)):
                raise ControllerError(f"{name} must be a list of unique nonempty strings")
        if set(self.permitted_inputs) & set(self.prohibited_inputs):
            raise ControllerError("An input cannot be both permitted and prohibited")
        if not self.allowed_outputs:
            raise ControllerError("At least one allowed output directory is required")
        for path in self.allowed_outputs:
            if _relative(path) == ".":
                raise ControllerError("Give a dedicated task output directory, not the artifact root")
        if self.task_id in self.dependencies:
            raise ControllerError("A task cannot depend on itself")
        for name in ("runtime_seconds", "token_limit", "paid_limit_micros", "experiment_limit"):
            _integer(getattr(self, name), name, 1 if name == "runtime_seconds" else 0)
        if self.role in {"critic", "replicator"} and not self.source_evidence_ids:
            raise ControllerError("Independent review requires source evidence")
        _json(asdict(self))

    def reservation(self) -> dict[str, int]:
        return {"tokens": self.token_limit, "compute_seconds": self.runtime_seconds,
                "paid_micros": self.paid_limit_micros, "experiments": self.experiment_limit}


class Controller:
    """SQLite transactions serialize claims, results, and resource reservations."""

    def __init__(self, db_path: Path | str, artifact_root: Path | str,
                 clock: Callable[[], float] = time.time) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id TEXT PRIMARY KEY, created REAL NOT NULL, limits TEXT NOT NULL,
                mode TEXT NOT NULL, provenance TEXT NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS hypotheses (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                idem TEXT NOT NULL, spec TEXT NOT NULL, state TEXT NOT NULL,
                UNIQUE(campaign_id, idem));
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                idem TEXT NOT NULL, spec TEXT NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT, UNIQUE(campaign_id, idem));
            CREATE TABLE IF NOT EXISTS attempts (
                lease TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                campaign_id TEXT NOT NULL REFERENCES campaigns(id), worker TEXT NOT NULL,
                started REAL NOT NULL, deadline REAL NOT NULL, state TEXT NOT NULL,
                reserved TEXT NOT NULL, actual TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                task_id TEXT NOT NULL REFERENCES tasks(id), worker TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                task_id TEXT NOT NULL REFERENCES tasks(id), worker TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                idem TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(campaign_id, idem));
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                idem TEXT NOT NULL, record TEXT NOT NULL, UNIQUE(campaign_id, idem));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
                at REAL NOT NULL, kind TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS coordinator_checkpoints (
                campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id),
                payload TEXT NOT NULL, sha256 TEXT NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Controller":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _event(self, campaign_id: str, kind: str, record: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO events(campaign_id,at,kind,record) VALUES(?,?,?,?)",
                        (campaign_id, self.clock(), kind, _json(record)))

    def _campaign(self, campaign_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise ControllerError("Unknown campaign")
        return row

    def _active(self, campaign_id: str) -> tuple[sqlite3.Row, CampaignLimits]:
        row = self._campaign(campaign_id)
        if row["state"] != "ACTIVE":
            raise InvalidTransition("Campaign is not active")
        limits = CampaignLimits(**json.loads(row["limits"]))
        if self.clock() >= row["created"] + limits.wall_seconds:
            raise BudgetExceeded("Campaign wall-time ceiling reached")
        return row, limits

    def create_campaign(self, campaign_id: str, limits: CampaignLimits | None = None,
                        *, mode: str = "synthetic_fixture", provenance: dict[str, Any] | None = None) -> dict[str, Any]:
        """Real campaigns require an externally produced, hashed readiness file.

        A readiness signature is a content checksum, not a claim that this
        controller has independently audited every data and sandbox gate.
        """
        _nonempty(campaign_id, "campaign_id")
        limits = limits or CampaignLimits()
        limits.validate()
        if mode not in {"synthetic_fixture", "historical_research"}:
            raise ControllerError("Only offline fixture or historical research modes are supported")
        provenance = provenance or {}
        if mode == "historical_research":
            required = {"readiness_path", "readiness_sha256", "code_sha256", "dataset_sha256", "evaluation_policy_sha256", "backend"}
            if not required <= provenance.keys() or provenance["backend"] != "external_manual":
                raise ControllerError("Historical campaign needs readiness, code, data, policy hashes and external_manual backend")
            for name in ("readiness_sha256", "code_sha256", "dataset_sha256", "evaluation_policy_sha256"):
                _sha(provenance[name])
            readiness_path = self._artifact_path(provenance["readiness_path"])
            if file_sha256(readiness_path) != provenance["readiness_sha256"]:
                raise ControllerError("Readiness artifact hash mismatch")
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
            if readiness.get("status") != "READY_FOR_OFFLINE_CAMPAIGN":
                raise ControllerError("Goal 1 readiness is not satisfied")
            if any(readiness.get(k) != provenance[k] for k in ("code_sha256", "dataset_sha256", "evaluation_policy_sha256")):
                raise ControllerError("Readiness versions do not match campaign versions")
            if readiness.get("offline_verified") is not True or readiness.get("holdout_access_denied") is not True:
                raise ControllerError("Readiness must verify offline execution and holdout denial")
        with self._transaction():
            old = self.db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            values = (_json(asdict(limits)), mode, _json(provenance))
            if old:
                if (old["limits"], old["mode"], old["provenance"]) != values:
                    raise ControllerError("Campaign identifier already has a different specification")
            else:
                self.db.execute("INSERT INTO campaigns VALUES(?,?,?,?,?,?)", (campaign_id, self.clock(), *values, "ACTIVE"))
                self._event(campaign_id, "CREATED", {"mode": mode, "backend": "external_manual"})
        return self.status(campaign_id)

    def add_hypothesis(self, campaign_id: str, hypothesis_id: str, idempotency_key: str,
                       *, claim: str, mechanism: str, falsification: str,
                       planned_comparison: str, parent_ids: list[str] | None = None) -> str:
        spec = dict(claim=claim, mechanism=mechanism, falsification=falsification,
                    planned_comparison=planned_comparison, parent_ids=parent_ids or [])
        for name, value in {"hypothesis_id": hypothesis_id, "idempotency_key": idempotency_key,
                            "claim": claim, "mechanism": mechanism, "falsification": falsification,
                            "planned_comparison": planned_comparison}.items():
            _nonempty(value, name)
        with self._transaction():
            _, limits = self._active(campaign_id)
            old = self.db.execute("SELECT * FROM hypotheses WHERE campaign_id=? AND idem=?", (campaign_id, idempotency_key)).fetchone()
            if old:
                if old["id"] != hypothesis_id or old["spec"] != _json(spec):
                    raise ControllerError("Hypothesis idempotency key conflicts")
                return old["id"]
            count = self.db.execute("SELECT COUNT(*) FROM hypotheses WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
            if count >= limits.max_hypotheses:
                raise BudgetExceeded("Hypothesis budget exhausted")
            for parent in spec["parent_ids"]:
                if not self.db.execute("SELECT 1 FROM hypotheses WHERE id=? AND campaign_id=?", (parent, campaign_id)).fetchone():
                    raise ControllerError("Unknown parent hypothesis")
            self.db.execute("INSERT INTO hypotheses VALUES(?,?,?,?,?)", (hypothesis_id, campaign_id, idempotency_key, _json(spec), "PROPOSED"))
            self._event(campaign_id, "HYPOTHESIS_PROPOSED", {"hypothesis_id": hypothesis_id, "parents": spec["parent_ids"]})
        return hypothesis_id

    def submit_task(self, spec: TaskSpec) -> str:
        spec.validate()
        with self._transaction():
            _, limits = self._active(spec.campaign_id)
            old = self.db.execute("SELECT * FROM tasks WHERE campaign_id=? AND idem=?", (spec.campaign_id, spec.idempotency_key)).fetchone()
            if old:
                if old["id"] != spec.task_id or old["spec"] != _json(asdict(spec)):
                    raise ControllerError("Task idempotency key conflicts")
                return old["id"]
            if spec.epoch > limits.max_epochs:
                raise BudgetExceeded("Epoch budget exceeded")
            if self.db.execute("SELECT COUNT(*) FROM tasks WHERE campaign_id=?", (spec.campaign_id,)).fetchone()[0] >= limits.max_tasks:
                raise BudgetExceeded("Task budget exhausted")
            if any(v > limits.resources()[k] for k, v in spec.reservation().items()):
                raise BudgetExceeded("A task reservation exceeds the entire campaign budget")
            if spec.hypothesis_id and not self.db.execute("SELECT 1 FROM hypotheses WHERE id=? AND campaign_id=?", (spec.hypothesis_id, spec.campaign_id)).fetchone():
                raise ControllerError("Unknown hypothesis")
            # Requiring previously registered dependencies prevents dependency cycles.
            for dependency in spec.dependencies:
                if not self.db.execute("SELECT 1 FROM tasks WHERE id=? AND campaign_id=?", (dependency, spec.campaign_id)).fetchone():
                    raise ControllerError("Dependency must already exist in this campaign")
            self._evidence_records(spec.campaign_id, spec.source_evidence_ids)
            for directory in spec.allowed_outputs:
                resolved = (self.artifact_root / _relative(directory)).resolve()
                if not resolved.is_relative_to(self.artifact_root):
                    raise ControllerError("Output directory escapes artifact root through a symlink")
                for existing in self.db.execute("SELECT spec FROM tasks"):
                    for other in json.loads(existing[0])["allowed_outputs"]:
                        other_path = (self.artifact_root / other).resolve()
                        if resolved.is_relative_to(other_path) or other_path.is_relative_to(resolved):
                            raise ControllerError("Task output directories must not overlap another task's outputs")
            self.db.execute("INSERT INTO tasks(id,campaign_id,idem,spec,state,created) VALUES(?,?,?,?,?,?)",
                            (spec.task_id, spec.campaign_id, spec.idempotency_key, _json(asdict(spec)), "QUEUED", self.clock()))
            self._event(spec.campaign_id, "TASK_QUEUED", {"task_id": spec.task_id})
        return spec.task_id

    def _resources(self, campaign_id: str) -> tuple[dict[str, int], dict[str, int]]:
        spent, reserved = ({k: 0 for k in RESOURCE_KEYS}, {k: 0 for k in RESOURCE_KEYS})
        for row in self.db.execute("SELECT state,reserved,actual FROM attempts WHERE campaign_id=?", (campaign_id,)):
            target = reserved if row["state"] == "RUNNING" else spent
            resources = json.loads(row["reserved"] if row["state"] == "RUNNING" else row["actual"])
            for key in RESOURCE_KEYS:
                target[key] += resources[key]
        return spent, reserved

    def claim_task(self, campaign_id: str, worker_id: str, *, task_id: str | None = None) -> dict[str, Any] | None:
        """Atomically accept an eligible task and reserve worst-case resources."""
        _nonempty(worker_id, "worker_id")
        with self._transaction():
            campaign, limits = self._active(campaign_id)
            running = self.db.execute("SELECT COUNT(*) FROM attempts WHERE campaign_id=? AND state='RUNNING'", (campaign_id,)).fetchone()[0]
            if running >= limits.max_concurrency - limits.coordinator_slots:
                raise BudgetExceeded("All worker slots are reserved")
            if self.db.execute("SELECT 1 FROM attempts WHERE campaign_id=? AND worker=? AND state='RUNNING'", (campaign_id, worker_id)).fetchone():
                raise ControllerError("Worker already holds a running lease")
            query = "SELECT * FROM tasks WHERE campaign_id=? AND state='QUEUED'"
            params: tuple[Any, ...] = (campaign_id,)
            if task_id:
                query += " AND id=?"
                params += (task_id,)
            rows = self.db.execute(query + " ORDER BY created,id", params).fetchall()
            spent, reserved = self._resources(campaign_id)
            eligible_but_over_budget = False
            for row in rows:
                spec = TaskSpec(**json.loads(row["spec"]))
                states = [self.db.execute("SELECT state FROM tasks WHERE id=?", (dep,)).fetchone()[0] for dep in spec.dependencies]
                if any(state in {"FAILED", "CANCELLED", "BLOCKED"} for state in states):
                    self.db.execute("UPDATE tasks SET state='BLOCKED' WHERE id=?", (spec.task_id,))
                    self._event(campaign_id, "TASK_BLOCKED", {"task_id": spec.task_id, "reason": "dependency failed"})
                    continue
                if any(state != "SUCCEEDED" for state in states):
                    continue
                sources = self._evidence_records(campaign_id, spec.source_evidence_ids)
                if spec.role in {"critic", "replicator"} and any(r["worker"] == worker_id for r in sources):
                    if task_id:
                        raise ControllerError("Reviewer/replicator must have a different worker identity from source authors")
                    continue
                request = spec.reservation()
                if any(spent[k] + reserved[k] + request[k] > limits.resources()[k] for k in RESOURCE_KEYS):
                    eligible_but_over_budget = True
                    continue
                deadline = min(self.clock() + spec.runtime_seconds, campaign["created"] + limits.wall_seconds)
                lease = uuid.uuid4().hex
                self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (lease, spec.task_id, campaign_id, worker_id, self.clock(), deadline, "RUNNING", _json(request), None, None))
                self.db.execute("UPDATE tasks SET state='RUNNING',attempts=attempts+1 WHERE id=?", (spec.task_id,))
                if not self.db.execute("SELECT 1 FROM events WHERE campaign_id=? AND kind='STARTED'", (campaign_id,)).fetchone():
                    self._event(campaign_id, "STARTED", {"worker_id": worker_id, "task_id": spec.task_id, "mode": campaign["mode"]})
                self._event(campaign_id, "TASK_ACCEPTED", {"task_id": spec.task_id, "worker_id": worker_id, "lease": lease, "reserved": request})
                return {"task": asdict(spec), "lease_token": lease, "deadline": deadline,
                        "worker_id": worker_id, "mode": campaign["mode"], "backend": "external_manual",
                        "attempt_number": row["attempts"] + 1,
                        "retries_remaining": max(0, limits.max_retries - row["attempts"]),
                        "reserved": request}
            if eligible_but_over_budget:
                raise BudgetExceeded("Remaining resources cannot cover an eligible task")
            return None

    def _attempt(self, task_id: str, lease_token: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM attempts WHERE lease=? AND task_id=?", (lease_token, task_id)).fetchone()
        if not row:
            raise InvalidTransition("Unknown task lease")
        return row

    def _usage(self, usage: dict[str, int], reserved: dict[str, int]) -> dict[str, int]:
        if not isinstance(usage, dict) or set(usage) != set(RESOURCE_KEYS):
            raise InvalidResult("Actual usage requires tokens, compute_seconds, paid_micros, experiments")
        for key in RESOURCE_KEYS:
            _integer(usage[key], key)
            if usage[key] > reserved[key]:
                raise InvalidResult(f"Reported {key} exceeds reservation; lease must fail and be reviewed")
        return usage

    def _artifact_path(self, value: str) -> Path:
        path = (self.artifact_root / _relative(value)).resolve()
        if not path.is_relative_to(self.artifact_root) or not path.is_file():
            raise InvalidResult("Artifact is missing or outside the artifact root")
        return path

    def _evidence_records(self, campaign_id: str, evidence_ids: list[str]) -> list[sqlite3.Row]:
        records = []
        for evidence_id in evidence_ids:
            row = self.db.execute("SELECT * FROM evidence WHERE id=? AND campaign_id=?", (evidence_id, campaign_id)).fetchone()
            if not row:
                raise InvalidResult(f"Unknown evidence ID: {evidence_id}")
            artifact = json.loads(row["record"])
            if file_sha256(self._artifact_path(artifact["path"])) != artifact["sha256"]:
                raise InvalidResult(f"Previously registered evidence changed: {evidence_id}")
            records.append(row)
        return records

    def _validate_result(self, row: sqlite3.Row, spec: TaskSpec, result: dict[str, Any], usage: dict[str, int]) -> None:
        required = {"status", "claims", "evidence", "experiments", "limitations", "next_steps"}
        if not isinstance(result, dict) or set(result) != required or result["status"] not in {"supported", "no_improvement", "insufficient_data", "invalidated", "completed"}:
            raise InvalidResult("Result has invalid fields or scientific status")
        if any(not isinstance(result[name], list) for name in required - {"status"}):
            raise InvalidResult("Result collections must be lists")
        if any(not isinstance(value, str) for name in ("limitations", "next_steps") for value in result[name]):
            raise InvalidResult("Limitations and next steps must be text lists")
        _json(result)
        if len(result["experiments"]) != usage["experiments"]:
            raise InvalidResult("Experiment usage must match registered experiment records")
        experiment_ids = set()
        for experiment in result["experiments"]:
            if not isinstance(experiment, dict) or set(experiment) != {"experiment_id", "code_sha256", "dataset_sha256", "parameters", "metrics", "synthetic"}:
                raise InvalidResult("Invalid experiment record fields")
            _nonempty(experiment["experiment_id"], "experiment_id")
            if experiment["experiment_id"] in experiment_ids:
                raise InvalidResult("Duplicate experiment ID")
            experiment_ids.add(experiment["experiment_id"])
            _sha(experiment["code_sha256"])
            _sha(experiment["dataset_sha256"])
            if not isinstance(experiment["parameters"], dict) or not isinstance(experiment["metrics"], dict) or type(experiment["synthetic"]) is not bool:
                raise InvalidResult("Invalid experiment parameter, metric or synthetic fields")
            if any(type(v) not in {int, float} or not math.isfinite(v) for v in experiment["metrics"].values()):
                raise InvalidResult("Experiment metrics must be finite numbers")
            if self._campaign(spec.campaign_id)["mode"] == "synthetic_fixture" and not experiment["synthetic"]:
                raise InvalidResult("Fixture experiments must be explicitly synthetic")
            if self._campaign(spec.campaign_id)["mode"] == "historical_research" and experiment["synthetic"]:
                raise InvalidResult("Synthetic experiments cannot count toward historical campaign completion")
        evidence_ids = set()
        for evidence in result["evidence"]:
            if not isinstance(evidence, dict) or set(evidence) != {"evidence_id", "path", "sha256", "kind", "experiment_id"}:
                raise InvalidResult("Invalid evidence fields")
            _nonempty(evidence["evidence_id"], "evidence_id")
            _nonempty(evidence["kind"], "kind")
            _sha(evidence["sha256"])
            if evidence["evidence_id"] in evidence_ids:
                raise InvalidResult("Duplicate evidence ID")
            evidence_ids.add(evidence["evidence_id"])
            artifact_path = self._artifact_path(evidence["path"])
            allowed = [(self.artifact_root / path).resolve() for path in spec.allowed_outputs]
            if not any(artifact_path.is_relative_to(directory) for directory in allowed):
                raise InvalidResult("Artifact is outside task's allowed output directories")
            if file_sha256(artifact_path) != evidence["sha256"]:
                raise InvalidResult("Artifact content hash mismatch")
            if evidence["experiment_id"] is not None and evidence["experiment_id"] not in experiment_ids:
                raise InvalidResult("Evidence must refer to an experiment in this result")
        for experiment_id in experiment_ids:
            if not any(e["experiment_id"] == experiment_id for e in result["evidence"]):
                raise InvalidResult("Every experiment must have a hashed artifact")
        permitted_evidence = evidence_ids | set(spec.source_evidence_ids)
        for claim in result["claims"]:
            if not isinstance(claim, dict) or set(claim) != {"text", "kind", "evidence_ids"}:
                raise InvalidResult("Invalid claim fields")
            _nonempty(claim["text"], "claim text")
            if claim["kind"] not in {"finding", "speculation"} or not isinstance(claim["evidence_ids"], list):
                raise InvalidResult("Claims must identify findings versus speculation")
            if any(not isinstance(e, str) or e not in permitted_evidence for e in claim["evidence_ids"]):
                raise InvalidResult("Claim cites unavailable or unauthorized evidence")
            if claim["kind"] == "finding" and not claim["evidence_ids"]:
                raise InvalidResult("Factual findings need evidence")
        if not result["evidence"]:
            raise InvalidResult("A successful task needs at least one hashed result artifact")

    def complete_task(self, task_id: str, lease_token: str, result: dict[str, Any],
                      actual_usage: dict[str, int]) -> dict[str, Any]:
        with self._transaction():
            attempt = self._attempt(task_id, lease_token)
            outcome = _json({"result": result, "usage": actual_usage})
            if attempt["state"] == "SUCCEEDED":
                if attempt["outcome"] != outcome:
                    raise InvalidResult("Duplicate completion conflicts with accepted result")
                return result
            if attempt["state"] != "RUNNING" or self.clock() >= attempt["deadline"]:
                raise InvalidTransition("Lease is closed or expired")
            task = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task["state"] != "RUNNING":
                raise InvalidTransition("Task is not running")
            spec = TaskSpec(**json.loads(task["spec"]))
            usage = self._usage(actual_usage, json.loads(attempt["reserved"]))
            self._validate_result(attempt, spec, result, usage)
            for experiment in result["experiments"]:
                self.db.execute("INSERT INTO experiments VALUES(?,?,?,?,?)", (experiment["experiment_id"], spec.campaign_id, task_id, attempt["worker"], _json(experiment)))
            for evidence in result["evidence"]:
                self.db.execute("INSERT INTO evidence VALUES(?,?,?,?,?)", (evidence["evidence_id"], spec.campaign_id, task_id, attempt["worker"], _json(evidence)))
            self.db.execute("UPDATE attempts SET state='SUCCEEDED',actual=?,outcome=? WHERE lease=?", (_json(usage), outcome, lease_token))
            self.db.execute("UPDATE tasks SET state='SUCCEEDED',result=? WHERE id=?", (_json(result), task_id))
            self._event(spec.campaign_id, "TASK_SUCCEEDED", {"task_id": task_id, "scientific_status": result["status"], "usage": usage})
        return result

    def _fail(self, attempt: sqlite3.Row, reason: str, transient: bool, usage: dict[str, int] | None) -> str:
        task = self.db.execute("SELECT * FROM tasks WHERE id=?", (attempt["task_id"],)).fetchone()
        limits = CampaignLimits(**json.loads(self._campaign(attempt["campaign_id"])["limits"]))
        reserved = json.loads(attempt["reserved"])
        # A lost worker may already have spent its entire allocation. Never
        # reclaim unknown usage and accidentally spend the same budget twice.
        actual = reserved if usage is None else self._usage(usage, reserved)
        can_retry = transient and task["attempts"] <= limits.max_retries
        state = "QUEUED" if can_retry else "FAILED"
        self.db.execute("UPDATE attempts SET state='FAILED',actual=?,outcome=? WHERE lease=?", (_json(actual), _json({"reason": reason, "usage_unknown": usage is None}), attempt["lease"]))
        self.db.execute("UPDATE tasks SET state=? WHERE id=?", (state, task["id"]))
        self._event(attempt["campaign_id"], "TASK_RETRY_QUEUED" if can_retry else "TASK_FAILED", {"task_id": task["id"], "reason": reason, "attempt": task["attempts"], "charged_usage": actual})
        return state

    def fail_task(self, task_id: str, lease_token: str, reason: str, *, transient: bool = False,
                  actual_usage: dict[str, int] | None = None) -> str:
        _nonempty(reason, "failure reason")
        with self._transaction():
            attempt = self._attempt(task_id, lease_token)
            if attempt["state"] != "RUNNING":
                raise InvalidTransition("Only a running attempt may fail")
            return self._fail(attempt, reason, transient, actual_usage)

    def recover_timeouts(self, campaign_id: str) -> list[dict[str, str]]:
        """Safe after restart: only expired leases close; retries remain bounded."""
        recovered = []
        with self._transaction():
            self._campaign(campaign_id)
            for attempt in self.db.execute("SELECT * FROM attempts WHERE campaign_id=? AND state='RUNNING' AND deadline<=?", (campaign_id, self.clock())).fetchall():
                state = self._fail(attempt, "worker lease timed out", True, None)
                recovered.append({"task_id": attempt["task_id"], "state": state})
        return recovered

    def checkpoint(self, campaign_id: str, payload: dict[str, Any]) -> None:
        """Atomically persist a checksummed coordinator boundary, not a new budget."""
        encoded = _json(payload)
        with self._transaction():
            self._campaign(campaign_id)
            self.db.execute("INSERT OR REPLACE INTO coordinator_checkpoints VALUES(?,?,?)",
                            (campaign_id, encoded, hashlib.sha256(encoded.encode()).hexdigest()))

    def read_checkpoint(self, campaign_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM coordinator_checkpoints WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row["payload"].encode()).hexdigest() != row["sha256"]:
            raise InvalidResult("Corrupted coordinator checkpoint")
        return json.loads(row["payload"])

    def audit_recovery(self, campaign_id: str) -> None:
        """Verify durable successes before reuse; no inference or data scoring."""
        if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok" or self.db.execute("PRAGMA foreign_key_check").fetchone():
            raise InvalidResult("Registry integrity check failed")
        self.read_checkpoint(campaign_id)
        self._campaign(campaign_id)
        for task in self.db.execute("SELECT * FROM tasks WHERE campaign_id=?", (campaign_id,)).fetchall():
            spec = TaskSpec(**json.loads(task["spec"]))
            spec.validate()
            attempts = self.db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY started,lease", (task["id"],)).fetchall()
            if task["attempts"] != len(attempts):
                raise InvalidResult("Task attempt inventory differs")
            successes = [row for row in attempts if row["state"] == "SUCCEEDED"]
            if (task["state"] == "SUCCEEDED") != (len(successes) == 1) or len(successes) > 1:
                raise InvalidResult("Task success and attempt records differ")
            for attempt in attempts:
                reserved = json.loads(attempt["reserved"])
                if reserved != spec.reservation():
                    raise InvalidResult("Attempt resource reservation differs")
                if attempt["state"] != "RUNNING":
                    self._usage(json.loads(attempt["actual"]), reserved)
            if successes:
                result, attempt = json.loads(task["result"]), successes[0]
                actual = json.loads(attempt["actual"])
                if json.loads(attempt["outcome"]) != {"result": result, "usage": actual}:
                    raise InvalidResult("Saved successful attempt differs from result")
                self._validate_result(attempt, spec, result, actual)
                for table, field in (("evidence", "evidence_id"), ("experiments", "experiment_id")):
                    stored = [json.loads(row[0]) for row in self.db.execute(f"SELECT record FROM {table} WHERE task_id=?", (task["id"],))]
                    expected = result[table]
                    if {row[field]: row for row in stored} != {row[field]: row for row in expected}:
                        raise InvalidResult("Saved task artifact registry differs")
            self._evidence_records(campaign_id, spec.source_evidence_ids)

    def pause(self, campaign_id: str, reason: str = "operator requested task-boundary pause") -> None:
        with self._transaction():
            self._active(campaign_id)
            if self.db.execute("SELECT 1 FROM attempts WHERE campaign_id=? AND state='RUNNING'", (campaign_id,)).fetchone():
                raise InvalidTransition("Pause requires a completed task boundary")
            self.db.execute("UPDATE campaigns SET state='PAUSED' WHERE id=?", (campaign_id,))
            self._event(campaign_id, "PAUSED", {"reason": reason})

    def suspend(self, campaign_id: str, reason: str) -> None:
        """Fail closed after interruption without cancelling the remaining plan."""
        with self._transaction():
            campaign = self._campaign(campaign_id)
            if campaign["state"] == "RESEARCH_CYCLE_COMPLETE":
                return
            for attempt in self.db.execute("SELECT * FROM attempts WHERE campaign_id=? AND state='RUNNING'", (campaign_id,)).fetchall():
                self._fail(attempt, "interrupted coordinator: " + reason, False, None)
            self.db.execute("UPDATE campaigns SET state='RECOVERY_REQUIRED' WHERE id=?", (campaign_id,))
            self._event(campaign_id, "RECOVERY_REQUIRED", {"reason": reason})

    def resume(self, campaign_id: str, *, review_interrupted: bool = False) -> None:
        """Explicit same-campaign continuation; paused time consumes wall budget.

        Caller must hold the process-level coordinator lock. Unknown attempts
        keep their full charge and consume the original bounded retry allowance.
        """
        with self._transaction():
            self.audit_recovery(campaign_id)
            campaign = self._campaign(campaign_id)
            limits = CampaignLimits(**json.loads(campaign["limits"]))
            if campaign["state"] == "RESEARCH_CYCLE_COMPLETE":
                return
            if self.clock() >= campaign["created"] + limits.wall_seconds:
                raise BudgetExceeded("Original campaign wall-time ceiling reached; resume cannot reset it")
            if campaign["state"] not in {"PAUSED", "ACTIVE", "RECOVERY_REQUIRED"}:
                raise InvalidTransition("This terminal campaign cannot resume")
            interrupted = self.db.execute("SELECT * FROM tasks WHERE campaign_id=? AND state IN ('RUNNING','FAILED','BLOCKED','CANCELLED')", (campaign_id,)).fetchall()
            if (campaign["state"] != "PAUSED" or interrupted) and not review_interrupted:
                raise InvalidTransition("Interrupted campaign requires explicit recovery review")
            for task in interrupted:
                if task["state"] not in {"RUNNING", "FAILED"} or task["attempts"] > limits.max_retries:
                    raise InvalidTransition("Unrecoverable task or original retry ceiling reached")
                attempts = self.db.execute("SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC, rowid DESC", (task["id"],)).fetchall()
                last = attempts[0]
                if last["state"] == "RUNNING":
                    self._fail(last, "operator-reviewed interrupted attempt", False, None)
                elif not json.loads(last["outcome"]).get("usage_unknown"):
                    raise InvalidTransition("Known task failure requires separate diagnosis, not an automatic retry")
                self.db.execute("UPDATE tasks SET state='QUEUED' WHERE id=?", (task["id"],))
            self.db.execute("UPDATE campaigns SET state='ACTIVE' WHERE id=?", (campaign_id,))
            self._event(campaign_id, "RESUMED", {"review_interrupted": review_interrupted,
                         "requeued_tasks": [row["id"] for row in interrupted], "budgets_reset": False})

    def add_message(self, campaign_id: str, message_id: str, idempotency_key: str, *, sender: str,
                    kind: str, text: str, evidence_ids: list[str] | None = None,
                    recipient: str = "local_group") -> str:
        if kind not in MESSAGE_TYPES:
            raise ControllerError("Unknown message kind")
        for name, value in {"sender": sender, "text": text, "recipient": recipient}.items():
            _nonempty(value, name)
        evidence_ids = evidence_ids or []
        if kind in {"FINDING", "CONTRADICTION", "REQUEST_REPLICATION", "REQUEST_DATA",
                    "REQUEST_CHECK", "REQUEST_FALSIFICATION"} and not evidence_ids:
            raise InvalidResult("This message type needs evidence references")
        record = dict(sender=sender, kind=kind, text=text, evidence_ids=evidence_ids, recipient=recipient)
        with self._transaction():
            self._active(campaign_id)
            self._evidence_records(campaign_id, evidence_ids)
            return self._record("messages", campaign_id, message_id, idempotency_key, record, "MESSAGE")

    def _record(self, table: str, campaign_id: str, record_id: str, idempotency_key: str,
                record: dict[str, Any], event: str) -> str:
        # table is called only with constant internal names, never model output.
        _nonempty(record_id, "record_id")
        _nonempty(idempotency_key, "idempotency_key")
        old = self.db.execute(f"SELECT * FROM {table} WHERE campaign_id=? AND idem=?", (campaign_id, idempotency_key)).fetchone()
        if old:
            if old["id"] != record_id or old["record"] != _json(record):
                raise ControllerError("Idempotency key conflicts with accepted record")
            return old["id"]
        self.db.execute(f"INSERT INTO {table} VALUES(?,?,?,?)", (record_id, campaign_id, idempotency_key, _json(record)))
        self._event(campaign_id, event, {"id": record_id, **record})
        return record_id

    def record_decision(self, campaign_id: str, decision_id: str, idempotency_key: str, *,
                        kind: str, rationale: str, evidence_ids: list[str],
                        hypothesis_id: str | None = None, target_state: str | None = None) -> str:
        if kind not in {"SYNTHESIS", "GATE", "REJECTION", "BUDGET_STOP", "ALLOCATION",
                        "CONTINUE", "FORK", "COMBINE", "REPLICATE", "CHALLENGE", "STOP"}:
            raise ControllerError("Invalid decision kind")
        _nonempty(rationale, "rationale")
        record = dict(kind=kind, rationale=rationale, evidence_ids=evidence_ids,
                      hypothesis_id=hypothesis_id, target_state=target_state)
        with self._transaction():
            self._active(campaign_id)
            old = self.db.execute("SELECT * FROM decisions WHERE campaign_id=? AND idem=?", (campaign_id, idempotency_key)).fetchone()
            if old:
                if old["id"] != decision_id or old["record"] != _json(record):
                    raise ControllerError("Decision idempotency key conflicts")
                return old["id"]
            records = self._evidence_records(campaign_id, evidence_ids)
            if kind != "BUDGET_STOP" and not records:
                raise InvalidResult("Scientific decisions require evidence")
            if target_state is not None:
                row = self.db.execute("SELECT * FROM hypotheses WHERE id=? AND campaign_id=?", (hypothesis_id, campaign_id)).fetchone()
                if not row or target_state not in HYPOTHESIS_TRANSITIONS[row["state"]]:
                    raise InvalidTransition("Illegal hypothesis transition")
                source_specs = [json.loads(self.db.execute("SELECT spec FROM tasks WHERE id=?", (r["task_id"],)).fetchone()[0]) for r in records]
                if any(s["hypothesis_id"] != hypothesis_id for s in source_specs):
                    raise InvalidTransition("A hypothesis gate must cite evidence from that hypothesis")
                if target_state in {"REPLICATED", "CANDIDATE"}:
                    roles = [s["role"] for s in source_specs]
                    if "replicator" not in roles:
                        raise InvalidTransition("Replication/candidate gate requires replication evidence")
                    if target_state == "CANDIDATE" and "critic" not in roles:
                        raise InvalidTransition("Candidate gate requires independent critic evidence")
                self.db.execute("UPDATE hypotheses SET state=? WHERE id=?", (target_state, hypothesis_id))
            return self._record("decisions", campaign_id, decision_id, idempotency_key, record, "DECISION")

    def cancel_task(self, task_id: str, reason: str) -> None:
        _nonempty(reason, "cancellation reason")
        with self._transaction():
            row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["state"] not in {"QUEUED", "BLOCKED"}:
                raise InvalidTransition("Only queued or blocked tasks may be cancelled")
            self.db.execute("UPDATE tasks SET state='CANCELLED' WHERE id=?", (task_id,))
            self._event(row["campaign_id"], "TASK_CANCELLED", {"task_id": task_id, "reason": reason})

    def stop(self, campaign_id: str, reason: str) -> None:
        _nonempty(reason, "stop reason")
        with self._transaction():
            self._campaign(campaign_id)
            # Closing unknown running work charges its full reservation.
            for attempt in self.db.execute("SELECT * FROM attempts WHERE campaign_id=? AND state='RUNNING'", (campaign_id,)).fetchall():
                self._fail(attempt, f"campaign stopped: {reason}", False, None)
            self.db.execute("UPDATE tasks SET state='CANCELLED' WHERE campaign_id=? AND state='QUEUED'", (campaign_id,))
            self.db.execute("UPDATE campaigns SET state='STOPPED' WHERE id=?", (campaign_id,))
            self._event(campaign_id, "STOPPED", {"reason": reason})

    def finish(self, campaign_id: str, conclusion: str) -> dict[str, Any]:
        """Record a completed evidence cycle, never just a set of queued prompts."""
        if conclusion not in {"SUPPORTED_WITH_LIMITATIONS", "NO_IMPROVEMENT", "INSUFFICIENT_DATA", "INVALIDATED"}:
            raise ControllerError("Unknown scientific conclusion")
        with self._transaction():
            campaign, _ = self._active(campaign_id)
            if self.db.execute("SELECT 1 FROM tasks WHERE campaign_id=? AND state IN ('RUNNING','QUEUED')", (campaign_id,)).fetchone():
                raise InvalidTransition("Unfinished tasks remain")
            if not self.db.execute("SELECT 1 FROM experiments WHERE campaign_id=?", (campaign_id,)).fetchone():
                raise InvalidTransition("No experiment completed")
            if not self.db.execute("SELECT 1 FROM messages WHERE campaign_id=?", (campaign_id,)).fetchone():
                raise InvalidTransition("No evidence exchange completed")
            decisions = [json.loads(r[0]) for r in self.db.execute("SELECT record FROM decisions WHERE campaign_id=?", (campaign_id,))]
            if not any(d["kind"] == "SYNTHESIS" for d in decisions):
                raise InvalidTransition("No synthesis decision completed")
            successful_specs = [json.loads(r[0]) for r in self.db.execute("SELECT spec FROM tasks WHERE campaign_id=? AND state='SUCCEEDED'", (campaign_id,))]
            if not any(s["role"] == "replicator" for s in successful_specs) and not any(d["kind"] == "REJECTION" for d in decisions):
                raise InvalidTransition("No independent replication or rejection path completed")
            state = "FIXTURE_COMPLETE" if campaign["mode"] == "synthetic_fixture" else "RESEARCH_CYCLE_COMPLETE"
            # A historical cycle still needs external final reporting/readiness
            # review before the project may use OFFLINE_CAMPAIGN_COMPLETE.
            self.db.execute("UPDATE campaigns SET state=? WHERE id=?", (state, campaign_id))
            self._event(campaign_id, state, {"conclusion": conclusion, "goal2_complete": False})
        return self.status(campaign_id)

    def status(self, campaign_id: str) -> dict[str, Any]:
        with self._transaction():
            row = self._campaign(campaign_id)
            limits = CampaignLimits(**json.loads(row["limits"]))
            spent, reserved = self._resources(campaign_id)
            tasks = [dict(r) for r in self.db.execute("SELECT id,state,attempts FROM tasks WHERE campaign_id=? ORDER BY created,id", (campaign_id,))]
            return {"campaign_id": campaign_id, "state": row["state"], "mode": row["mode"],
                    "backend": "external_manual", "limits": asdict(limits), "spent": spent,
                    "enforcement": "Registry-level reservations; external executor must enforce process and token limits",
                    "usage_source": "External worker reports; lost workers charged their full reservation",
                    "reserved": reserved, "remaining": {k: limits.resources()[k] - spent[k] - reserved[k] for k in RESOURCE_KEYS},
                    "tasks": tasks, "experiments": self.db.execute("SELECT COUNT(*) FROM experiments WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
                    "hypotheses": self.db.execute("SELECT COUNT(*) FROM hypotheses WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
                    "messages": self.db.execute("SELECT COUNT(*) FROM messages WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
                    "decisions": self.db.execute("SELECT COUNT(*) FROM decisions WHERE campaign_id=?", (campaign_id,)).fetchone()[0],
                    "goal2_complete": False}

    def export_ledger(self, campaign_id: str) -> dict[str, Any]:
        """Read-only, JSON-ready inspection with all negative evidence preserved."""
        with self._transaction():
            self._campaign(campaign_id)
            result: dict[str, Any] = {"campaign_id": campaign_id}
            for table in ("hypotheses", "tasks", "attempts", "experiments", "evidence", "messages", "decisions", "events"):
                rows = [dict(r) for r in self.db.execute(f"SELECT * FROM {table} WHERE campaign_id=?", (campaign_id,))]
                for row in rows:
                    for key in ("spec", "result", "reserved", "actual", "outcome", "record"):
                        if key in row and row[key] is not None:
                            row[key] = json.loads(row[key])
                result[table] = rows
            return result


def run_fixture_campaign(output_dir: Path | str) -> dict[str, Any]:
    """Run a tiny synthetic worker exchange without a model, network, or market.

    Inputs and metrics are deliberately invented fixture numbers. Re-running
    requires a new output directory so prior evidence is never overwritten.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / "fixture.sqlite"
    if db_path.exists():
        raise ControllerError("Fixture output already exists; choose a fresh directory")
    artifact_root = output_dir / "artifacts"
    limits = CampaignLimits(max_hypotheses=3, max_experiments=4, token_budget=0)
    with Controller(db_path, artifact_root) as controller:
        controller.create_campaign("synthetic-fixture", limits)
        for hid in ("fixture-h1", "fixture-h2"):
            controller.add_hypothesis("synthetic-fixture", hid, hid, claim="Synthetic arithmetic improvement", mechanism="Fixture addition",
                                      falsification="Arithmetic does not match", planned_comparison="Compare toy integers")

        def submit(task_id: str, role: str, epoch: int, *, evidence: list[str] | None = None,
                   dependencies: list[str] | None = None, hypothesis: str = "fixture-h1") -> None:
            controller.submit_task(TaskSpec(task_id=task_id, campaign_id="synthetic-fixture", idempotency_key=task_id,
                epoch=epoch, track="R36", role=role, question="Verify explicitly synthetic arithmetic", permitted_inputs=["synthetic fixture"],
                prohibited_inputs=["historical market data", "protected holdout"], allowed_outputs=[task_id],
                success_condition="Toy arithmetic is reproducible", failure_condition="Unexpected arithmetic", dependencies=dependencies or [],
                hypothesis_id=hypothesis, source_evidence_ids=evidence or [], runtime_seconds=30,
                experiment_limit=0 if role == "synthesizer" else 1))

        def complete(task_id: str, worker: str, number: int, *, scientific_status: str = "completed") -> str:
            accepted = controller.claim_task("synthetic-fixture", worker, task_id=task_id)
            assert accepted
            directory = artifact_root / task_id
            directory.mkdir(parents=True, exist_ok=True)
            artifact = directory / "synthetic.json"
            artifact.write_text(_json({"synthetic": True, "value": number, "no_market_data": True}), encoding="utf-8")
            exp = accepted["task"]["experiment_limit"]
            eid = f"{task_id}-evidence"
            result = {"status": scientific_status,
                "claims": [{"text": f"Synthetic fixture value is {number}; not a trading result", "kind": "finding", "evidence_ids": [eid]}],
                "evidence": [{"evidence_id": eid, "path": artifact.relative_to(artifact_root).as_posix(), "sha256": file_sha256(artifact),
                              "kind": "synthetic_fixture", "experiment_id": f"{task_id}-experiment" if exp else None}],
                "experiments": [{"experiment_id": f"{task_id}-experiment", "code_sha256": file_sha256(Path(__file__)),
                                 "dataset_sha256": file_sha256(artifact), "parameters": {"synthetic": True}, "metrics": {"toy_value": number}, "synthetic": True}] if exp else [],
                "limitations": ["Synthetic engineering demonstration; no weather or return analysis"], "next_steps": []}
            controller.complete_task(task_id, accepted["lease_token"], result,
                                     {"tokens": 0, "compute_seconds": 1, "paid_micros": 0, "experiments": exp})
            return eid

        submit("fixture-discovery-a", "explorer", 1)
        failed_attempt = controller.claim_task("synthetic-fixture", "fixture-crashed-worker", task_id="fixture-discovery-a")
        assert failed_attempt
        controller.fail_task("fixture-discovery-a", failed_attempt["lease_token"], "Deliberate synthetic transient failure",
                             transient=True, actual_usage={"tokens": 0, "compute_seconds": 1, "paid_micros": 0, "experiments": 0})
        first = complete("fixture-discovery-a", "fixture-explorer-a", 2)
        submit("fixture-discovery-b", "explorer", 1, hypothesis="fixture-h2")
        second = complete("fixture-discovery-b", "fixture-explorer-b", -1, scientific_status="no_improvement")
        controller.record_decision("synthetic-fixture", "reject-b", "reject-b", kind="REJECTION", rationale="Synthetic second hypothesis failed its fixture criterion",
                                   evidence_ids=[second], hypothesis_id="fixture-h2", target_state="REJECTED")
        controller.add_message("synthetic-fixture", "exchange", "exchange", sender="fixture-explorer-a", kind="FINDING",
                               text="Synthetic branch A improved the toy metric; branch B failed", evidence_ids=[first, second])
        controller.add_hypothesis("synthetic-fixture", "fixture-combined", "fixture-combined", claim="Toy combined follow-up", mechanism="Combine synthetic findings",
                                  falsification="No reproducible arithmetic", planned_comparison="Independent toy recomputation", parent_ids=["fixture-h1", "fixture-h2"])
        submit("fixture-synthesis", "synthesizer", 2, evidence=[first, second], dependencies=["fixture-discovery-a", "fixture-discovery-b"], hypothesis="fixture-combined")
        synthesis = complete("fixture-synthesis", "fixture-synthesizer", 1)
        controller.record_decision("synthetic-fixture", "combine", "combine", kind="SYNTHESIS", rationale="Retain negative result while specifying a combined synthetic follow-up", evidence_ids=[first, second, synthesis])
        submit("fixture-combined-test", "implementer", 2, evidence=[synthesis], dependencies=["fixture-synthesis"], hypothesis="fixture-combined")
        combined = complete("fixture-combined-test", "fixture-implementer", 1)
        submit("fixture-replication", "replicator", 3, evidence=[combined], dependencies=["fixture-combined-test"], hypothesis="fixture-combined")
        complete("fixture-replication", "fixture-independent-replicator", 1)
        # Demonstrate the hard hypothesis and experiment ceilings. No extra work runs.
        exhaustion = []
        try:
            controller.add_hypothesis("synthetic-fixture", "over-budget", "over-budget", claim="Extra", mechanism="Extra", falsification="Extra", planned_comparison="Extra")
        except BudgetExceeded:
            exhaustion.append("hypotheses")
        submit("fixture-over-budget", "explorer", 3)
        try:
            controller.claim_task("synthetic-fixture", "fixture-extra-worker", task_id="fixture-over-budget")
        except BudgetExceeded:
            exhaustion.append("experiments")
        # The bounded cycle closes the ineligible queued task explicitly.
        controller.cancel_task("fixture-over-budget", "experiment budget exhausted")
        controller.record_decision("synthetic-fixture", "budget-stop", "budget-stop", kind="BUDGET_STOP", rationale="Configured synthetic resource ceilings reached", evidence_ids=[])
        summary = controller.finish("synthetic-fixture", "NO_IMPROVEMENT")
        summary.update({"synthetic": True, "budget_exhaustion_demonstrated": exhaustion,
                        "warning": "Engineering fixture only. No historical experiment, returns, or Goal 2 campaign occurred."})
        ledger = controller.export_ledger("synthetic-fixture")
        (output_dir / "fixture-ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        (output_dir / "fixture-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
