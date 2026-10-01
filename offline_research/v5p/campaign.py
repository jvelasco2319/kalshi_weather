"""Registration and status for the V5P partial-evidence campaign."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from v5.acquire_probability_evidence import DEFAULT_RUN_ID, status as acquisition_status


VERSION = "klax-v5p-campaign-state-v1"
CONFIG = Path("configs/v5p_partial_evidence_campaign.json")
CURRENT = Path("runs/v5p_current_campaign.json")


class V5PCampaignError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V5PCampaignError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V5PCampaignError(f"JSON object required: {path}")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def _campaign_id() -> str:
    return "v5p-partial-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def register(root: Path, campaign_id: str | None = None) -> dict[str, Any]:
    root = Path(root).resolve()
    config_path = root / CONFIG
    config = _load(config_path)
    if config.get("schema_version") != "klax-v5p-partial-evidence-campaign-v1":
        raise V5PCampaignError("unexpected V5P config schema")
    goal_path = root / str(config.get("goal_document"))
    if _sha(goal_path) != config.get("goal_document_sha256"):
        raise V5PCampaignError("V5P goal binding mismatch")
    if config.get("live_feeds") is not False or config.get("paper_orders") is not False or config.get("live_orders") is not False:
        raise V5PCampaignError("V5P order/live boundary changed")
    current_path = root / CURRENT
    if current_path.exists():
        current = _load(current_path)
        state_path = root / current["state_path"]
        if state_path.exists():
            return _load(state_path)
    identifier = campaign_id or _campaign_id()
    run_dir = root / "runs/campaigns_v5p" / identifier
    state_path = run_dir / "recovery-state.json"
    state = {
        "version": VERSION,
        "campaign_id": identifier,
        "status": "REGISTERED_ACQUISITION_PENDING",
        "phase": "historical_acquisition",
        "registered_at_utc": _now(),
        "config_path": CONFIG.as_posix(),
        "config_sha256": _sha(config_path),
        "goal_path": str(config["goal_document"]),
        "goal_sha256": config["goal_document_sha256"],
        "acquisition_run_id": DEFAULT_RUN_ID,
        "acquisition_state_path": f"runs/v5p_acquisition/{DEFAULT_RUN_ID}/recovery-state.json",
        "analysis_wall_clock_budget_seconds": int(config["iteration"]["wall_clock_seconds"]),
        "analysis_budget_started_at_utc": None,
        "analysis_absolute_deadline_utc": None,
        "actual_coverage_must_be_reported": True,
        "original_v5_90_percent_gate_applies": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "live_feed_started": False,
    }
    _write(state_path, state)
    _write(current_path, {
        "version": VERSION,
        "campaign_id": identifier,
        "state_path": state_path.relative_to(root).as_posix(),
    })
    return state


def status(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    current_path = root / CURRENT
    if not current_path.exists():
        return {"version": VERSION, "status": "NOT_REGISTERED"}
    current = _load(current_path)
    state_path = root / current["state_path"]
    state = _load(state_path)
    acquisition = acquisition_status(root, state["acquisition_run_id"])
    acquisition_phase = acquisition.get("status")
    mapped = {
        "NOT_STARTED": "REGISTERED_ACQUISITION_PENDING",
        "REGISTERED_NOT_STARTED": "REGISTERED_ACQUISITION_PENDING",
        "RUNNING": "ACQUIRING_HISTORICAL_DATA",
        "FAILED": "ACQUISITION_FAILED",
        "COMPLETE": "DATA_ACQUIRED_NORMALIZATION_REQUIRED",
    }.get(acquisition_phase, "ACQUISITION_STATE_UNKNOWN")
    result = dict(state)
    result["status"] = mapped
    result["acquisition"] = {
        "status": acquisition_phase,
        "process_id": acquisition.get("process_id"),
        "charged_request_starts": acquisition.get("charged_request_starts", 0),
        "kalshi": acquisition.get("kalshi"),
        "clilax": acquisition.get("clilax"),
        "weather": acquisition.get("weather"),
        "failure_type": acquisition.get("failure_type"),
        "failure_message": acquisition.get("failure_message"),
        "recovery_sha256": acquisition.get("recovery_sha256"),
    }
    result["protected_confirmation_labels_read"] = False
    result["actual_orders_placed"] = False
    return result


def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "status"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    result = register(args.project_root) if args.command == "register" else status(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
