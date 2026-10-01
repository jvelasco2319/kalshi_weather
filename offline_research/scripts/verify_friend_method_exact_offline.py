"""Independently rerun and verify the sealed exact friend-method test."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from friend_method.exact_evaluation import evaluate
from v5b.campaign import checked, filehash, offline, seal, write


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    pointer = checked(root / "runs/friend_method_exact_current.json")
    run_dir = (root / pointer["run_path"]).resolve()
    if not run_dir.is_relative_to((root / "runs/friend_method_exact_tests").resolve()):
        raise RuntimeError("exact run path escaped")
    registration = checked(run_dir / "registration.json")
    summary = checked(run_dir / "summary.json")
    if pointer["summary_sha256"] != summary["self_sha256"]:
        raise RuntimeError("summary pointer differs")
    for name, expected in registration["bindings"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or filehash(path) != expected:
            raise RuntimeError(f"frozen binding differs: {name}")
    config = read(root / "configs/friend_method_exact_test.json")
    with offline():
        evaluation = evaluate(root, config)
    for window_id, forecasts in evaluation["forecasts_by_window"].items():
        observed = checked(run_dir / f"forecasts-{window_id}.json")
        expected = seal({
            "schema": "friend-method-exact-forecasts-v1",
            "window_id": window_id,
            "forecasts": forecasts,
            "protected_confirmation_labels_read": False,
        })
        if observed != expected:
            raise RuntimeError(f"forecast reproduction differs: {window_id}")
    for result_id, result in evaluation["results"].items():
        observed = checked(run_dir / f"result-{result_id.replace('/', '--')}.json")
        if observed != seal(result):
            raise RuntimeError(f"result reproduction differs: {result_id}")
    for variant in config["strategy"]["variants"]:
        kalshi = evaluation["results"][f"kalshi_fixed_pst_f008_f031/{variant}"]
        fixture = evaluation["results"][f"supplied_fixture_f007_f030/{variant}"]
        comparable_kalshi = {key: value for key, value in kalshi.items() if key not in {"window_id", "window_role"}}
        comparable_fixture = {key: value for key, value in fixture.items() if key not in {"window_id", "window_role"}}
        if comparable_kalshi != comparable_fixture:
            raise RuntimeError(f"identical NBM daily highs produced different results: {variant}")
    if summary["primary_result_id"] != evaluation["primary_result_id"]:
        raise RuntimeError("primary result identity differs")
    verification = seal({
        "schema": "friend-method-exact-verification-v1",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": registration["run_id"],
        "registration_sha256": registration["self_sha256"],
        "summary_sha256": summary["self_sha256"],
        "binding_count": len(registration["bindings"]),
        "reproduced_windows": sorted(evaluation["forecasts_by_window"]),
        "reproduced_results": sorted(evaluation["results"]),
        "exact_reproduction_passed": True,
        "nbm_window_numerical_equivalence_passed": True,
        "network_denial_active_during_reproduction": True,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    })
    write(run_dir / "verification.json", verification)
    print(json.dumps(verification, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
