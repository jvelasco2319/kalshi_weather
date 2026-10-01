import pytest

from research_swarm.artifacts import IntegrityError, checked
from research_swarm.campaign import freeze, run_epoch, status
from test_campaign import new_run, packet_for, submit, successor


def test_each_round_requires_a_shared_evidence_synthesis(tmp_path):
    directory = new_run(tmp_path)
    packet = packet_for(directory)
    packet.pop("synthesis")
    with pytest.raises(ValueError, match="shared synthesis"):
        submit(directory, packet)


def test_follow_up_tasks_receive_the_shared_findings(tmp_path):
    directory = new_run(tmp_path)
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[successor(directory)]))
    text = (directory / "tasks/epoch-01-mechanisms.md").read_text(encoding="utf-8")
    assert "Scale mismatch needs a follow-up test" in text
    assert "scale-check" in text
    synthesis = directory / "syntheses/01.json"
    synthesis.write_text(synthesis.read_text().replace("Scale mismatch", "Invented mechanism"), encoding="utf-8")
    with pytest.raises(IntegrityError, match="seal"):
        status(directory)


def test_transfer_between_questions_requires_a_new_test_rationale(tmp_path):
    directory = new_run(tmp_path)
    proposal = successor(directory)
    state = checked(directory / "state.json")
    parent = next(key for key in state["last_candidates"] if checked(directory / "candidates" / (key + ".json"))["hypothesis"]["question_id"] == "scale-check")
    proposal["parents"] = [parent]
    proposal.pop("transfer_rationale")
    with pytest.raises(ValueError, match="Transfer between questions"):
        submit(directory, packet_for(directory, action="CONTINUE", proposals=[proposal]))


def test_partial_metric_improvement_is_not_a_supported_all_gate_lesson(tmp_path):
    directory = new_run(tmp_path)
    packet = packet_for(directory)
    packet["lessons"][0]["status"] = "SUPPORTED"
    with pytest.raises(ValueError, match="partial findings remain unresolved"):
        submit(directory, packet)


def test_easier_problem_cannot_be_frozen_as_main_answer(tmp_path):
    directory = new_run(tmp_path)
    proposal = successor(directory)
    proposal["question_id"] = "scale-check"
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[proposal]))
    run_epoch(directory)
    candidate = checked(directory / "state.json")["last_candidates"][0]
    submit(directory, packet_for(directory))
    with pytest.raises(ValueError, match="easier-problem result"):
        freeze(directory, candidate)
