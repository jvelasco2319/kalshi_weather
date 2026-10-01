"""Synthetic state-machine tests. Never load historical data or protected labels."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from v5b_next import campaign as c
from v5b.hypotheses import _record


def fake_result(parameters):
    return {"selected_days": 35, "aggregate_realized_net_return": .2,
        "mean_expected_net_return": .3, "positive_fold_count": 4,
        "worst_nonempty_fold_return": -.05, "evidence_quality_score": .8,
        "multiclass_brier": .2, "baseline_multiclass_brier": .3,
        "adverse_stress": {"2": {"aggregate_realized_net_return": .1}},
        "best_day_removed_return": .1}


@pytest.fixture
def project(tmp_path, monkeypatch):
    clock = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(c, "now", lambda: clock[0])
    monkeypatch.setattr(c, "stamp", lambda: clock[0].isoformat())
    monkeypatch.setattr(c, "load_development", lambda root: {"input_bindings": {}})
    calls = []
    def evaluate(context, parameters):
        calls.append(parameters)
        return fake_result(parameters)
    monkeypatch.setattr(c, "evaluate_candidate", evaluate)
    hs = [_record("forecast_probability", f"fixture {i}", "Synthetic mechanism",
        {"neighbor_smoothing": i / 10}, ["Synthetic falsification"]) for i in range(2)]
    monkeypatch.setattr(c, "seeds", lambda: hs)
    config = {"allow_network": False, "allow_orders": False, "allow_protected_labels": False,
        "wall_seconds": 60, "max_epochs": 1, "max_unique_candidates": 10,
        "population_size": 2, "max_proposals_per_colony": 2,
        "minimum_selected_days": 30, "minimum_realized_return": .1,
        "minimum_expected_return": .1, "minimum_positive_folds": 4,
        "minimum_worst_fold_return": -.1, "minimum_evidence_quality": .65,
        "stress_cents": 2}
    (tmp_path / "configs").mkdir()
    (tmp_path / c.CONFIG).write_text(json.dumps(config))
    c.register(tmp_path)
    return tmp_path, clock, calls, hs


def test_candidate_write_crash_recovered_without_reevaluation(project, monkeypatch):
    root, clock, calls, hs = project
    directory = c.locate(root)
    original_write = c.write
    crashed = [False]
    def fail_after_candidate(path, obj):
        if str(path).endswith("recovery-state.json") and obj.get("queue_cursor") == 1 and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("synthetic crash after candidate commit")
        return original_write(path, obj)
    monkeypatch.setattr(c, "write", fail_after_candidate)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        c.epoch(root)
    queue_before = c.checked(directory / "proposal-queue-01.json")
    assert len(calls) == 1
    # If recovery accidentally rebuilds seeds it either fails or changes the queue.
    monkeypatch.setattr(c, "seeds", lambda: (_ for _ in ()).throw(AssertionError("queue regenerated")))
    state = c.resume(root)
    assert len(calls) == 2 and state["unique_candidates"] == 2
    assert state["duplicates_skipped"] == 0
    assert state["deadline"] == (clock[0] + timedelta(seconds=60)).isoformat()
    assert c.checked(directory / "proposal-queue-01.json") == queue_before
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"


@pytest.mark.parametrize("waiting", ["STOPPED", "AWAITING_AGENT_REVIEW", "READY"])
def test_deadline_enforced_in_inactive_states(project, waiting):
    root, clock, calls, hs = project
    directory = c.locate(root)
    state = c.checked(directory / "recovery-state.json")
    state["status"] = waiting
    c._save(directory, state)
    clock[0] += timedelta(seconds=61)
    result = c.status(root)
    assert result["status"] == "AWAITING_TERMINAL_REVIEW"
    assert result["stop_reason"] == "WALL_BUDGET"
    assert not calls
    assert c.resume(root)["status"] == "AWAITING_TERMINAL_REVIEW"


def test_cooperative_stop_preserves_queue_cursor(project, monkeypatch):
    root, clock, calls, hs = project
    def evaluate(context, params):
        calls.append(params)
        if len(calls) == 1:
            c.stop(root)
        return fake_result(params)
    monkeypatch.setattr(c, "evaluate_candidate", evaluate)
    state = c.epoch(root)
    assert state["status"] == "STOPPED" and state["queue_cursor"] == 1 and state["epoch"] == 0
    deadline = state["deadline"]
    state = c.resume(root)
    assert len(calls) == 2 and state["unique_candidates"] == 2
    assert state["deadline"] == deadline


def terminal_packet(directory):
    records = c.records_at(directory)
    leader = max(records.values(), key=c.rank)["candidate_id"]
    return {"epoch": 1, "terminal": True, "proposals": [], "reviews": [
        {"colony": colony, "reviewer": f"actual-{colony}", "findings": ["Synthetic independent audit"],
         "candidate_ids": [leader]} for colony in ("timing_execution", "robustness_adversary")]}


def test_terminal_actual_review_required_before_freeze(project):
    root, clock, calls, hs = project
    state = c.epoch(root)
    directory = c.locate(root)
    assert state["status"] == "AWAITING_TERMINAL_REVIEW"
    with pytest.raises(ValueError, match="terminal actual review"):
        c.freeze(root)
    packet = terminal_packet(directory)
    packet_path = root / "review.json"
    packet_path.write_text(json.dumps(packet))
    assert c.review(root, packet_path)["status"] == "COMPLETE"
    assert c.freeze(root)["holdout_access_authorized"] is False
    assert c.status(root)["status"] == "FROZEN"


def test_terminal_review_cannot_be_own_colony_or_single_author(project):
    root, clock, calls, hs = project
    c.epoch(root)
    packet = terminal_packet(c.locate(root))
    packet["reviews"][0]["colony"] = "forecast_probability"
    path = root / "bad-review.json"
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="not independent"):
        c.review(root, path)


def test_admission_intersects_both_validators_before_any_scoring(project, monkeypatch):
    root, clock, calls, hs = project
    # Grammar accepts strength 1001; evaluator rejects >1000.
    h = _record("forecast_probability", "out of bounds", "Synthetic", {
        "calibration": "expanding_rank_frequency", "calibration_strength": 1001}, ["test"])
    monkeypatch.setattr(c, "seeds", lambda: [hs[0], h])
    with pytest.raises(ValueError, match="calibration_strength"):
        c.epoch(root)
    assert not calls and not list((c.locate(root) / "candidates").glob("*.json"))


def test_queue_tampering_and_committed_candidate_rewrite_denied(project):
    root, clock, calls, hs = project
    c.epoch(root)
    directory = c.locate(root)
    path = next((directory / "candidates").glob("*.json"))
    item = c.checked(path)
    item["result"]["aggregate_realized_net_return"] = 99
    c.write(path, c.seal(item))
    with pytest.raises(ValueError, match="committed candidate changed"):
        c.status(root)


def test_unknown_backend_denied():
    with pytest.raises(ValueError, match="unsupported bound"):
        c.backend("some.arbitrary.module")


def test_queue_attachment_crash_reuses_saved_queue(project, monkeypatch):
    root, clock, calls, hs = project
    original_write = c.write
    crashed = [False]
    def fail_attach(path, obj):
        if str(path).endswith("recovery-state.json") and obj.get("active_queue") and not crashed[0]:
            crashed[0] = True
            raise RuntimeError("queue attachment crash")
        return original_write(path, obj)
    monkeypatch.setattr(c, "write", fail_attach)
    with pytest.raises(RuntimeError, match="attachment"):
        c.epoch(root)
    assert not calls
    monkeypatch.setattr(c, "seeds", lambda: (_ for _ in ()).throw(AssertionError("rebuilt queue")))
    assert c.resume(root)["unique_candidates"] == 2


def test_sealed_queue_replacement_detected(project, monkeypatch):
    root, clock, calls, hs = project
    def evaluate(context, parameters):
        calls.append(parameters)
        c.stop(root)
        return fake_result(parameters)
    monkeypatch.setattr(c, "evaluate_candidate", evaluate)
    state = c.epoch(root)
    directory = c.locate(root)
    path = directory / state["active_queue"]
    q = c.checked(path)
    q["proposals"] = q["proposals"][:1]
    c.write(path, c.seal(q))
    with pytest.raises(ValueError, match="queue binding differs"):
        c.resume(root)


def test_deadline_after_committed_candidate_does_not_restart(project, monkeypatch):
    root, clock, calls, hs = project
    def evaluate(context, parameters):
        calls.append(parameters)
        clock[0] += timedelta(seconds=61)
        return fake_result(parameters)
    monkeypatch.setattr(c, "evaluate_candidate", evaluate)
    state = c.epoch(root)
    assert state["unique_candidates"] == 1 and state["stop_reason"] == "WALL_BUDGET"
    assert c.resume(root)["unique_candidates"] == 1 and len(calls) == 1


def test_stop_waiting_for_review_remains_waiting_on_resume(project):
    root, clock, calls, hs = project
    directory = c.locate(root)
    state = c.checked(directory / "recovery-state.json")
    state["status"] = "AWAITING_AGENT_REVIEW"
    c._save(directory, state)
    c.stop(root)
    assert c.epoch(root)["status"] == "STOPPED"
    assert c.resume(root)["status"] == "AWAITING_AGENT_REVIEW"
    assert not calls


def test_research_artifacts_preserve_population_ownership(project):
    root, clock, calls, hs = project
    c.epoch(root)
    directory = c.locate(root)
    records = c.records_at(directory)
    front = c.checked(directory / "pareto-archive.json")
    assert front["candidate_ids"] == c.pareto(records)
    populations = c.checked(directory / "colony-populations.json")["populations"]
    for colony, ids in populations.items():
        assert all(records[key]["hypothesis"]["colony"] == colony for key in ids)
    criticism = c.checked(directory / "deterministic-criticism-ledger.json")
    assert criticism["actual_agent_review"] is False
    assert all(row["proposing_colony"] != row["reviewing_colony"] for row in criticism["checks"])
    assert len(c.checked(directory / "hypothesis-registry.json")["hypotheses"]) == 2


@pytest.mark.parametrize("key,value", [("minimum_selected_days", True),
    ("minimum_realized_return", float("nan")), ("minimum_positive_folds", 3),
    ("minimum_evidence_quality", .1), ("stress_cents", "2")])
def test_unsafe_gate_configuration_rejected(project, key, value):
    root, clock, calls, hs = project
    config = c.read(root / c.CONFIG)
    config[key] = value
    with pytest.raises(ValueError, match="gate"):
        c.validate_config(config)


def test_numerical_runtime_change_denied(project, monkeypatch):
    root, clock, calls, hs = project
    monkeypatch.setattr(c, "runtime_versions", lambda: {"python": "different"})
    with pytest.raises(ValueError, match="numerical runtime"):
        c.status(root)
