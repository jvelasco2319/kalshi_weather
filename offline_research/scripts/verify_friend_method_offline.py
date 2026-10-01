"""Independently replay and verify the current sealed friend-method test."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from friend_method.evaluation import evaluate
from friend_method.fixture_validation import validate_fixture
from v5b.campaign import checked, filehash, offline, read, seal, write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()

    pointer = checked(root / "runs/friend_method_current.json")
    run_dir = root / pointer["run_path"]
    summary = checked(run_dir / "summary.json")
    registration = checked(run_dir / "registration.json")
    fixture_saved = checked(run_dir / "fixture-validation.json")

    if pointer["summary_sha256"] != summary["self_sha256"]:
        raise RuntimeError("Current pointer does not bind the sealed summary")
    if summary["registration_sha256"] != registration["self_sha256"]:
        raise RuntimeError("Summary does not bind the sealed registration")
    if summary["fixture_validation_sha256"] != fixture_saved["self_sha256"]:
        raise RuntimeError("Summary does not bind the sealed fixture validation")

    mismatches = {
        path: {"expected": expected, "actual": filehash(root / path)}
        for path, expected in registration["input_bindings"].items()
        if not (root / path).is_file() or filehash(root / path) != expected
    }
    if mismatches:
        raise RuntimeError(f"Frozen input mismatch: {mismatches}")

    config = read(root / "configs/friend_method_offline_test.json")
    fixture_replay = seal(validate_fixture(root / config["legacy_fixture"]["path"]))
    if fixture_replay != fixture_saved:
        raise RuntimeError("Fixture-validation replay differs from the sealed artifact")

    with offline():
        replay = evaluate(root, config)
    replay_hashes = {}
    for name, result in replay["results"].items():
        saved = checked(run_dir / f"result-{name}.json")
        repeated = seal(result)
        if repeated != saved:
            raise RuntimeError(f"Deterministic replay mismatch for {name}")
        replay_hashes[name] = saved["self_sha256"]
        if saved["protected_confirmation_labels_read"] or saved["actual_orders_placed"]:
            raise RuntimeError(f"Safety invariant failed for {name}")

    if summary["protected_confirmation_labels_read"] or summary["actual_orders_placed"]:
        raise RuntimeError("Summary safety invariant failed")
    verification = seal({
        "schema": "friend-method-offline-verification-v1",
        "run_id": summary["run_id"],
        "summary_sha256": summary["self_sha256"],
        "registration_sha256": registration["self_sha256"],
        "fixture_validation_sha256": fixture_saved["self_sha256"],
        "result_sha256": replay_hashes,
        "input_binding_count": len(registration["input_bindings"]),
        "input_bindings_verified": True,
        "deterministic_replay_verified": True,
        "network_used": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "verification_passed": True,
    })
    write(run_dir / "verification.json", verification)
    print(json.dumps(verification, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
