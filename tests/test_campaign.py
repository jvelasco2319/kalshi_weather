from pathlib import Path
import json

import pytest

from research_swarm.artifacts import IntegrityError, checked, digest, read
from research_swarm.campaign import confirm, freeze, initialize, pause, resume, review, run_epoch, status


ROOT = Path(__file__).resolve().parents[1]


def new_run(tmp_path):
    directory = tmp_path / "campaign"
    initialize(ROOT, ROOT / "config/example_problem.json", directory)
    run_epoch(directory)
    return directory


def packet_for(directory, *, action="STOP", proposals=()):
    state = checked(directory / "state.json")
    reviews = []
    # Synthetic review fixtures test validation; they do not claim actual agent work.
    for name in ("measurement", "falsification"):
        reviews.append({"origin": "human", "reviewer_id": "test-fixture-" + name,
                        "colony": name, "candidate_ids": state["last_candidates"],
                        "findings": "TEST FIXTURE ONLY: metrics were numerically reproduced; examine alternative slopes."})
    return {"epoch": state["epoch"], "action": action, "reviews": reviews, "proposals": list(proposals),
            "synthesis": "TEST FIXTURE ONLY: test slope correction while preserving the group gates.",
            "lessons": [{"status": "UNRESOLVED", "claim": "Scale mismatch needs a follow-up test.", "candidate_ids": state["last_candidates"]}]}


def submit(directory, packet):
    path = directory / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return review(directory, path)


def successor(directory):
    parent = checked(directory / "state.json")["last_candidates"][0]
    return {"id": "test-successor", "colony": "mechanisms", "question_id": "main", "title": "Test doubled slope",
            "mechanism": "Scale mismatch", "counter_hypothesis": "Bias could explain residuals",
            "parameters": {"slope": 2.0, "bias": 0.0}, "parents": [parent], "operation": "FORK",
            "transfer_rationale": "TEST FIXTURE ONLY: check the slope insight against all main-question gates.",
            "falsification_plan": "Inspect worst group and reproduce predictions."}


def test_real_review_required_between_epochs(tmp_path):
    directory = new_run(tmp_path)
    assert status(directory)["status"] == "AWAITING_REVIEW"
    with pytest.raises(ValueError, match="not READY"):
        run_epoch(directory)
    packet = packet_for(directory)
    packet["reviews"] = packet["reviews"][:1]
    with pytest.raises(ValueError, match="two independent"):
        submit(directory, packet)


def test_colony_cannot_review_itself(tmp_path):
    directory = new_run(tmp_path)
    packet = packet_for(directory)
    packet["reviews"][0]["colony"] = "formulations"
    with pytest.raises(ValueError, match="own independent"):
        submit(directory, packet)


def test_iterative_successor_freeze_and_one_shot_confirmation(tmp_path):
    directory = new_run(tmp_path)
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[successor(directory)]))
    run_epoch(directory)
    candidate_id = "candidate-" + digest({"slope": 2.0, "bias": 0.0})
    # The follow-up proposing colony is mechanisms; measurement/falsification review it.
    submit(directory, packet_for(directory))
    frozen = freeze(directory, candidate_id)
    assert frozen["status"] == "DEVELOPMENT_WINNER_NOT_CONFIRMED"
    heldout = tmp_path / "synthetic_confirmation.json"
    data = read(ROOT / "examples/synthetic_development.json")
    for row in data:
        row["y"] += 0.03
    heldout.write_text(json.dumps(data), encoding="utf-8")
    final = confirm(directory, heldout, authorized=True)
    assert final["conclusion"] == "CONFIRMATION_GATES_PASSED"
    with pytest.raises(ValueError, match="already consumed"):
        confirm(directory, heldout, authorized=True)


def test_duplicate_narrative_does_not_create_new_experiment(tmp_path):
    directory = new_run(tmp_path)
    duplicate = read(ROOT / "config/example_problem.json")["seeds"][0]
    duplicate["id"] = "different-story"
    duplicate["title"] = "New narrative, identical executable parameters"
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[duplicate]))
    state = run_epoch(directory)
    assert state["status"] == "NO_NEW_HYPOTHESES"
    assert state["attempt_count"] == 2
    assert state["duplicates_skipped"] == 1


def test_unregistered_parameter_and_foreign_parent_are_rejected(tmp_path):
    directory = new_run(tmp_path)
    proposal = successor(directory)
    proposal["parameters"]["slope"] = 999
    with pytest.raises(ValueError, match="Unregistered value"):
        submit(directory, packet_for(directory, action="CONTINUE", proposals=[proposal]))
    proposal["parameters"]["slope"] = 2.0
    proposal["parents"] = ["invented-candidate"]
    with pytest.raises(ValueError, match="Unknown parent"):
        submit(directory, packet_for(directory, action="CONTINUE", proposals=[proposal]))


def test_tampered_data_blocks_next_round(tmp_path):
    directory = new_run(tmp_path)
    (directory / "development-input.json").write_text("[]", encoding="utf-8")
    with pytest.raises(IntegrityError, match="input changed"):
        status(directory)


def test_pause_resume_preserves_absolute_deadline_and_counters(tmp_path):
    directory = new_run(tmp_path)
    before = checked(directory / "registration.json")
    attempts = status(directory)["attempts"]
    pause(directory)
    value = resume(directory)
    assert value["status"] == "AWAITING_REVIEW"
    assert checked(directory / "registration.json")["deadline"] == before["deadline"]
    assert value["attempts"] == attempts


def test_final_read_requires_freeze_and_explicit_authorization(tmp_path):
    directory = new_run(tmp_path)
    with pytest.raises(ValueError, match="authorize-one-shot"):
        confirm(directory, tmp_path / "does-not-exist.json")
    assert not (directory / "confirmation-claim.json").exists()


def test_failed_gates_cannot_be_frozen(tmp_path):
    directory = new_run(tmp_path)
    candidate_id = checked(directory / "state.json")["last_candidates"][0]
    submit(directory, packet_for(directory))
    with pytest.raises(ValueError, match="all-gate"):
        freeze(directory, candidate_id)


def test_reproduction_failure_consumes_attempt_and_preserves_remaining_queue(tmp_path, monkeypatch):
    directory = tmp_path / "campaign"
    initialize(ROOT, ROOT / "config/example_problem.json", directory)
    from research_swarm.adapters.example import evaluate
    from research_swarm.artifacts import write
    def worker(registration, run_dir, parameters, prefix, remaining, replicate_path=None, data_path=None):
        result = {"verified": False} if replicate_path else evaluate(parameters, read(run_dir / "development-input.json"))
        path = run_dir / "work" / (prefix + "-test-output.json")
        return write(path, result), path
    monkeypatch.setattr("research_swarm.campaign._worker", worker)
    value = run_epoch(directory)
    assert value["status"] == "INTEGRITY_FAILURE"
    assert value["attempt_count"] == 1
    assert len(value["queue"]) == 1
    assert status(directory)["ranked_candidates"][0]["passed"] is False
