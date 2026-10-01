"""Controller tests use synthetic task results; no agent or provider is launched."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from research_swarm import resources, tasks
from research_swarm.artifacts import checked, filehash, read
from research_swarm.campaign import initialize
from test_campaign import ROOT


def fresh(tmp_path, *, runtime=None):
    spec = read(ROOT / "config/example_problem.json")
    spec["runtime"].update(runtime or {})
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    directory = tmp_path / "run"
    initialize(ROOT, path, directory)
    return directory


def task(directory, identifier="fixture-task", **updates):
    packet = {"id": identifier, "objective": "TEST FIXTURE ONLY: verify ownership protocol", "role": "research",
              "colony": "mechanisms", "dependencies": [], "evidence": [], "tools": ["write_attempt_workspace"],
              "max_units": 10, "depth": 1, "completion_rule": "Submit the fixture artifact and acknowledge it"}
    packet.update(updates)
    tasks.enqueue(directory, packet)
    return identifier


def result(brief, **updates):
    workspace = Path(brief["workspace"])
    path = workspace / "fixture.txt"
    path.write_text("TEST FIXTURE ONLY", encoding="utf-8")
    packet = {"task_id": brief["id"], "attempt_id": brief["attempt_id"], "fence": brief["fence"], "owner": brief["owner"],
              "finding": "TEST FIXTURE ONLY: result transport", "evidence": [], "assumptions": [], "objections": [],
              "artifacts": [{"path": "fixture.txt", "sha256": filehash(path), "type": "test-fixture"}],
              "checks_actually_run": [], "remaining_gaps": ["No scientific investigation ran"], "next_action": "Validate controller",
              "execution": "completed", "research_outcome": "inconclusive"}
    packet.update(updates)
    return packet


def test_atomic_claim_has_one_owner(tmp_path):
    directory = fresh(tmp_path)
    identifier = task(directory)
    def attempt(owner):
        try:
            return tasks.claim(directory, identifier, owner)
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(attempt, ["fixture-a", "fixture-b"]))
    assert sum(value is not None for value in claimed) == 1
    assert resources.budget_status(directory)["unknown_usage_reservations"] == 1


def test_retransmission_is_idempotent_and_needs_durable_acknowledgment(tmp_path):
    directory = fresh(tmp_path)
    brief = tasks.claim(directory, task(directory), "fixture-a")
    packet = result(brief)
    first = tasks.submit(directory, packet)
    assert tasks.task_status(directory)["tasks"][-1]["status"] == "submitted"
    second = tasks.submit(directory, packet)
    assert second["id"] == first["id"] and second["retransmission"]
    completed = tasks.acknowledge(directory, first["id"])
    assert completed["task_delivery"] == "completed"
    assert completed["acceptance"] == "pending"
    assert tasks.submit(directory, packet)["acknowledged"]
    packet["finding"] = "Altered story"
    with pytest.raises(ValueError, match="Retransmission changed"):
        tasks.submit(directory, packet)


def test_stale_fencing_token_cannot_complete_new_owner(tmp_path, monkeypatch):
    directory = fresh(tmp_path)
    identifier = task(directory)
    old = tasks.claim(directory, identifier, "fixture-a")
    old_packet = result(old)
    later = datetime.fromisoformat(old["expires_at"]) + timedelta(seconds=1)
    monkeypatch.setattr(tasks, "utcnow", lambda: later)
    new = tasks.claim(directory, identifier, "fixture-b")
    assert new["fence"] == old["fence"] + 1
    stale = tasks.submit(directory, old_packet)
    assert stale["accepted_submission"] is False
    with pytest.raises(ValueError, match="eligible durable"):
        tasks.acknowledge(directory, stale["id"])
    assert resources.budget_status(directory)["unknown_usage_reservations"] == 2


def test_cancellation_does_not_free_unconfirmed_calls_or_refund_usage(tmp_path):
    directory = fresh(tmp_path)
    briefs = [tasks.claim(directory, task(directory, f"fixture-{i}"), "fixture-owner") for i in range(3)]
    tasks.release(directory, briefs[0]["id"], cancel=True, reason="TEST FIXTURE: cancellation requested")
    fourth = task(directory, "fixture-four")
    with pytest.raises(ValueError, match="active task slot cap"):
        tasks.claim(directory, fourth, "fixture-owner")
    tasks.confirm_stop(directory, briefs[0]["attempt_id"], {"provider_handle": "TEST-FIXTURE-NO-PROVIDER", "stopped_at": briefs[0]["expires_at"], "outcome": "cancelled"})
    tasks.claim(directory, fourth, "fixture-owner")
    assert resources.budget_status(directory)["unknown_usage_reservations"] == 4


def test_retries_are_bounded_and_semantic_failures_need_replanning(tmp_path):
    directory = fresh(tmp_path)
    identifier = task(directory)
    tasks.claim(directory, identifier, "fixture-owner")
    tasks.release(directory, identifier, error_kind="transient", reason="TEST FIXTURE transient failure")
    tasks.claim(directory, identifier, "fixture-owner")
    tasks.release(directory, identifier, error_kind="transient", reason="TEST FIXTURE repeated transient failure")
    with pytest.raises(ValueError, match="retry limit"):
        tasks.claim(directory, identifier, "fixture-owner")
    other = task(directory, "fixture-semantic")
    tasks.claim(directory, other, "fixture-owner")
    tasks.release(directory, other, error_kind="semantic", reason="TEST FIXTURE invalid scientific approach")
    with pytest.raises(ValueError):
        tasks.claim(directory, other, "fixture-owner")


def test_artifact_path_escape_and_worker_acceptance_are_rejected(tmp_path):
    directory = fresh(tmp_path)
    brief = tasks.claim(directory, task(directory), "fixture-owner")
    packet = result(brief)
    packet["acceptance"] = "accepted"
    with pytest.raises(ValueError, match="cannot submit acceptance"):
        tasks.submit(directory, packet)
    packet.pop("acceptance")
    packet["artifacts"][0]["path"] = "../fixture.txt"
    with pytest.raises(ValueError, match="inside the attempt workspace"):
        tasks.submit(directory, packet)


def test_failed_experiment_can_complete_delivery_without_accepting_claim(tmp_path):
    directory = fresh(tmp_path)
    brief = tasks.claim(directory, task(directory), "fixture-owner")
    submitted = tasks.submit(directory, result(brief, execution="error", research_outcome="unknown"))
    value = tasks.acknowledge(directory, submitted["id"])
    assert value == {"task_delivery": "completed", "execution": "error", "research_outcome": "unknown", "acceptance": "pending"}


def test_dependencies_need_acknowledged_delivery(tmp_path):
    directory = fresh(tmp_path)
    first = task(directory, "fixture-parent")
    second = task(directory, "fixture-dependent", dependencies=[first])
    with pytest.raises(ValueError, match="acknowledged"):
        tasks.claim(directory, second, "fixture-owner")
    brief = tasks.claim(directory, first, "fixture-owner")
    submitted = tasks.submit(directory, result(brief))
    with pytest.raises(ValueError, match="acknowledged"):
        tasks.claim(directory, second, "fixture-owner")
    tasks.acknowledge(directory, submitted["id"])
    tasks.claim(directory, second, "fixture-owner")


def test_stage_reserves_and_unknown_usage_survive_cancellation(tmp_path):
    directory = fresh(tmp_path)
    resources.reserve(directory, "fixture-large", "research", 700)
    with pytest.raises(ValueError, match="reserved resources"):
        resources.reserve(directory, "fixture-extra", "research", 1)
    resources.reserve(directory, "fixture-check", "verification", 1)
    with pytest.raises(ValueError, match="unknown usage"):
        resources.transfer(directory, "research", "verification", 1, "Cannot release unknown usage")
    budget = resources.reconcile(directory, "fixture-large", conservative=True)
    assert budget["committed"]["research"] == 700
    assert budget["unknown_usage_reservations"] == 1


def test_over_budget_actual_usage_stops_later_admissions(tmp_path):
    directory = fresh(tmp_path)
    resources.reserve(directory, "fixture-call", "research", 1)
    assert resources.reconcile(directory, "fixture-call", 701)["overspent"]
    with pytest.raises(ValueError, match="global budget"):
        resources.reserve(directory, "fixture-next", "verification", 1)


def test_topology_depth_tool_and_shutdown_limits(tmp_path, monkeypatch):
    directory = fresh(tmp_path, runtime={"topology": "single", "coordinator_slots": 0, "max_explorers": 1})
    with pytest.raises(ValueError, match="Delegation depth"):
        task(directory, depth=2)
    with pytest.raises(ValueError, match="allowlist"):
        task(directory, tools=["alter_verifier"])
    tasks.claim(directory, task(directory), "fixture-owner")
    other = task(directory, "fixture-second")
    with pytest.raises(ValueError, match="slot cap"):
        tasks.claim(directory, other, "fixture-owner")
    registration = checked(directory / "registration.json")
    monkeypatch.setattr(tasks, "utcnow", lambda: datetime.fromisoformat(registration["deadline"]) - timedelta(seconds=10))
    with pytest.raises(ValueError, match="shutdown/report reserve"):
        tasks.claim(directory, other, "fixture-owner")


def test_runtime_recovers_claims_from_persistent_store(tmp_path):
    directory = fresh(tmp_path)
    brief = tasks.claim(directory, task(directory), "fixture-owner")
    # Each API opens a fresh connection; no process-local ownership is needed.
    assert tasks.task_status(directory)["outstanding_provider_attempts"] == 1
    submitted = tasks.submit(directory, result(brief))
    tasks.acknowledge(directory, submitted["id"])
    from research_swarm.store import audit
    assert audit(directory)["records"] > 0
