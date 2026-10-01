"""Close V6.1 before its experiment clock after objective preregistration failure."""
from __future__ import annotations

import argparse
from pathlib import Path

from .campaign import locate, verify
from .common import checked, filehash, seal, stamp, write


def close(root: Path) -> dict:
    root = Path(root).resolve()
    registration, state = verify(root)
    if state["development_budget_started"] or state["evaluated_candidate_count"]:
        raise ValueError("V6.1 already consumed experimental evidence")
    directory = locate(root)
    audit = checked(directory / "gate-impossibility-audit.json")
    terminal = seal({
        "schema_version": "klax-v6.1-preexperiment-stop-v1",
        "campaign_id": state["campaign_id"], "closed_at": stamp(),
        "status": "CLOSED_PREEXPERIMENT_PREREGISTRATION_DEFECT",
        "scientific_candidate_evaluations": 0,
        "development_seconds_spent": 0,
        "reason": audit["status"],
        "corrected_successor_required": True,
        "protected_labels_accessed": False, "network_used_by_experiment": False,
        "orders": 0,
        "bindings": {
            "gate-impossibility-audit.json": filehash(directory / "gate-impossibility-audit.json"),
            "v6_1/close_preexperiment.py": filehash(root / "v6_1/close_preexperiment.py"),
        },
    })
    write(directory / "preexperiment-stop.json", terminal)
    state = seal({**{key: value for key, value in state.items() if key != "self_sha256"},
                  "status": terminal["status"], "updated_at": stamp()})
    write(directory / "recovery-state.json", state)
    return terminal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = close(args.project_root)
    print({key: result[key] for key in ("campaign_id", "status", "scientific_candidate_evaluations",
                                       "development_seconds_spent", "corrected_successor_required")})


if __name__ == "__main__":
    main()

