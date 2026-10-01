"""Register and start the corrected V6.2 offline first wave."""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path
import socket

from v6_1.common import checked, filehash, now, read, seal, stamp, write
from .evaluation import run as evaluate


CONFIG = Path("configs/v6_2_campaign.json")
POINTER = Path("runs/v6_2_current_campaign.json")
RUN_ROOT = Path("runs/campaigns_v6_2")


def locate(root: Path) -> Path:
    pointer = read(root / POINTER)
    directory = (root / pointer["run_path"]).resolve()
    if not directory.is_relative_to((root / RUN_ROOT).resolve()):
        raise ValueError("V6.2 run path escaped campaign root")
    return directory


def register(root: Path) -> dict:
    root = Path(root).resolve()
    if (root / POINTER).is_file():
        return checked(locate(root) / "recovery-state.json")
    config = read(root / CONFIG)
    if any((config["allow_network"], config["allow_confirmation_labels"], config["allow_orders"])):
        raise ValueError("Unsafe V6.2 configuration")
    if len(config["catalog_ids"]) != 27 or len(set(config["catalog_ids"])) != 27:
        raise ValueError("V6.2 catalog differs")
    v61_pointer = read(root / "runs/v6_1_current_campaign.json")
    v61_stop = Path(v61_pointer["run_path"]) / "preexperiment-stop.json"
    checked(root / v61_stop)
    files = [CONFIG.as_posix(), "docs/V6_2_CORRECTED_FORECAST_FIRST_CAMPAIGN.md",
             "data/development/v6_1/market_baseline_v2.json",
             "data/manifests/v6_1_observations_2026.json",
             "data/manifests/v6_1_global_exposure_ledger.json",
             "data/manifests/v5a_outcome_blind_universe.json",
             "data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json",
             "data/normalized/v5a/frozen_leader_probabilities.parquet",
             "data/development/v5a/development_labels.json",
             "data/development/v5b_next/weather_features.json",
             "data/development/v5b_next/weather_features_manifest.json",
             "v6_2/evaluation.py", "v6_2/campaign.py", v61_stop.as_posix()]
    identifier = "v6-2-development-" + now().strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / RUN_ROOT / identifier
    directory.mkdir(parents=True)
    deadline = (now() + timedelta(seconds=config["development_wall_seconds"])).isoformat()
    registration = seal({
        "schema_version": "klax-v6.2-registration-v1", "campaign_id": identifier,
        "registered_at": stamp(), "deadline": deadline, "catalog_ids": config["catalog_ids"],
        "bindings": {path: filehash(root / path) for path in sorted(files)},
        "predecessor_v6_1_stop_sha256": checked(root / v61_stop)["self_sha256"],
        "confirmation_authorized": False, "protected_labels_accessed": False,
        "network_used_by_experiment": False, "orders": 0,
    })
    state = seal({"schema_version": "klax-v6.2-state-v1", "campaign_id": identifier,
                  "status": "REGISTERED", "phase": "FIRST_WAVE",
                  "deadline": deadline, "evaluated_candidate_count": 0,
                  "created_at": stamp(), "updated_at": stamp(),
                  "protected_labels_accessed": False, "network_used_by_experiment": False,
                  "orders": 0})
    write(directory / "registration.json", registration)
    write(directory / "recovery-state.json", state)
    write(root / POINTER, {"campaign_id": identifier,
                           "run_path": directory.relative_to(root).as_posix()})
    return state


def verify(root: Path) -> tuple[dict, dict]:
    directory = locate(root)
    registration, state = checked(directory / "registration.json"), checked(directory / "recovery-state.json")
    for relative, expected in registration["bindings"].items():
        if filehash(root / relative) != expected:
            raise ValueError(f"V6.2 frozen binding differs: {relative}")
    if registration["campaign_id"] != state["campaign_id"] or registration["deadline"] != state["deadline"]:
        raise ValueError("V6.2 identity or deadline differs")
    return registration, state


def start(root: Path) -> dict:
    root = Path(root).resolve()
    register(root)
    registration, state = verify(root)
    if state["status"] != "REGISTERED":
        return state
    config = read(root / CONFIG)
    original_socket, original_connection = socket.socket, socket.create_connection
    def denied(*args, **kwargs):
        raise PermissionError("V6.2 experiment forbids network access")
    socket.socket = denied; socket.create_connection = denied
    try:
        result = evaluate(root, config)
    finally:
        socket.socket = original_socket; socket.create_connection = original_connection
    result = seal({**result, "campaign_id": state["campaign_id"], "completed_at": stamp()})
    directory = locate(root)
    write(directory / "first-wave.json", result)
    state = seal({**{key: value for key, value in state.items() if key != "self_sha256"},
                  "status": "ACTIVE_AFTER_FIRST_WAVE", "phase": "OBSERVATION_AND_RESIDUAL_WAVE",
                  "evaluated_candidate_count": len(result["evaluated_candidate_ids"]),
                  "updated_at": stamp()})
    write(directory / "recovery-state.json", state)
    return state


def status(root: Path) -> dict:
    registration, state = verify(Path(root).resolve())
    result = checked(locate(Path(root).resolve()) / "first-wave.json") if state["evaluated_candidate_count"] else None
    return {"campaign_id": state["campaign_id"], "status": state["status"], "phase": state["phase"],
            "deadline": state["deadline"], "evaluated_candidate_count": state["evaluated_candidate_count"],
            "market_gate_passed": None if result is None else result["market_results"]["M01_MARKET_PLUS_CALIBRATED_WEATHER"]["market_gate_passed"],
            "confirmation_authorized": registration["confirmation_authorized"],
            "protected_labels_accessed": state["protected_labels_accessed"], "orders": state["orders"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "start", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    print({"register": register, "start": start, "status": status}[args.action](args.project_root))


if __name__ == "__main__":
    main()

