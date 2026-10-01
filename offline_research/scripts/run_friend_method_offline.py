"""Run and seal the development-only friend-method comparison."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from friend_method.evaluation import evaluate
from friend_method.fixture_validation import validate_fixture
from v5b.campaign import filehash, seal, write


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _benchmark(root: Path) -> list[dict]:
    v5a = _read(root / "runs/campaigns_v5a/v5a-offline-20260927T171522716076Z/recovery-state.json")["incumbent"]
    v5b = _read(root / "runs/campaigns_v5b/v5b-development-20260927T183845017734Z/development-summary.json")["leader"]["result"]
    v5bn = _read(root / "runs/campaigns_v5b_next/v5b-next-development-20260927T190525115982Z/development-summary.json")["leader"]["result"]
    v6_summary = _read(root / "runs/campaigns_v6/v6-development-20260927T214208879719Z/development-summary.json")
    v6_id = v6_summary["diagnostic_leader_ids"][0]
    v6 = _read(root / "runs/campaigns_v6/v6-development-20260927T214208879719Z/candidates" / f"{v6_id}.json")["result"]
    reference = _read(root / "runs/campaigns_v6/v6-development-20260927T214208879719Z/reference-controls.json")["control"]["common_44_date_result"]
    return [
        {"name": "Frozen V5B NO strategy", "status": "development_screen_passed_unconfirmed", "selected_dates": v5b["selected_days"], "selected_trades": v5b["selected_trades"], "return": v5b["aggregate_realized_net_return"], "positive_folds": v5b["positive_fold_count"], "worst_fold": v5b["worst_nonempty_fold_return"]},
        {"name": "Frozen V5B replay on common 44 dates", "status": "reference_control_unconfirmed", "selected_dates": reference["selected_days"], "selected_trades": reference["selected_trades"], "return": reference["aggregate_realized_net_return"], "positive_folds": reference["positive_fold_count"], "worst_fold": min(row["aggregate_realized_net_return"] for row in reference["temporal_folds"] if row["selected_days"])},
        {"name": "V5A YES strategy", "status": "fragile_positive_rejected", "selected_dates": v5a["selected_days"], "selected_trades": v5a["selected_trades"], "return": v5a["aggregate_realized_net_return"], "positive_folds": v5a["positive_fold_count"], "worst_fold": v5a["worst_nonempty_fold_return"]},
        {"name": "V5B-next equal-blend NO", "status": "failed", "selected_dates": v5bn["selected_days"], "selected_trades": v5bn["selected_trades"], "return": v5bn["aggregate_realized_net_return"], "positive_folds": v5bn["positive_fold_count"], "worst_fold": v5bn["worst_nonempty_fold_return"]},
        {"name": "V6 GEFS-spread NO", "status": "failed_diagnostic_leader", "selected_dates": v6["selected_days"], "selected_trades": v6["selected_trades"], "return": v6["aggregate_realized_net_return"], "positive_folds": v6["positive_fold_count"], "worst_fold": v6["worst_nonempty_fold_return"]},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    config_path = root / "configs/friend_method_offline_test.json"
    protocol_path = root / "docs/FRIEND_METHOD_OFFLINE_TEST_PROTOCOL.md"
    config = _read(config_path)
    fixture_path = root / config["legacy_fixture"]["path"]
    source_spec_path = root / config["source_spec_path"]
    if filehash(source_spec_path) != config["source_spec_sha256"]:
        raise RuntimeError("Source specification hash does not match the frozen config")
    if filehash(fixture_path) != config["legacy_fixture"]["sha256"]:
        raise RuntimeError("Legacy fixture hash does not match the frozen config")
    run_id = "friend-method-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = root / "runs/friend_method_tests" / run_id
    run_dir.mkdir(parents=True)
    registration = seal({
        "schema": "friend-method-offline-registration-v1",
        "run_id": run_id,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "config_sha256": filehash(config_path),
        "protocol_sha256": filehash(protocol_path),
        "source_spec_sha256": config["source_spec_sha256"],
        "input_bindings": load_bindings(root),
        "network_allowed": False,
        "protected_confirmation_access": False,
        "orders_authorized": 0,
    })
    write(run_dir / "registration.json", registration)
    fixture_validation = seal(validate_fixture(fixture_path))
    write(run_dir / "fixture-validation.json", fixture_validation)
    evaluation = evaluate(root, config)
    for name, result in evaluation["results"].items():
        write(run_dir / f"result-{name}.json", seal(result))
    comparisons = _benchmark(root)
    for name, result in evaluation["results"].items():
        comparisons.append({
            "name": f"Friend method proxy / {name}",
            "status": "development_screen_passed" if result["all_gates_passed"] else "failed",
            "selected_dates": result["selected_dates"],
            "selected_trades": result["selected_trades"],
            "return": result["aggregate_realized_net_return"],
            "positive_folds": result["positive_fold_count"],
            "worst_fold": result["worst_nonempty_fold_return"],
            "gate_failures": result["gate_failures"],
        })
    comparisons.sort(key=lambda row: (-row["return"], -row["selected_dates"], row["name"]))
    summary = seal({
        "schema": "friend-method-offline-summary-v1",
        "run_id": run_id,
        "registration_sha256": registration["self_sha256"],
        "fixture_validation_sha256": fixture_validation["self_sha256"],
        "fixture_conformance_passed": fixture_validation["conformance_passed"],
        "fixture_effective_source_groups": fixture_validation["effective_source_groups"],
        "exact_four_model_test_completed": False,
        "data_adapted_proxy_completed": True,
        "forecast_count": evaluation["forecast_count"],
        "results": {name: {key: value for key, value in result.items() if key not in {"trades", "abstentions"}} for name, result in evaluation["results"].items()},
        "comparison_ranking": comparisons,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    })
    write(run_dir / "summary.json", summary)
    pointer = seal({"run_id": run_id, "run_path": str(run_dir.relative_to(root)).replace("\\", "/"), "summary_sha256": summary["self_sha256"]})
    write(root / "runs/friend_method_current.json", pointer)
    print(json.dumps({"run_path": pointer["run_path"], "summary": summary}, indent=2, allow_nan=False))


def load_bindings(root: Path) -> dict:
    paths = [
        "configs/friend_method_offline_test.json",
        "docs/FRIEND_METHOD_OFFLINE_TEST_PROTOCOL.md",
        "friend_method/evaluation.py",
        "friend_method/fixture_validation.py",
        "scripts/run_friend_method_offline.py",
        "docs/reference/TRADING_BOT_SPEC.md",
        "data/development/v5b_next/weather_features.json",
        "data/development/v5b_next/weather_features_manifest.json",
        "data/development/v5a/development_labels.json",
        "data/manifests/v5a_outcome_blind_universe.json",
        "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json",
        "data/reference/friend_method/Los_Angeles_CA_2026-07-03.json",
    ]
    return {path: filehash(root / path) for path in paths}


if __name__ == "__main__":
    main()
