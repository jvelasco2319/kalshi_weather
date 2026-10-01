"""Auditor corruption checks on disposable synthetic campaign artifacts."""
import pytest

from tests.test_v5b_next_campaign import project, fake_result
from v5b_next import campaign as c
from scripts.verify_v5b_next_artifacts import verify


def prepare(project, monkeypatch):
    root, clock, calls, hs = project
    def evaluate(context, parameters):
        calls.append(parameters)
        return {**fake_result(parameters), "parameters": parameters, "development_only": True,
            "evaluation_partition": "development", "holdout_labels_opened": False,
            "protected_confirmation_labels_read": False, "actual_orders_placed": False}
    monkeypatch.setattr(c, "evaluate_candidate", evaluate)
    c.epoch(root)
    return root, c.locate(root), calls


def test_full_artifact_reproduction_is_read_only(project, monkeypatch):
    root, directory, calls = prepare(project, monkeypatch)
    before = {p.name: c.filehash(p) for p in directory.rglob("*.json")}
    result = verify(root, reproduce=True)
    assert result["verification"] == "PASS" and result["reproduced_candidates"] == 2
    assert result["new_experiments"] == 0
    assert before == {p.name: c.filehash(p) for p in directory.rglob("*.json")}


def test_population_corruption_detected_even_when_resealed(project, monkeypatch):
    root, directory, calls = prepare(project, monkeypatch)
    path = directory / "colony-populations.json"
    value = c.checked(path)
    value["populations"]["timing_execution"] = value["populations"]["forecast_probability"]
    c.write(path, c.seal(value))
    with pytest.raises(ValueError, match="population ownership"):
        verify(root)


def test_resealed_gate_forgery_detected(project, monkeypatch):
    root, directory, calls = prepare(project, monkeypatch)
    path = next((directory / "candidates").glob("*.json"))
    item = c.checked(path)
    item["gate_failures"] = ["sample"]
    item = c.seal(item)
    c.write(path, item)
    state = c.checked(directory / "recovery-state.json")
    state["candidate_hashes"][item["candidate_id"]] = item["self_sha256"]
    c._save(directory, state)
    with pytest.raises(ValueError, match="gate verdict"):
        verify(root)
