"""Independent synthetic boundary/crash tests; no historical inputs."""
from datetime import timedelta
import json
import pytest
from tests.test_v5b_next_campaign import project, terminal_packet
from v5b_next import campaign as c


def test_duplicate_seed_never_scores_twice(project, monkeypatch):
    root, clock, calls, hs = project
    monkeypatch.setattr(c, "seeds", lambda: [hs[0], hs[0]])
    state = c.epoch(root)
    assert len(calls) == 1
    assert state["unique_candidates"] == 1
    assert state["duplicates_skipped"] == 1


def test_orphan_queue_recovered_without_regeneration(project, monkeypatch):
    root, clock, calls, hs = project
    original_save = c._save
    crashed = [False]
    def save(directory, state):
        if state.get("active_queue") and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("synthetic queue attachment crash")
        return original_save(directory, state)
    monkeypatch.setattr(c, "_save", save)
    with pytest.raises(RuntimeError): c.epoch(root)
    queue = c.checked(c.locate(root) / "proposal-queue-01.json")
    monkeypatch.setattr(c, "seeds", lambda: (_ for _ in ()).throw(AssertionError("regenerated")))
    assert c.resume(root)["unique_candidates"] == 2
    assert c.checked(c.locate(root) / "proposal-queue-01.json") == queue


def test_no_scoring_when_data_load_consumes_remaining_wall_budget(project, monkeypatch):
    root, clock, calls, hs = project
    def delayed_load(root):
        clock[0] += timedelta(seconds=61)
        return {"input_bindings": {}}
    monkeypatch.setattr(c, "load_development", delayed_load)
    state = c.epoch(root)
    assert not calls, "candidate must not start after data loading crosses deadline"
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"


def test_exact_deadline_denies_first_candidate(project):
    root, clock, calls, hs = project
    clock[0] += timedelta(seconds=60)
    state = c.epoch(root)
    assert not calls
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"
    assert state["stop_reason"] == "WALL_BUDGET"


def test_stop_during_loading_denies_candidate_start(project, monkeypatch):
    root, clock, calls, hs = project
    def delayed_load(root):
        c.stop(root)
        return {"input_bindings": {}}
    monkeypatch.setattr(c, "load_development", delayed_load)
    assert c.epoch(root)["status"] == "STOPPED"
    assert not calls


def test_repeated_stopped_epoch_keeps_original_resume_target(project):
    root, clock, calls, hs = project
    c.stop(root)
    assert c.epoch(root)["status"] == "STOPPED"
    assert c.epoch(root)["status"] == "STOPPED"
    state = c.resume(root)
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"
    assert len(calls) == 2


def test_terminal_packet_cannot_admit_proposals(project):
    root, clock, calls, hs = project
    c.epoch(root)
    packet = terminal_packet(c.locate(root))
    packet["proposals"] = [hs[0]]
    path = root / "terminal.json"
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="cannot reopen"):
        c.review(root, path)
    with pytest.raises(ValueError, match="terminal actual review"):
        c.freeze(root)


def test_immutable_active_queue_rejects_rehashed_edit(project, monkeypatch):
    root, clock, calls, hs = project
    original = c._save
    def halt(directory, state):
        original(directory, state)
        if state["status"] == "RUNNING": raise RuntimeError("synthetic pre-evaluation interruption")
    monkeypatch.setattr(c, "_save", halt)
    with pytest.raises(RuntimeError): c.epoch(root)
    monkeypatch.setattr(c, "_save", original)
    path = c.locate(root) / "proposal-queue-01.json"
    queue = c.checked(path)
    queue["proposals"] = list(reversed(queue["proposals"]))
    c.write(path, c.seal(queue))
    with pytest.raises(ValueError, match="queue binding"):
        c.resume(root)
    assert not calls
