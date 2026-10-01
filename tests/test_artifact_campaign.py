from pathlib import Path

import pytest

from research_swarm import evidence, tasks
from research_swarm.artifacts import IntegrityError, checked
from research_swarm.campaign import initialize, refresh_report, request_reflection, run_epoch, status
from test_campaign import ROOT
from test_runtime import result, task


def test_artifact_mode_needs_no_numeric_adapter_metric_or_fake_seed(tmp_path):
    directory = tmp_path / "artifact-run"
    initialize(ROOT, ROOT / "config/example_artifact_problem.json", directory)
    registration = checked(directory / "registration.json")
    assert "adapter" not in registration["spec"] and "primary_metric" not in registration["spec"]
    assert checked(directory / "state.json")["queue"] == []
    assert status(directory)["ranked_candidates"] == []
    assert (directory / "report.html").exists()
    with pytest.raises(ValueError, match="instead of numerical epochs"):
        run_epoch(directory)
    brief = tasks.claim(directory, task(directory), "fixture-owner")
    submitted = tasks.submit(directory, result(brief))
    tasks.acknowledge(directory, submitted["id"])
    from research_swarm.store import get_record, transaction
    with transaction(directory) as db:
        artifact = get_record(db, "submission", submitted["id"])["payload"]["artifact_records"][0]
    proposed = evidence.propose_claim(directory, {"id": "fixture-artifact-claim", "question_id": "main", "colony": "mechanisms",
        "claim": "TEST FIXTURE ONLY; no literature evaluation ran", "assumptions": [], "parents": [], "evidence": [artifact],
        "objections": [], "proposed_checks": [], "producer_result_id": submitted["id"]})
    request = request_reflection(directory, {"kind": "contradiction", "reason": "TEST FIXTURE ONLY: inspect a source conflict", "candidate_ids": [proposed["id"]]})
    assert proposed["id"] in request["input_candidate_hashes"]
    assert request["evidence_states"][proposed["id"]]["acceptance"] == "pending"
    from test_reflection import reflect_packet, reflection_packet
    reflect_packet(directory, reflection_packet(directory))
    assert status(directory)["status"] == "READY"
    assert refresh_report(directory)["claims"] == 1
    assert proposed["id"] in (directory / "report.html").read_text(encoding="utf-8")


def test_frozen_original_artifacts_are_checked_on_recovery(tmp_path):
    directory = tmp_path / "artifact-run"
    initialize(ROOT, ROOT / "config/example_artifact_problem.json", directory)
    relative = next(iter(checked(directory / "registration.json")["artifact_input_bindings"]))
    (directory / relative).write_text("Tampered fixture", encoding="utf-8")
    with pytest.raises(IntegrityError, match="artifact input changed"):
        status(directory)
