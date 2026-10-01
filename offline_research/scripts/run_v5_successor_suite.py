"""Register and run the finite V5C-V5F development suite."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from v5a.development_search import LABELS, PREDICTION_MANIFEST, UNIVERSE
from v5b.campaign import checked, filehash, gate_failures, offline, seal, write
from v5_successors.evaluation import FORECAST_ARTIFACT, VERSIONS, evaluate_suite, validate_config


CONFIGS = [
    "configs/v5c_consensus.json",
    "configs/v5d_disagreement.json",
    "configs/v5e_heavy_tail.json",
    "configs/v5f_stacked.json",
]
POINTER = "runs/v5_successor_suite_current.json"
RUN_ROOT = "runs/v5_successor_suites"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _binding_paths(root: Path) -> list[str]:
    pointer = _read(root / "runs/v5b_current_campaign.json")
    v5b_run = Path(pointer["run_path"])
    freeze = checked(root / v5b_run / "strategy-freeze.json")
    prediction_manifest = _read(root / PREDICTION_MANIFEST)
    friend_run = Path(FORECAST_ARTIFACT).parent
    paths = set(CONFIGS + [
        "docs/V5C_V5F_SUCCESSOR_PROTOCOL.md",
        "v5_successors/__init__.py",
        "v5_successors/evaluation.py",
        "scripts/run_v5_successor_suite.py",
        "scripts/verify_v5_successor_suite.py",
        "v5b/campaign.py",
        "v5b/evaluation.py",
        "v5a/development_search.py",
        "friend_method/evaluation.py",
        "runs/v5b_current_campaign.json",
        (v5b_run / "registration.json").as_posix(),
        (v5b_run / "strategy-freeze.json").as_posix(),
        (v5b_run / "candidates" / f"{freeze['candidate_id']}.json").as_posix(),
        Path(UNIVERSE).as_posix(),
        Path(PREDICTION_MANIFEST).as_posix(),
        Path(LABELS).as_posix(),
        prediction_manifest["output"]["path"],
        FORECAST_ARTIFACT,
        (friend_run / "registration.json").as_posix(),
        (friend_run / "summary.json").as_posix(),
        (friend_run / "verification.json").as_posix(),
        "data/raw/friend_method_weather_v1/manifest.json",
        "data/manifests/friend_method_weather_v1_verification.json",
        "data/manifests/friend_method_exact_data_readiness.json",
        "data/manifests/v5a_holdout_seal.json",
        "configs/v5b_campaign.json",
    ])
    for name in paths:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"missing or escaping binding: {name}")
    return sorted(paths)


def add_gates(result: dict, gate_config: dict) -> dict:
    result = json.loads(json.dumps(result))
    failures = gate_failures(result, gate_config)
    result["original_v5b_gate_failures"] = failures
    result["original_v5b_all_gates_passed"] = not failures
    return result


def compact(result: dict) -> dict:
    return {
        "selected_days": result["selected_days"],
        "aggregate_realized_net_return": result["aggregate_realized_net_return"],
        "total_entry_outlay_dollars": result["total_entry_outlay_dollars"],
        "total_net_profit_dollars": result["total_net_profit_dollars"],
        "positive_fold_count": result["positive_fold_count"],
        "worst_nonempty_fold_return": result["worst_nonempty_fold_return"],
        "two_cent_adverse_fill_return": result["adverse_stress"]["2"]["aggregate_realized_net_return"],
        "best_day_removed_return": result["best_day_removed_return"],
        "multiclass_brier": result["multiclass_brier"],
        "baseline_multiclass_brier": result["baseline_multiclass_brier"],
        "evidence_quality_score": result["evidence_quality_score"],
        "execution_grade_counts": result["execution_grade_counts"],
        "selection_calibration": result["selection_calibration"],
        "original_v5b_gate_failures": result["original_v5b_gate_failures"],
        "original_v5b_all_gates_passed": result["original_v5b_all_gates_passed"],
    }


def build_summary(run_id: str, registration_sha256: str, baseline: dict, results: dict[str, dict]) -> dict:
    ranking = sorted(
        VERSIONS,
        key=lambda version: (
            bool(results[version]["original_v5b_all_gates_passed"]),
            results[version]["aggregate_realized_net_return"],
            results[version]["positive_fold_count"],
            results[version]["worst_nonempty_fold_return"],
            results[version]["selected_days"],
        ),
        reverse=True,
    )
    return seal({
        "schema_version": "v5-successor-suite-summary-v1",
        "run_id": run_id,
        "registration_sha256": registration_sha256,
        "common_scoring_date_count": 44,
        "warmup_date_count": 20,
        "baseline": compact(baseline),
        "results": {version: compact(results[version]) for version in VERSIONS},
        "development_ranking": ranking,
        "any_original_v5b_gate_pass": any(results[v]["original_v5b_all_gates_passed"] for v in VERSIONS),
        "ten_percent_return_methods": [v for v in ranking if results[v]["aggregate_realized_net_return"] >= 0.10],
        "confirmation_status": "UNAVAILABLE_REPEATEDLY_EXPOSED_DEVELOPMENT_ONLY",
        "protected_confirmation_labels_read": False,
        "holdout_access_authorized": False,
        "actual_orders_placed": False,
    })


def run(root: str | Path) -> Path:
    root = Path(root).resolve()
    pointer_path = root / POINTER
    if pointer_path.exists():
        pointer = checked(pointer_path)
        directory = (root / pointer["run_path"]).resolve()
        checked(directory / "registration.json")
        checked(directory / "summary.json")
        return directory
    configs = [validate_config(_read(root / name)) for name in CONFIGS]
    binding_paths = _binding_paths(root)
    run_id = "v5c-v5f-development-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / RUN_ROOT / run_id
    directory.mkdir(parents=True)
    registration = seal({
        "schema_version": "v5-successor-suite-registration-v1",
        "run_id": run_id,
        "registered_at": stamp(),
        "versions": list(VERSIONS),
        "config_paths": CONFIGS,
        "bindings": {name: filehash(root / name) for name in binding_paths},
        "common_scoring_date_count": 44,
        "warmup_date_count": 20,
        "allow_network": False,
        "allow_orders": False,
        "allow_protected_labels": False,
        "protected_confirmation_labels_read": False,
        "holdout_access_authorized": False,
        "actual_orders_placed": False,
    })
    write(directory / "registration.json", registration)
    state = seal({"run_id": run_id, "status": "REGISTERED", "registration_sha256": registration["self_sha256"],
                  "created_at": stamp(), "protected_confirmation_labels_read": False,
                  "network_used": False, "actual_orders_placed": False})
    write(directory / "state.json", state)
    gate_config = _read(root / "configs/v5b_campaign.json")
    with offline():
        suite = evaluate_suite(root, configs)
    baseline = add_gates(suite["baseline"], gate_config)
    baseline["schema_version"] = "v5-successor-common-baseline-v1"
    baseline["protected_confirmation_labels_read"] = False
    baseline["actual_orders_placed"] = False
    write(directory / "baseline-common-44.json", seal(baseline))
    results = {}
    for version in VERSIONS:
        result = add_gates(suite["results"][version], gate_config)
        results[version] = result
        write(directory / f"result-{version.lower()}.json", seal(result))
    summary = build_summary(run_id, registration["self_sha256"], baseline, results)
    write(directory / "summary.json", summary)
    state = seal({"run_id": run_id, "status": "COMPLETE", "registration_sha256": registration["self_sha256"],
                  "summary_sha256": summary["self_sha256"], "completed_at": stamp(),
                  "protected_confirmation_labels_read": False, "network_used": False,
                  "actual_orders_placed": False})
    write(directory / "state.json", state)
    write(pointer_path, seal({"run_id": run_id, "run_path": directory.relative_to(root).as_posix()}))
    return directory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    directory = run(args.project_root)
    print(directory)
    print((directory / "summary.json").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

