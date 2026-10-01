"""Synthetic path attacks; protected historical inputs are never accessed."""
import json
import os
from pathlib import Path

import pytest

from klax_lab import readiness
from klax_lab.provenance import canonical_hash, sha256_file, write_json


def safe_inputs(root):
    return (root / "runs/baselines-synthetic",
            root / "runs/local_worker_probe/synthetic/capability-report.json",
            root / "data/models/runtime_spec.json")


def frozen_path(root):
    return root / "data/manifests" / ("development-baselines-" + "a" * 64 + ".json")


def records(root, run, manifest):
    return [{"path": path.relative_to(root).as_posix(), "sha256": "0" * 64}
            for path in (run / "summary.json", manifest)]


@pytest.mark.parametrize("argument,nomination", [
    (0, "data/normalized/protected_final"),
    (0, "runs/protected_final"),
    (0, "runs/campaigns/synthetic"),
    (0, "runs/baselines-"),
    (0, "runs/baselines-synthetic/../baselines-other"),
    (1, "data/normalized/protected_final/labels/capability-report.json"),
    (1, "runs/synthetic_probe/capability-report.json"),
    (1, "runs/local_worker_probe/protected_final/capability-report.json"),
    (1, "runs/local_worker_probe/synthetic/other.json"),
    (2, "runs/runtime_spec.json"),
    (2, "data/models/protected_final/runtime_spec.json"),
    (2, "data/models/runtime_spec.txt"),
])
def test_readiness_rejects_unsafe_top_level_inputs_before_any_content_read(tmp_path, monkeypatch, argument, nomination):
    supplied = list(safe_inputs(tmp_path))
    supplied[argument] = tmp_path / nomination
    def forbidden_read(*args, **kwargs):
        raise AssertionError("Invalid input must fail before any content read")
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    monkeypatch.setattr(readiness, "sha256_file", forbidden_read)
    result = readiness.publish_readiness(tmp_path, *supplied)
    assert result["status"] == "NOT_READY_FOR_OFFLINE_CAMPAIGN"
    assert not result["goal1_complete"]


@pytest.mark.parametrize("nomination", [
    "data/normalized/protected_final/labels/synthetic-canary.json",
    "data/normalized/PrOtEcTeD_FiNaL/labels/synthetic-canary.json",
    "runs/baselines-synthetic/../../data/normalized/protected_final/labels/x.json",
    "runs/baselines-synthetic/summary.json:stream",
    "runs\\baselines-synthetic\\summary.json",
    "C:/outside/summary.json",
    "/outside/summary.json",
    "runs/baselines-other/summary.json",
    "runs/baselines-synthetic/custom_model_predictions.json",
    "runs/baselines-synthetic/independent_replication.json",
    "runs/baselines-synthetic/sensitivity/nbm_bias_corrected_bad_ledger.json",
    "data/manifests/development-baselines-other.json",
])
def test_replication_nominations_reject_unregistered_paths_before_hash(tmp_path, monkeypatch, nomination):
    run, probe, runtime = safe_inputs(tmp_path)
    manifest = {"files": [], "policy": {"synthetic": True}}
    manifest["version"] = canonical_hash(manifest)
    manifest_path = tmp_path / "data/manifests" / f"development-baselines-{manifest['version']}.json"
    write_json(manifest_path, manifest)
    summary = run / "summary.json"
    write_json(summary, {"status": "DEVELOPMENT_BASELINES_COMPLETE", "protected_final_evaluated": False,
                         "dataset_version": manifest["version"]})
    verifier = tmp_path / "src/klax_lab/replication.py"
    verifier.parent.mkdir(parents=True)
    verifier.write_text("# synthetic verifier identity only")
    used = records(tmp_path, run, manifest_path) + [{"path": nomination, "sha256": "0" * 64}]
    write_json(run / "independent_replication.json", {
        "status": "PASS", "dataset_version": manifest["version"],
        "saved_prediction_artifacts_verified": True, "verifier_sha256": sha256_file(verifier),
        "artifact_hashes": used,
    })
    hashed = []
    def guarded_hash(path):
        hashed.append(path)
        assert path == verifier, "Validate all nominated artifact paths before hashing any of them"
        return sha256_file(path)
    monkeypatch.setattr(readiness, "sha256_file", guarded_hash)
    result = readiness.publish_readiness(tmp_path, run, probe, runtime)
    assert result["status"] == "NOT_READY_FOR_OFFLINE_CAMPAIGN"
    assert hashed == [verifier]


def test_registered_baseline_output_names_and_exact_manifest_are_accepted(tmp_path):
    run, probe, runtime = safe_inputs(tmp_path)
    assert readiness.validate_readiness_input_paths(tmp_path, run, probe, runtime) == (run, probe, runtime)
    manifest = frozen_path(tmp_path)
    paths = [run / "summary.json", manifest, run / "fitted_models.json", run / "selection_eligibility.json"]
    for model in readiness._BASELINE_MODELS:
        paths.extend(run / f"{model}_{kind}.json" for kind in
                     ("predictions", "contract_probabilities", "daywise_scores", "decisions", "ledger"))
        paths.append(run / "sensitivity" / f"{model}_abcdef123456_ledger.json")
    supplied = [{"path": path.relative_to(tmp_path).as_posix(), "sha256": "0" * 64} for path in paths]
    assert readiness.validate_replication_artifact_paths(tmp_path, run, manifest, supplied) == paths


def test_replication_inventory_requires_manifest_summary_and_unique_records(tmp_path):
    run, _, _ = safe_inputs(tmp_path)
    manifest = frozen_path(tmp_path)
    good = records(tmp_path, run, manifest)
    for invalid in ([], good[:1], good[1:], good + good[:1]):
        with pytest.raises(ValueError):
            readiness.validate_replication_artifact_paths(tmp_path, run, manifest, invalid)


def test_actual_hardlink_alias_is_denied_before_read(tmp_path, monkeypatch):
    run, probe, runtime = safe_inputs(tmp_path)
    target = tmp_path / "synthetic-private-canary.json"
    target.write_text("SYNTHETIC ONLY; must never be read through the alias")
    alias = run / "summary.json"
    alias.parent.mkdir(parents=True)
    os.link(target, alias)
    assert alias.stat().st_nlink > 1
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Alias read forbidden")))
    result = readiness.publish_readiness(tmp_path, run, probe, runtime)
    assert result["status"] == "NOT_READY_FOR_OFFLINE_CAMPAIGN"
    assert "links" in result["reason"]


def test_resolved_link_redirection_is_rejected_before_content_read(tmp_path, monkeypatch):
    run, probe, runtime = safe_inputs(tmp_path)
    resolve = Path.resolve
    def redirected(path, *args, **kwargs):
        if path == probe:
            return tmp_path / "synthetic-private-canary.json"
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirected)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Link read forbidden")))
    result = readiness.publish_readiness(tmp_path, run, probe, runtime)
    assert result["status"] == "NOT_READY_FOR_OFFLINE_CAMPAIGN"
    assert "link" in result["reason"]


@pytest.mark.parametrize("nomination", [
    "data/normalized/protected_final/labels/synthetic-canary.json",
    "../outside/synthetic-canary.json", "C:/outside/synthetic-canary.json",
])
def test_runtime_support_file_cannot_nominate_protected_or_escaping_input(tmp_path, nomination):
    _, _, runtime = safe_inputs(tmp_path)
    record = {"executable": {"path": "external/synthetic/llama-completion.exe"},
              "model": {"path": "data/models/synthetic.gguf"},
              "help_file": {"path": "external/synthetic/help.txt"},
              "support_files": [{"path": nomination}]}
    write_json(runtime, record)
    with pytest.raises(ValueError):
        readiness.validate_runtime_artifact_paths(tmp_path, runtime)


def test_linked_development_manifest_input_is_rejected(tmp_path):
    canary = tmp_path / "synthetic-private-canary.py"
    canary.write_text("# synthetic only")
    alias = tmp_path / "src/klax_lab/synthetic.py"
    alias.parent.mkdir(parents=True)
    os.link(canary, alias)
    with pytest.raises(ValueError, match="links"):
        readiness.validate_development_manifest_paths(tmp_path, {"files": [{"path": "src/klax_lab/synthetic.py"}]})
