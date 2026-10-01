"""Register and launch the V6.1 prerequisite phase; no confirmation entry point exists."""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

from .common import checked, filehash, now, read, seal, stamp, write


CONFIG = Path("configs/v6_1_campaign.json")
READINESS = Path("data/manifests/v6_1_readiness.json")
LEDGER = Path("data/manifests/v6_1_global_exposure_ledger.json")
POINTER = Path("runs/v6_1_current_campaign.json")
RUN_ROOT = Path("runs/campaigns_v6_1")


def _validate_config(config: dict) -> None:
    if config["launch_mode"] != "DEVELOPMENT_ONLY":
        raise ValueError("V6.1 must launch development-only")
    if any((config["allow_network_during_experiment"], config["allow_orders"],
            config["allow_paper_orders"], config["allow_confirmation_labels"])):
        raise ValueError("Unsafe V6.1 configuration")
    identifiers = [item["id"] for item in config["candidate_catalog"]]
    if len(identifiers) != 27 or len(set(identifiers)) != 27:
        raise ValueError("V6.1 catalog must contain 27 unique registered candidates")
    if set(item["colony"] for item in config["candidate_catalog"]) != {
        "settlement_forecasting", "market_residuals", "execution_economics",
        "adversarial_validation"
    }:
        raise ValueError("V6.1 must contain exactly four colonies")
    if config["minimum_confirmation_grade_a_days"] != 100:
        raise ValueError("Confirmation sample gate differs from registration")


def locate(root: Path) -> Path:
    pointer = read(root / POINTER)
    directory = (root / pointer["run_path"]).resolve()
    if not directory.is_relative_to((root / RUN_ROOT).resolve()):
        raise ValueError("V6.1 campaign path escaped run root")
    return directory


def _bindings(root: Path, config: dict) -> dict:
    files = list(config["binding_files"]) + [
        READINESS.as_posix(), LEDGER.as_posix(),
        "v6_1/common.py", "v6_1/date_ledger.py", "v6_1/readiness.py",
        "v6_1/market_projection.py", "v6_1/campaign.py",
        "scripts/control_v6_1_campaign.ps1", "scripts/verify_v6_1_artifacts.py",
    ]
    old_v6 = root / "runs/campaigns_v6/v6-development-20260927T214208879719Z/recovery-state.json"
    if old_v6.is_file():
        files.append(old_v6.relative_to(root).as_posix())
    return {name: filehash(root / name) for name in sorted(set(files))}


def register(root: Path) -> dict:
    root = Path(root).resolve()
    if (root / POINTER).is_file():
        return verify(root)[1]
    config = read(root / CONFIG)
    _validate_config(config)
    readiness = checked(root / READINESS)
    ledger = checked(root / LEDGER)
    if readiness["status"] != "READY_FOR_DEVELOPMENT_PREREQUISITES_ONLY":
        raise ValueError("V6.1 readiness is not launchable")
    if ledger["confirmation_eligible_date_count"] != 0:
        raise ValueError("Exposure policy unexpectedly differs")
    identifier = "v6-1-development-" + now().strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / RUN_ROOT / identifier
    directory.mkdir(parents=True)
    registration = seal({
        "schema_version": "klax-v6.1-registration-v1",
        "campaign_id": identifier,
        "registered_at": stamp(),
        "launch_mode": "DEVELOPMENT_ONLY",
        "phase": "PREREQUISITE_AND_MARKET_BASELINE_BUILD",
        "development_budget_seconds": config["development_wall_seconds"],
        "development_deadline": None,
        "candidate_catalog_ids": [item["id"] for item in config["candidate_catalog"]],
        "colonies": sorted({item["colony"] for item in config["candidate_catalog"]}),
        "readiness_sha256": readiness["self_sha256"],
        "exposure_ledger_sha256": ledger["self_sha256"],
        "bindings": _bindings(root, config),
        "protected_labels_accessed": False,
        "network_used_by_experiment": False,
        "orders": 0,
        "confirmation_authorized": False,
        "predecessor_v6_preserved": True,
    })
    state = seal({
        "schema_version": "klax-v6.1-recovery-state-v1",
        "campaign_id": identifier,
        "status": "REGISTERED",
        "phase": "PREREQUISITE_AND_MARKET_BASELINE_BUILD",
        "registered_candidate_count": len(config["candidate_catalog"]),
        "evaluated_candidate_count": 0,
        "development_budget_started": False,
        "development_seconds_spent": 0,
        "protected_labels_accessed": False,
        "network_used_by_experiment": False,
        "orders": 0,
        "created_at": stamp(),
        "updated_at": stamp(),
    })
    write(directory / "registration.json", registration)
    write(directory / "recovery-state.json", state)
    write(root / POINTER, {
        "campaign_id": identifier,
        "run_path": directory.relative_to(root).as_posix(),
    })
    return state


def verify(root: Path) -> tuple[dict, dict]:
    root = Path(root).resolve()
    directory = locate(root)
    registration = checked(directory / "registration.json")
    state = checked(directory / "recovery-state.json")
    if registration["campaign_id"] != state["campaign_id"]:
        raise ValueError("V6.1 campaign identity differs")
    for relative, expected in registration["bindings"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or filehash(path) != expected:
            raise ValueError(f"Frozen V6.1 binding differs: {relative}")
    if any((state["protected_labels_accessed"], state["network_used_by_experiment"], state["orders"])):
        raise ValueError("V6.1 safety invariant failed")
    return registration, state


def start(root: Path) -> dict:
    root = Path(root).resolve()
    state = register(root)
    registration, state = verify(root)
    if state["status"] == "ACTIVE_PREREQUISITE_PHASE":
        return state
    if state["status"] != "REGISTERED":
        raise ValueError("V6.1 cannot start from current state")
    readiness = checked(root / READINESS)
    colony_status = seal({
        "schema_version": "klax-v6.1-colony-status-v1",
        "campaign_id": state["campaign_id"],
        "updated_at": stamp(),
        "colonies": readiness["colonies"],
        "cross_pollination_enabled": False,
        "cross_pollination_condition": "Only after settlement forecast and market baseline precursor gates pass",
        "confirmation_authorized": False,
    })
    directory = locate(root)
    write(directory / "colony-status.json", colony_status)
    launch = seal({
        "schema_version": "klax-v6.1-launch-certificate-v1",
        "campaign_id": state["campaign_id"],
        "launched_at": stamp(),
        "phase": state["phase"],
        "development_only": True,
        "candidate_catalog_registered": len(registration["candidate_catalog_ids"]),
        "colonies_started": registration["colonies"],
        "readiness_status": readiness["status"],
        "blocking_gates": readiness["blocking_gates"],
        "development_budget_started": False,
        "reason_budget_not_started": "The experimental clock begins only after 2026 observations and the coherent market baseline pass readiness.",
        "confirmation_authorized": False,
        "protected_labels_accessed": False,
        "orders": 0,
    })
    write(directory / "launch-certificate.json", launch)
    state = seal({**{key: value for key, value in state.items() if key != "self_sha256"},
        "status": "ACTIVE_PREREQUISITE_PHASE", "updated_at": stamp()})
    write(directory / "recovery-state.json", state)
    return state


def status(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    readiness = checked(root / READINESS)
    return {
        "campaign_id": state["campaign_id"],
        "status": state["status"],
        "phase": state["phase"],
        "colonies": registration["colonies"],
        "registered_candidate_count": state["registered_candidate_count"],
        "evaluated_candidate_count": state["evaluated_candidate_count"],
        "development_budget_started": state["development_budget_started"],
        "blocking_gates": readiness["blocking_gates"],
        "confirmation_authorized": registration["confirmation_authorized"],
        "protected_labels_accessed": state["protected_labels_accessed"],
        "orders": state["orders"],
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "start", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    action = {"register": register, "start": start, "status": status}[args.action]
    print(action(args.project_root))


if __name__ == "__main__":
    main()

