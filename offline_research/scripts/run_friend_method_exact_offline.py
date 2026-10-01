"""Register, run, and seal the exact friend-method development test."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from friend_method.exact_evaluation import evaluate
from scripts.run_friend_method_offline import _benchmark
from v5b.campaign import filehash, offline, seal, write


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bindings(root: Path) -> dict[str, str]:
    prediction_manifest_path = root / "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json"
    prediction_manifest = read(prediction_manifest_path)
    paths = [
        "configs/friend_method_exact_test.json",
        "docs/FRIEND_METHOD_EXACT_TEST_PROTOCOL.md",
        "docs/reference/TRADING_BOT_SPEC.md",
        "friend_method/exact_evaluation.py",
        "friend_method/evaluation.py",
        "scripts/run_friend_method_exact_offline.py",
        "v5a/development_search.py",
        "v5b/evaluation.py",
        "data/raw/friend_method_weather_v1/manifest.json",
        "data/manifests/friend_method_weather_v1_verification.json",
        "data/manifests/friend_method_exact_data_readiness.json",
        "data/manifests/v5a_outcome_blind_universe.json",
        "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json",
        "data/development/v5a/development_labels.json",
        prediction_manifest["output"]["path"],
    ]
    return {path: filehash(root / path) for path in paths}


def proxy_results(root: Path) -> list[dict]:
    pointer = read(root / "runs/friend_method_current.json")
    summary = read(root / pointer["run_path"] / "summary.json")
    rows = []
    for variant, result in summary["results"].items():
        rows.append({
            "name": f"Friend proxy / {variant}",
            "status": "development_screen_passed" if result["all_gates_passed"] else "failed",
            "selected_dates": result["selected_dates"],
            "selected_trades": result["selected_trades"],
            "return": result["aggregate_realized_net_return"],
            "positive_folds": result["positive_fold_count"],
            "worst_fold": result["worst_nonempty_fold_return"],
            "gate_failures": result["gate_failures"],
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    config = read(root / "configs/friend_method_exact_test.json")
    readiness = read(root / "data/manifests/friend_method_exact_data_readiness.json")
    if not readiness["ready_for_exact_development_test"] or readiness["protected_confirmation_labels_read"]:
        raise RuntimeError("exact data readiness does not authorize this development test")

    run_id = "friend-method-exact-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = root / "runs/friend_method_exact_tests" / run_id
    run_dir.mkdir(parents=True)
    registration = seal({
        "schema": "friend-method-exact-registration-v1",
        "run_id": run_id,
        "registered_at_utc": datetime.now(timezone.utc).isoformat(),
        "bindings": bindings(root),
        "candidate_ids": [
            f"{window['id']}/{variant}"
            for window in config["weather"]["windows"]
            for variant in config["strategy"]["variants"]
        ],
        "primary_candidate_id": config["strategy"]["primary_variant"],
        "network_allowed": False,
        "protected_confirmation_access": False,
        "orders_authorized": 0,
    })
    write(run_dir / "registration.json", registration)

    with offline():
        evaluation = evaluate(root, config)
    for window_id, forecasts in evaluation["forecasts_by_window"].items():
        write(run_dir / f"forecasts-{window_id}.json", seal({
            "schema": "friend-method-exact-forecasts-v1",
            "window_id": window_id,
            "forecasts": forecasts,
            "protected_confirmation_labels_read": False,
        }))
    for result_id, result in evaluation["results"].items():
        safe_name = result_id.replace("/", "--")
        write(run_dir / f"result-{safe_name}.json", seal(result))

    comparison = _benchmark(root) + proxy_results(root)
    for result_id, result in evaluation["results"].items():
        comparison.append({
            "name": f"Friend exact / {result_id}",
            "status": "development_screen_passed" if result["all_gates_passed"] else "failed",
            "selected_dates": result["selected_dates"],
            "selected_trades": result["selected_trades"],
            "return": result["aggregate_realized_net_return"],
            "positive_folds": result["positive_fold_count"],
            "worst_fold": result["worst_nonempty_fold_return"],
            "gate_failures": result["gate_failures"],
        })
    comparison.sort(key=lambda row: (-row["return"], -row["selected_dates"], row["name"]))
    compact_results = {
        result_id: {key: value for key, value in result.items() if key not in {"trades", "abstentions"}}
        for result_id, result in evaluation["results"].items()
    }
    primary = evaluation["results"][evaluation["primary_result_id"]]
    summary = seal({
        "schema": "friend-method-exact-summary-v1",
        "run_id": run_id,
        "registration_sha256": registration["self_sha256"],
        "exact_four_model_test_completed": True,
        "effective_source_count": evaluation["effective_source_count"],
        "forecast_count_per_window": evaluation["forecast_count_per_window"],
        "primary_result_id": evaluation["primary_result_id"],
        "primary_all_gates_passed": primary["all_gates_passed"],
        "primary_gate_failures": primary["gate_failures"],
        "results": compact_results,
        "comparison_ranking": comparison,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    })
    write(run_dir / "summary.json", summary)
    pointer = seal({
        "run_id": run_id,
        "run_path": str(run_dir.relative_to(root)).replace("\\", "/"),
        "summary_sha256": summary["self_sha256"],
    })
    write(root / "runs/friend_method_exact_current.json", pointer)
    print(json.dumps({
        "run_path": pointer["run_path"],
        "primary_result_id": summary["primary_result_id"],
        "primary_all_gates_passed": summary["primary_all_gates_passed"],
        "results": {
            name: {
                "selected_dates": result["selected_dates"],
                "selected_trades": result["selected_trades"],
                "return": result["aggregate_realized_net_return"],
                "gate_failures": result["gate_failures"],
            }
            for name, result in evaluation["results"].items()
        },
    }, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
