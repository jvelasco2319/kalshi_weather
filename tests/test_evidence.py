"""Authority/check identities below are explicitly synthetic controller fixtures."""
import json
import sqlite3

import pytest

from research_swarm import evidence, planning, tasks
from research_swarm.artifacts import IntegrityError, checked
from research_swarm.campaign import freeze, run_epoch, status
from research_swarm.store import audit, get_record, transaction
from test_campaign import new_run, packet_for, submit, successor
from test_runtime import fresh, result, task


def reviewed_winner(tmp_path, *, host_enforced=False):
    directory = fresh(tmp_path, runtime={"permission_boundary": "host_enforced" if host_enforced else "unconfigured"})
    run_epoch(directory)
    submit(directory, packet_for(directory, action="CONTINUE", proposals=[successor(directory)]))
    run_epoch(directory)
    identifier = checked(directory / "state.json")["last_candidates"][0]
    submit(directory, packet_for(directory))
    return directory, identifier


def check_packet(directory, identifier, name, index=0):
    with transaction(directory) as db:
        candidate = get_record(db, "candidate", identifier)
    registration = checked(directory / "registration.json")
    return {"id": f"fixture-check-{name}-{index}", "candidate_id": identifier, "candidate_version": candidate["version"],
            "artifact_sha256": candidate["payload"]["artifact_sha256"], "contract_version": registration["spec"]["contract"]["version"],
            "name": name, "outcome": "passed", "scope": registration["spec"]["contract"]["acceptance_scope"],
            "checker_version": "synthetic-fixture-v1", "diagnostics": "TEST FIXTURE ONLY", "issuer": "independent-domain-checker", "objections_resolved": True}


def decision_packet(directory, identifier):
    version = evidence.state_for(directory, identifier)["version"]
    contract = checked(directory / "registration.json")["spec"]["contract"]
    return {"id": "fixture-decision", "candidate_id": identifier, "candidate_version": version, "authority": "project-owner",
            "status": "accepted", "accepted_scope": contract["acceptance_scope"], "rationale": "TEST FIXTURE ONLY: validate version-matched acceptance protocol",
            "check_ids": [], "research_outcome": "supported"}


def test_gates_and_replication_do_not_grant_claim_acceptance(tmp_path):
    directory, identifier = reviewed_winner(tmp_path)
    axes = evidence.state_for(directory, identifier)
    assert axes["all_metric_gates_passed"] and axes["verification"] == "passed"
    assert axes["research_outcome"] == "unknown" and axes["acceptance"] == "pending"
    with pytest.raises(ValueError, match="Protected acceptance is unconfigured"):
        evidence.decide(directory, decision_packet(directory, identifier), authorized=True)


def test_acceptance_requires_scope_authority_and_version_matched_domain_checks(tmp_path):
    directory, identifier = reviewed_winner(tmp_path, host_enforced=True)
    packet = decision_packet(directory, identifier)
    with pytest.raises(ValueError, match="explicit authority"):
        evidence.decide(directory, packet)
    with pytest.raises(ValueError, match="required version-matched"):
        evidence.decide(directory, packet, authorized=True)
    required = checked(directory / "registration.json")["spec"]["contract"]["required_checks"]
    for name in required:
        check = check_packet(directory, identifier, name)
        evidence.import_check(directory, check)
        packet["check_ids"].append(check["id"])
    evidence.decide(directory, packet, authorized=True)
    assert evidence.state_for(directory, identifier)["acceptance"] == "accepted"
    assert evidence.state_for(directory, identifier)["research_outcome"] == "supported"


def test_negative_result_can_be_accepted_for_a_question_oriented_contract(tmp_path):
    directory = fresh(tmp_path, runtime={"permission_boundary": "host_enforced"})
    run_epoch(directory)
    identifier = checked(directory / "state.json")["last_candidates"][0]
    submit(directory, packet_for(directory))
    packet = decision_packet(directory, identifier)
    packet["research_outcome"] = "refuted"
    for name in checked(directory / "registration.json")["spec"]["contract"]["required_checks"]:
        check = check_packet(directory, identifier, name)
        evidence.import_check(directory, check)
        packet["check_ids"].append(check["id"])
    evidence.decide(directory, packet, authorized=True)
    axes = evidence.state_for(directory, identifier)
    assert axes["all_metric_gates_passed"] is False
    assert axes["acceptance"] == "accepted" and axes["research_outcome"] == "refuted"


def test_dispute_quarantines_descendants_and_memory_and_blocks_freeze(tmp_path):
    directory, identifier = reviewed_winner(tmp_path)
    with transaction(directory) as db:
        winner = get_record(db, "candidate", identifier)
    parent_id = winner["payload"]["hypothesis"]["parents"][0]
    parent_version = evidence.state_for(directory, parent_id)["version"]
    evidence.remember(directory, {"id": "fixture-method", "scope": "method", "content": "TEST FIXTURE ONLY", "conditions": "Recheck on new data",
        "references": [{"kind": "candidate", "id": identifier, "version": winner["version"]}]})
    triage = evidence.quarantine(directory, {"kind": "candidate", "id": parent_id, "version": parent_version,
                "authority": "project-owner", "reason": "TEST FIXTURE ONLY: dependency integrity dispute"})
    assert len(triage["affected"]) > 1
    assert evidence.state_for(directory, identifier)["applicability"] == "quarantined"
    assert evidence.retrieve(directory, "method") == []
    with pytest.raises(ValueError, match="quarantined"):
        freeze(directory, identifier)


def test_revalidation_does_not_silently_restore_all_dependents(tmp_path):
    directory, identifier = reviewed_winner(tmp_path)
    version = evidence.state_for(directory, identifier)["version"]
    evidence.remember(directory, {"id": "fixture-memory", "scope": "project", "content": "TEST FIXTURE ONLY", "conditions": "Toy input only",
        "references": [{"kind": "candidate", "id": identifier, "version": version}]})
    evidence.quarantine(directory, {"kind": "candidate", "id": identifier, "version": version, "authority": "project-owner", "reason": "TEST FIXTURE ONLY"})
    packet = check_packet(directory, identifier, "fresh-revalidation")
    evidence.import_check(directory, packet)
    response = evidence.revalidate(directory, {"kind": "candidate", "id": identifier, "version": version, "authority": "project-owner",
        "reason": "TEST FIXTURE ONLY: new checks", "check_ids": [packet["id"]]}, authorized=True)
    assert response["dependents_require_separate_revalidation"]
    assert evidence.retrieve(directory, "project") == []


def test_record_tampering_is_detected(tmp_path):
    directory = new_run(tmp_path)
    with sqlite3.connect(directory / "runtime.sqlite3") as db:
        db.execute("UPDATE records SET payload='{}' WHERE kind='source'")
    with pytest.raises(IntegrityError, match="record checksum"):
        status(directory)


def test_quarantine_projection_cannot_be_reset_without_revalidation(tmp_path):
    directory = new_run(tmp_path)
    identifier = checked(directory / "state.json")["last_candidates"][0]
    version = evidence.state_for(directory, identifier)["version"]
    evidence.quarantine(directory, {"kind": "candidate", "id": identifier, "version": version, "authority": "project-owner", "reason": "TEST FIXTURE ONLY"})
    with sqlite3.connect(directory / "runtime.sqlite3") as db:
        db.execute("UPDATE applicability SET status='active' WHERE kind='candidate'")
    with pytest.raises(IntegrityError, match="applicability"):
        status(directory)


def test_memory_scopes_remain_separate_and_tentative(tmp_path):
    directory = fresh(tmp_path)
    reference = [{"kind": "source", "id": "development-input", "version": 1}]
    for scope in ("project", "method"):
        evidence.remember(directory, {"id": "fixture-" + scope, "scope": scope, "content": "TEST FIXTURE ONLY",
            "conditions": "Synthetic example only; check applicability on a new task", "references": reference})
    project = evidence.retrieve(directory, "project")
    method = evidence.retrieve(directory, "method")
    assert len(project) == len(method) == 1
    assert project[0]["id"] != method[0]["id"]
    assert method[0]["payload"]["acceptance"] == "pending"


def test_non_numeric_claim_uses_acknowledged_artifacts_and_fresh_reviews(tmp_path):
    directory = fresh(tmp_path)
    brief = tasks.claim(directory, task(directory), "fixture-owner")
    submitted = tasks.submit(directory, result(brief))
    tasks.acknowledge(directory, submitted["id"])
    with transaction(directory) as db:
        artifact = get_record(db, "submission", submitted["id"])["payload"]["artifact_records"][0]
    proposal = {"id": "fixture-literature-claim", "question_id": "main", "colony": "mechanisms", "claim": "TEST FIXTURE ONLY; no literature conclusion",
        "assumptions": [], "parents": [], "evidence": [artifact], "objections": [], "proposed_checks": ["Source support"], "producer_result_id": submitted["id"]}
    proposed = evidence.propose_claim(directory, proposal)
    reviews = [{"reviewer_id": "fixture-" + colony, "colony": colony, "origin": "human", "findings": "TEST FIXTURE ONLY",
                "recommendation": "ready", "fresh_context": True} for colony in ("measurement", "falsification")]
    evidence.review_claim(directory, {"candidate_id": proposed["id"], "candidate_version": proposed["version"], "rubric": "TEST FIXTURE ONLY", "reviews": reviews})
    axes = evidence.state_for(directory, proposed["id"])
    assert axes["review"] == "ready" and axes["verification"] == "unchecked" and axes["acceptance"] == "pending"


def test_operational_plan_cannot_change_acceptance_contract(tmp_path):
    directory = fresh(tmp_path)
    packet = {"rationale": "TEST FIXTURE ONLY", "uncertainties": [], "dependencies": [], "next_tasks": [], "useful_progress": [], "next_discriminating_test": "A synthetic test"}
    planning.update_plan(directory, packet)
    packet["gates"] = []
    with pytest.raises(ValueError, match="frozen target"):
        planning.update_plan(directory, packet)


def test_budget_stop_is_not_objective_satisfaction_and_delivery_stops_admissions(tmp_path):
    directory = fresh(tmp_path)
    packet = {"completion_class": "objective_satisfied_within_scope", "rationale": "TEST FIXTURE ONLY", "decision_ids": [],
              "unresolved": ["No research took place"], "excluded_runs": [], "next_action": "Run actual research"}
    with pytest.raises(ValueError, match="Budget exhaustion"):
        planning.finish(directory, packet)
    packet["completion_class"] = "inconclusive"
    delivered = planning.finish(directory, packet)
    assert delivered["claim_outcomes_remain_separate"]
    with pytest.raises(ValueError, match="delivered"):
        run_epoch(directory)
    with pytest.raises(ValueError, match="admissions"):
        tasks.claim(directory, task(directory), "fixture-owner")
