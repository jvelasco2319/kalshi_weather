"""Synthetic fixtures only: no historical outcomes or source directories read."""
from copy import deepcopy
from datetime import date, timedelta
import json
from math import sin
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import klax_lab.experiments as module
from klax_lab.baseline import development_paths, utc
from klax_lab.models import BASELINE_DEFINITIONS
from klax_lab.provenance import canonical_hash, freeze_manifest, inventory, write_json
from klax_lab.research_plan import make_plan


SPEC = {"gfs_weight": .5, "bias_mode": "global", "spread_mode": "global", "spread_scale": 1,
        "disagreement_coefficient": 0}


def build_dataset(tmp_path, training_days=120, stride=3, yes_ask=".30", yes_bid=".20"):
    root = tmp_path / "synthetic_project"
    source = Path(module.__file__).parent
    policy = json.loads((source.parents[1] / "configs/evaluation.json").read_text())
    policy["bootstrap_samples"] = 20
    training, climate = [], []
    for index in range(training_days):
        day = (date(2024, 1, 1) + timedelta(days=index * stride)).isoformat()
        gfs = 60 + 5 * sin(index / 14)
        training.append({"climate_date": day, "gfs": gfs, "nbm": gfs + 1.5,
                         "available_at": day + "T06:00:00Z"})
        climate.append({"climate_date": day, "tmax_f": round(gfs + 2 + sin(index)),
                        "available_at": (date.fromisoformat(day) + timedelta(days=1)).isoformat() + "T10:00:00Z",
                        "source_sha256": "synthetic-label-" + day})
    selections, labels, contracts, candles, outcomes, reconciliation = [], [], [], [], [], []
    for index in range(3):
        day = f"2025-01-{5 + index:02d}"
        next_day = f"2025-01-{6 + index:02d}"
        selections.append({"climate_date": day, "gfs": 61. + index, "nbm": 62. + index, "available_at": day + "T06:00:00Z"})
        labels.append({"climate_date": day, "tmax_f": 64, "available_at": next_day + "T10:00:00Z", "source_sha256": "synthetic"})
        for suffix in ("A", "B"):
            ticker = day + suffix
            contracts.append({"climate_date": day, "ticker": ticker, "lower_integer_f": None, "upper_integer_f": 65,
                              "open_time": day + "T00:00:00Z", "close_time": next_day + "T08:00:00Z"})
            candles.append({"climate_date": day, "ticker": ticker, "end_period_ts": int(utc(day + "T14:00:00Z").timestamp()),
                            "yes_ask_close": yes_ask, "yes_bid_close": yes_bid, "source_sha256": "synthetic-candle"})
            outcomes.append({"climate_date": day, "ticker": ticker, "yes_outcome": 1,
                             "mapping_consistent": True, "settlement_time": next_day + "T12:00:00Z"})
            reconciliation.append({"climate_date": day, "ticker": ticker, "settlement_label_reconciled": index != 2})
    tables = {("weather_training", "forecasts"): training, ("weather_training", "climate"): climate,
              ("selection", "forecasts"): selections, ("selection", "climate"): labels,
              ("selection", "contracts"): contracts, ("selection", "candles"): candles,
              ("selection", "outcomes"): outcomes, ("selection", "reconciliation"): reconciliation}
    for path in development_paths(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(tables.get((path.parent.parent.name, path.stem), [])), path)
    write_json(root / "configs/evaluation.json", policy)
    for name in module.CODE_FILES:
        destination = root / "src/klax_lab" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
    paths = development_paths(root) + [root / "configs/evaluation.json"] + list((root / "src/klax_lab").glob("*.py"))
    manifest_policy = {"evaluation": policy, "baseline_definitions": BASELINE_DEFINITIONS}
    version = canonical_hash({"files": inventory(root, paths), "policy": manifest_policy})
    manifest_path = root / "data/manifests" / f"development-baselines-{version}.json"
    freeze_manifest(root, paths, manifest_policy, manifest_path)
    baseline = root / "runs" / f"baselines-{version[:16]}"
    write_json(baseline / "summary.json", {"status": "DEVELOPMENT_BASELINES_COMPLETE", "dataset_version": version,
                                          "protected_final_evaluated": False})
    write_json(baseline / "selection_eligibility.json", {
        "eligible_days": ["2025-01-05", "2025-01-06"],
        "excluded": [{"climate_date": "2025-01-07", "reasons": ["NWS_label_not_reconciled_with_settlement", "missing_reconciled_contract"]}]})
    return root, manifest_path


@pytest.fixture
def dataset(tmp_path):
    return build_dataset(tmp_path)


def run(dataset, spec=None):
    return module.run_candidate_experiment(*dataset, spec or SPEC)


def resign(folder):
    """Model an artifact-editing fault even when local hashes were regenerated."""
    write_json(folder / "artifact_manifest.json", {"experiment_id": folder.name, "files": module._artifact_inventory(folder)})


@pytest.mark.parametrize("bias,spread,coefficient", [("global", "global", 0), ("monthly_shrinkage", "monthly_shrinkage", 0),
                                                    ("seasonal_harmonic", "disagreement", .5)])
def test_closed_experiment_and_independent_replica(dataset, bias, spread, coefficient, monkeypatch):
    report = run(dataset, {**SPEC, "bias_mode": bias, "spread_mode": spread, "disagreement_coefficient": coefficient})
    assert report["status"] == "DEVELOPMENT_EXPERIMENT_COMPLETE"
    assert report["training_days"] == 120 and report["eligible_selection_days"] == 2
    assert len(report["predeclared_cost_sensitivity"]) == 12
    assert report["historical_assumed_fill"]["trade_count"] == 2
    assert report["protected_final_evaluated"] is False
    folder = Path(report["path"])
    assert len(json.loads((folder / "daywise_scores.json").read_text())) == 2
    def forbidden(*_a, **_k): raise AssertionError("production calculation reused by verifier")
    for name in ("fit_candidate", "predict_candidate", "replay_model", "probability_scores", "gaussian_crps"):
        monkeypatch.setattr(module, name, forbidden)
    replica = module.verify_candidate_experiment(*dataset, folder)
    assert replica["status"] == "PASS"
    assert replica["primary_candidate_selection_verified"] is True
    assert replica["primary_opportunities_verified"] == 8
    assert replica["primary_selected_days_verified"] == 2
    assert len(replica["scenarios"]) == 13
    assert replica["independent_verification_complete"] is False


def test_v2_threshold_override_is_used_by_independent_replication(tmp_path):
    dataset = build_dataset(tmp_path, yes_ask=".75")
    plan = make_plan(colony="market_execution", stage="economic_simulation",
                     weight=.5, bias="global", spread="global", threshold=.30)
    report = module.run_candidate_experiment(*dataset, plan.to_dict())
    decisions = json.loads((Path(report["path"]) / "primary_decisions.json").read_text())
    assert any(.10 <= float(row["expected_net_return"]) < .30 for row in decisions)
    assert module.verify_candidate_experiment(*dataset, Path(report["path"]))["status"] == "PASS"


def test_v2_side_override_is_used_by_independent_replication(dataset):
    plan = make_plan(colony="market_execution", stage="economic_simulation",
                     weight=.5, bias="global", spread="global", sides="NO")
    report = module.run_candidate_experiment(*dataset, plan.to_dict())
    decisions = json.loads((Path(report["path"]) / "primary_decisions.json").read_text())
    assert decisions and {row["side"] for row in decisions} == {"NO"}
    assert report["historical_assumed_fill"]["skipped_reasons"]["SIDE_POLICY"] == 4
    assert module.verify_candidate_experiment(*dataset, Path(report["path"]))["status"] == "PASS"


def test_reuses_same_frozen_candidate_and_rejects_changed_artifact(dataset):
    first = run(dataset)
    second = run(dataset, {**SPEC, "spread_scale": 1.0, "disagreement_coefficient": 0.0})
    assert first["path"] == second["path"]
    target = Path(first["path"]) / "predictions.json"
    target.write_text("[]")
    with pytest.raises(ValueError, match="inventory|modified"): run(dataset)


def test_rejects_arbitrary_recipe_code(dataset):
    with pytest.raises(ValueError, match="five declared"):
        run(dataset, {**SPEC, "code": "import socket"})


def test_rejects_unfrozen_or_changed_inputs(dataset):
    root, manifest = dataset
    feature = root / "data/normalized/selection/features/forecasts.parquet"
    feature.write_bytes(feature.read_bytes() + b"fault")
    with pytest.raises(ValueError, match="Frozen input changed"): run(dataset)


def test_blocks_holdout_manifest_before_read(dataset, monkeypatch):
    root, path = dataset
    manifest = json.loads(path.read_text())
    manifest["files"].append({"path": "data/normalized/protected_final/labels/outcomes.parquet", "sha256": "x", "bytes": 1})
    manifest["version"] = canonical_hash({"files": manifest["files"], "policy": manifest["policy"]})
    poisoned = path.with_name("development-baselines-" + manifest["version"] + ".json")
    write_json(poisoned, manifest)
    def forbidden(*_a, **_k): raise AssertionError("manifest contents opened before protection check")
    monkeypatch.setattr(module, "verify_manifest", forbidden)
    with pytest.raises(ValueError, match="protected"):
        module.run_candidate_experiment(root, poisoned, SPEC)


def test_rejects_midrun_data_mutation(dataset, monkeypatch):
    original = module.predict_candidate
    root, _ = dataset
    target = root / "data/normalized/weather_training/labels/climate_versions.parquet"
    changed = False
    def mutate(model, row):
        nonlocal changed
        if not changed:
            target.write_bytes(target.read_bytes() + b"fault")
            changed = True
        return original(model, row)
    monkeypatch.setattr(module, "predict_candidate", mutate)
    with pytest.raises(ValueError, match="Frozen input changed"): run(dataset)
    assert not [p for p in (root / "runs/experiments").iterdir() if p.name[0] != "."]


@pytest.mark.parametrize("artifact,field", [("fitted_model.json", "global_bias"), ("daywise_scores.json", "crps_f"),
                                          ("primary_ledger.json", "net_profit")])
def test_replica_detects_regenerated_hashes_over_wrong_math(dataset, artifact, field):
    report = run(dataset)
    folder = Path(report["path"])
    content = json.loads((folder / artifact).read_text())
    target = content if isinstance(content, dict) else content[0]
    target[field] = float(target[field]) + 2
    write_json(folder / artifact, content)
    resign(folder)
    with pytest.raises(ValueError): module.verify_candidate_experiment(*dataset, folder)


def test_replica_detects_omitted_unselected_opportunity(dataset):
    report = run(dataset)
    folder = Path(report["path"])
    decisions = json.loads((folder / "primary_decisions.json").read_text())
    decisions.remove(next(row for row in decisions if not row["selected"]))
    write_json(folder / "primary_decisions.json", decisions)
    resign(folder)
    with pytest.raises(ValueError, match="omitted"):
        module.verify_candidate_experiment(*dataset, folder)


def test_fresh_process_cli_run_and_verify_with_guard(dataset):
    root, manifest = dataset
    spec_path = root / "runs/task/spec.json"
    write_json(spec_path, SPEC)
    env = {**os.environ, "PYTHONPATH": str(Path(module.__file__).parents[1])}
    prefix = [sys.executable, "-m", "klax_lab.experiments"]
    result = subprocess.run(prefix + ["run", "--root", str(root), "--manifest", str(manifest), "--spec-file", str(spec_path),
                                      "--output-root", str(root / "runs/candidates")], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    checks = json.loads((Path(report["path"]) / "offline_checks.json").read_text())
    assert checks["python_socket_denied"] and checks["protected_file_open_denied"] and checks["child_process_denied"]
    output = root / "runs/replica/report.json"
    result = subprocess.run(prefix + ["verify", "--root", str(root), "--manifest", str(manifest), "--run-directory", report["path"],
                                      "--output", str(output)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    replica = json.loads(result.stdout)
    assert replica["primary_candidate_selection_verified"]
    assert json.loads(output.read_text()) == replica
    assert (output.parent / "offline_checks.json").is_file()
