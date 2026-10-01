"""Independent artifact, replay, settlement, and arithmetic checks for V5C-V5F."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v5a.development_search import _contains, _fee
from v5b.campaign import checked, filehash, offline, seal, write
from v5_successors.evaluation import VERSIONS, evaluate_suite, validate_config
from scripts.run_v5_successor_suite import CONFIGS, POINTER, add_gates, build_summary


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _without_hash(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "self_sha256"}


def _ledger_checks(result: dict, labels: dict[str, float]) -> int:
    checks = 0
    for trade in result["trades"]:
        date = trade["climate_date"]
        price = int(trade["entry_price_cents"])
        fee = float(_fee(date, price))
        if abs(fee - trade["fee_dollars"]) > 1e-12:
            raise ValueError("fee reproduction failed")
        if trade["contract_side"] != "NO":
            raise ValueError("successor selected a non-NO side")
        bracket_won = _contains(trade, labels[date])
        won = bracket_won is False
        if won != trade["won"]:
            raise ValueError("settlement reproduction failed")
        outlay = price / 100.0 + fee
        profit = int(won) - outlay
        if abs(outlay - trade["entry_outlay_dollars"]) > 1e-12 or abs(profit - trade["net_profit_dollars"]) > 1e-12:
            raise ValueError("trade arithmetic reproduction failed")
        checks += 3
    outlay = sum(row["entry_outlay_dollars"] for row in result["trades"])
    profit = sum(row["net_profit_dollars"] for row in result["trades"])
    expected_return = profit / outlay if outlay else -1.0
    if abs(outlay - result["total_entry_outlay_dollars"]) > 1e-12:
        raise ValueError("aggregate outlay differs")
    if abs(profit - result["total_net_profit_dollars"]) > 1e-12:
        raise ValueError("aggregate profit differs")
    if abs(expected_return - result["aggregate_realized_net_return"]) > 1e-12:
        raise ValueError("aggregate return differs")
    return checks + 3


def verify(root: str | Path) -> dict:
    root = Path(root).resolve()
    pointer = checked(root / POINTER)
    directory = (root / pointer["run_path"]).resolve()
    if not directory.is_relative_to((root / "runs/v5_successor_suites").resolve()):
        raise ValueError("successor run path escaped")
    registration = checked(directory / "registration.json")
    state = checked(directory / "state.json")
    summary = checked(directory / "summary.json")
    if state["status"] != "COMPLETE" or state["summary_sha256"] != summary["self_sha256"]:
        raise ValueError("terminal state differs")
    for name, expected in registration["bindings"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or filehash(path) != expected:
            raise ValueError(f"frozen binding differs: {name}")
        lower = name.lower()
        if ("holdout" in lower or "protected" in lower) and name != "data/manifests/v5a_holdout_seal.json":
            raise ValueError("protected input was bound")
    configs = [validate_config(_read(root / name)) for name in CONFIGS]
    with offline():
        reproduced = evaluate_suite(root, configs)
    gate_config = _read(root / "configs/v5b_campaign.json")
    stored_baseline = checked(directory / "baseline-common-44.json")
    expected_baseline = add_gates(reproduced["baseline"], gate_config)
    expected_baseline["schema_version"] = "v5-successor-common-baseline-v1"
    expected_baseline["protected_confirmation_labels_read"] = False
    expected_baseline["actual_orders_placed"] = False
    if _without_hash(stored_baseline) != expected_baseline:
        raise ValueError("baseline exact replay differs")
    results = {}
    ledger_checks = _ledger_checks(stored_baseline, reproduced_context_labels(root))
    for version in VERSIONS:
        stored = checked(directory / f"result-{version.lower()}.json")
        expected = add_gates(reproduced["results"][version], gate_config)
        if _without_hash(stored) != expected:
            raise ValueError(f"{version} exact replay differs")
        results[version] = expected
        ledger_checks += _ledger_checks(stored, reproduced_context_labels(root))
    expected_summary = build_summary(registration["run_id"], registration["self_sha256"], expected_baseline, results)
    if summary != expected_summary:
        raise ValueError("summary reproduction differs")
    return seal({
        "schema_version": "v5-successor-suite-verification-v1",
        "run_id": registration["run_id"],
        "registration_sha256": registration["self_sha256"],
        "summary_sha256": summary["self_sha256"],
        "binding_count": len(registration["bindings"]),
        "versions_reproduced": list(VERSIONS),
        "ledger_arithmetic_checks": ledger_checks,
        "exact_replay_passed": True,
        "network_denial_active_during_replay": True,
        "protected_confirmation_labels_read": False,
        "holdout_access_authorized": False,
        "actual_orders_placed": False,
        "status": "PASS",
    })


def reproduced_context_labels(root: Path) -> dict[str, float]:
    # Development labels are an explicitly bound input. No protected path is read.
    payload = _read(root / "data/development/v5a/development_labels.json")
    return {row["climate_date"]: float(row["reported_high_f"]) for row in payload["labels"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    result = verify(root)
    pointer = checked(root / POINTER)
    directory = root / pointer["run_path"]
    write(directory / "verification.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

