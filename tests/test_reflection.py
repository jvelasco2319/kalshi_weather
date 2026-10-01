"""Synthetic fixtures test the controller; none of these reviews are agent research."""
from datetime import datetime, timedelta
import json

import pytest

from research_swarm import reflection
from research_swarm.artifacts import checked
from research_swarm.campaign import initialize, reflect, request_reflection, run_epoch, status, verify
from test_campaign import ROOT, new_run, packet_for, submit, successor


def clock_at_checkpoint(monkeypatch, directory, offset=10):
    moment = datetime.fromisoformat(checked(directory / "state.json")["next_reflection_at"]) + timedelta(seconds=offset)
    monkeypatch.setattr(reflection, "utcnow", lambda: moment)
    return moment


def reflection_packet(directory, *, targets=(), children=(), decisions=()):
    state = checked(directory / "state.json")
    request = checked(directory / "reflections" / f"{state['reflection_count'] + 1:02}-request.json")
    references = list(request["input_candidate_hashes"])
    reviews = []
    for name in ("measurement", "falsification"):
        for target in targets or (None,):
            reviews.append({"origin": "human", "reviewer_id": "synthetic-fixture-" + name,
                            "colony": name, "target_colony": target, "candidate_ids": references,
                            "findings": "TEST FIXTURE ONLY: compare reproduced gates and retain unresolved alternatives."})
    return {"checkpoint": request["checkpoint"], "request_sha256": request["self_sha256"],
            "synthesis": "TEST FIXTURE ONLY: reallocate based on the bound scorecard, preserving challenges.",
            "reviews": reviews, "new_colonies": list(children), "decisions": list(decisions)}


def reflect_packet(directory, packet):
    path = directory / "reflection-packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return reflect(directory, path)


def winning_run(tmp_path):
    directory = new_run(tmp_path)
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[successor(directory)]))
    run_epoch(directory)
    return directory


def test_hourly_checkpoint_blocks_new_experiments_and_resumes_after_review(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    before = status(directory)
    deadline = checked(directory / "registration.json")["deadline"]
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    assert status(directory)["status"] == "AWAITING_REFLECTION"
    with pytest.raises(ValueError, match="not READY"):
        run_epoch(directory)
    decision = reflect_packet(directory, reflection_packet(directory))
    assert status(directory)["status"] == "AWAITING_REVIEW"  # Epoch review remains necessary.
    assert status(directory)["attempts"] == before["attempts"]
    assert checked(directory / "registration.json")["deadline"] == deadline
    assert decision["review_duration_seconds"] == 0  # No artificial hour of idle time.
    assert decision["deadline_extended"] is False
    assert decision["counters_refunded"] is False


def test_reproduced_winner_can_fork_without_inflating_family_share(tmp_path, monkeypatch):
    directory = winning_run(tmp_path)
    parent = checked(directory / "state.json")["last_candidates"][0]
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    child = {"id": "scale_bias", "parent_colony": "mechanisms", "parent_candidates": [parent],
             "research_question": "Does bias explain remaining group differences?",
             "counter_hypothesis": "The correction could overfit the exposed groups."}
    decision = reflect_packet(directory, reflection_packet(directory, targets=["mechanisms"], children=[child]))
    colonies = status(directory)["colonies"]
    assert decision["winning_families"] == ["mechanisms"]
    assert sum(c["resource_share"] for c in colonies.values()) == pytest.approx(1)
    assert colonies["falsification"]["resource_share"] == pytest.approx(.2)
    assert colonies["mechanisms"]["resource_share"] + colonies["scale_bias"]["resource_share"] == pytest.approx(.7)
    assert colonies["formulations"]["resource_share"] == pytest.approx(.05)
    assert (directory / "tasks/epoch-02-scale_bias.md").exists()


def test_child_requires_passing_parent_and_two_nonfamily_reviews(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    child = {"id": "weak_child", "parent_colony": "mechanisms", "parent_candidates": [checked(directory / "state.json")["last_candidates"][0]],
             "research_question": "A synthetic question", "counter_hypothesis": "A synthetic counter"}
    with pytest.raises(ValueError, match="all-gate parent"):
        reflect_packet(directory, reflection_packet(directory, children=[child]))
    assert status(directory)["reflection_count"] == 0


def test_same_failed_evidence_is_not_two_failed_checkpoints(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    reflect_packet(directory, reflection_packet(directory))
    first = status(directory)["colonies"]["formulations"]["failure_checkpoints"]
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    reflect_packet(directory, reflection_packet(directory))
    assert first == 1
    assert status(directory)["colonies"]["formulations"]["failure_checkpoints"] == first


def test_retirement_needs_evidence_and_cannot_remove_challenges(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    packet = reflection_packet(directory, targets=["formulations"], decisions=[{"colony": "formulations", "action": "RETIRE", "reason": "Synthetic low progress"}])
    with pytest.raises(ValueError, match="Too little distinct"):
        reflect_packet(directory, packet)
    packet["decisions"][0]["colony"] = "falsification"
    with pytest.raises(ValueError, match="Protected adversarial"):
        reflect_packet(directory, packet)


def test_winner_requires_reviews_outside_its_family(tmp_path, monkeypatch):
    directory = winning_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    packet = reflection_packet(directory, targets=["mechanisms"])
    packet["reviews"][0]["colony"] = "mechanisms"
    with pytest.raises(ValueError, match="two independent nonfamily"):
        reflect_packet(directory, packet)


def test_reflection_packet_cannot_bind_another_request(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    packet = reflection_packet(directory)
    packet["request_sha256"] = "wrong"
    with pytest.raises(ValueError, match="exact pending request"):
        reflect_packet(directory, packet)


def test_fair_allocation_uses_available_budget_and_ignores_retired():
    state = {"colonies": {name: {"status": "ACTIVE", "resource_share": share} for name, share in (("winner", .6), ("explore", .2), ("falsification", .2))},
             "allocations_used": {"winner": 0, "explore": 0, "falsification": 0, "retired": 0}}
    state["colonies"]["retired"] = {"status": "RETIRED", "resource_share": 0}
    queue = [{"colony": name, "id": f"{name}-{i}"} for name in state["colonies"] for i in range(100)]
    chosen, pending = reflection.allocate(state, queue, 100)
    assert {name: sum(h["colony"] == name for h in chosen) for name in state["colonies"]} == {"winner": 60, "explore": 20, "falsification": 20, "retired": 0}
    assert len(pending) == 300


def test_failed_colony_can_retire_with_sufficient_distinct_new_evidence(tmp_path, monkeypatch):
    directory = new_run(tmp_path)
    clock_at_checkpoint(monkeypatch, directory)
    request_reflection(directory)
    registration, state, results = verify(directory)
    # In-memory synthetic fixtures exercise the retirement rules without changing a campaign artifact.
    state["colonies"]["formulations"].update(failure_checkpoints=1, last_trial_count=2)
    request_path = directory / "reflections/01-request.json"
    request = checked(request_path)
    card = request["scorecard"]["formulations"]
    card.update(distinct_reproduced_trials=3, new_trials=1)
    from research_swarm.artifacts import write
    write(request_path, request)
    packet = reflection_packet(directory, targets=["formulations"], decisions=[{"colony": "formulations", "action": "RETIRE", "reason": "TEST FIXTURE ONLY: three failed distinct trials over two checkpoints"}])
    reflection.apply_reflection(directory, registration, state, results, packet)
    assert state["colonies"]["formulations"]["status"] == "RETIRED"
    assert state["colonies"]["formulations"]["resource_share"] == 0


def test_checkpoint_at_candidate_boundary_defers_task_without_counting_empty_epoch(tmp_path, monkeypatch):
    directory = tmp_path / "run"
    initialize(ROOT, ROOT / "config/example_problem.json", directory)
    calls = 0
    def simulated_due(state):
        nonlocal calls
        calls += 1
        return calls >= 2
    monkeypatch.setattr("research_swarm.campaign.due", simulated_due)
    # Checkpoint crosses between entry and first candidate; start_if_due also sees the due time.
    monkeypatch.setattr(reflection, "due", simulated_due)
    value = run_epoch(directory)
    assert value["status"] == "AWAITING_REFLECTION"
    assert value["attempt_count"] == value["epoch"] == 0
    assert len(value["queue"]) == 2


def test_breakthrough_checkpoint_keeps_the_original_hourly_schedule(tmp_path):
    directory = new_run(tmp_path)
    before = status(directory)["next_reflection_at"]
    candidate = checked(directory / "state.json")["last_candidates"][0]
    request = request_reflection(directory, {"kind": "contradiction", "reason": "TEST FIXTURE ONLY: examine a competing explanation", "candidate_ids": [candidate]})
    assert request["trigger"]["kind"] == "contradiction"
    reflect_packet(directory, reflection_packet(directory))
    assert status(directory)["next_reflection_at"] == before
