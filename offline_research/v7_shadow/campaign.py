"""Register and verify the bounded V7 six-week shadow campaign.

This module does not acquire data, read outcomes, make predictions, or place
orders.  It freezes the campaign identity and maintains a hash-bound status
record that later finite daily acquisition jobs must satisfy.
"""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


CAMPAIGN_ID = "v7-shadow-20260929-20261109"
CONFIG = Path("configs/v7_six_week_shadow.json")
GOAL = Path("docs/V7_SIX_WEEK_SHADOW_CAMPAIGN_GOAL.md")
RUN_ROOT = Path("runs/campaigns_v7") / CAMPAIGN_ID
REGISTRATION = RUN_ROOT / "registration.json"
STATE = RUN_ROOT / "recovery-state.json"
IMPLEMENTATION_FREEZE = RUN_ROOT / "implementation-freeze.json"
IMPLEMENTATION_FILES = (
    Path("v7_shadow/campaign.py"),
    Path("v7_shadow/daily.py"),
    Path("v7_shadow/score.py"),
    Path("scripts/acquire_v7_weather_day.py"),
    Path("scripts/acquire_v7_kalshi_universe_day.py"),
    Path("scripts/acquire_v7_probalytics_day.py"),
    Path("scripts/acquire_v7_settlements.py"),
    Path("scripts/control_v7_shadow_campaign.ps1"),
)


class V7CampaignError(ValueError):
    """A frozen V7 identity or safety invariant failed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sealed(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    result = dict(value)
    result[field] = _hash(result)
    return result


def _verify_seal(value: Mapping[str, Any], field: str) -> None:
    body = {key: item for key, item in value.items() if key != field}
    if value.get(field) != _hash(body):
        raise V7CampaignError(f"{field} mismatch")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise V7CampaignError(f"JSON object required: {path}")
    return value


def _write_atomic(path: Path, value: Mapping[str, Any], *, immutable: bool) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if immutable and path.exists():
        if path.read_text(encoding="utf-8") != payload:
            raise V7CampaignError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(payload, encoding="utf-8", newline="\n")
    pending.replace(path)


def target_dates(config: Mapping[str, Any]) -> list[str]:
    start = date.fromisoformat(str(config["target_start"]))
    end = date.fromisoformat(str(config["target_end"]))
    if end < start:
        raise V7CampaignError("target window is reversed")
    values = [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]
    if len(values) != 42 or len(values) != int(config["target_date_count"]):
        raise V7CampaignError("V7 must contain exactly 42 target dates")
    return values


def validate_config(config: Mapping[str, Any]) -> list[str]:
    dates = target_dates(config)
    methods = list(config.get("methods", []))
    ids = [str(item.get("id")) for item in methods]
    expected = [
        "v5b_causal_no",
        "v6_gefs_spread_equal_blend_no",
        "diagnostic_nam_only",
        "friend_exact_primary_gfs_nam_nbm",
        "v5f_cross_family_stack",
        "diagnostic_gfs_only",
        "diagnostic_nbm_only",
    ]
    if ids != expected or len(ids) != len(set(ids)):
        raise V7CampaignError("frozen method catalog differs")
    if config.get("decision_time_utc") != "18:00:00":
        raise V7CampaignError("decision time differs")
    if int(config.get("arrival_delay_seconds", -1)) != 5:
        raise V7CampaignError("arrival delay differs")
    execution = config.get("execution", {})
    if (
        int(execution.get("quantity_contracts", -1)) != 1
        or execution.get("primary_evidence_grade") != "A"
        or execution.get("require_verified_state") is not True
        or execution.get("require_contiguous_sequence") is not True
    ):
        raise V7CampaignError("execution contract differs")
    if any(config.get(field) is not False for field in (
        "continuous_live_feed_allowed", "outcomes_before_freeze_allowed",
        "paper_orders_allowed", "live_orders_allowed",
    )):
        raise V7CampaignError("a prohibited capability is enabled")
    if int(config.get("order_authorization_count", -1)) != 0:
        raise V7CampaignError("order authorization must remain zero")
    evaluation = config.get("evaluation", {})
    if (
        evaluation.get("six_week_result_is_preliminary") is not True
        or int(evaluation.get("minimum_grade_a_days_for_final_confirmation", 0)) != 100
    ):
        raise V7CampaignError("final-confirmation boundary differs")
    return dates


def _bindings(root: Path, config: Mapping[str, Any]) -> dict[str, str]:
    relatives = [CONFIG, GOAL]
    relatives.extend(Path(item) for item in config["bound_source_files"])
    result: dict[str, str] = {}
    for relative in relatives:
        path = root / relative
        if not path.is_file():
            raise V7CampaignError(f"missing bound input: {relative.as_posix()}")
        result[relative.as_posix()] = _file_hash(path)
    return result


def _implementation_bindings(root: Path) -> dict[str, str]:
    result = {}
    for relative in IMPLEMENTATION_FILES:
        path = root / relative
        if not path.is_file():
            raise V7CampaignError(f"missing V7 implementation file: {relative.as_posix()}")
        result[relative.as_posix()] = _file_hash(path)
    return result


def register(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config = _read_object(root / CONFIG)
    dates = validate_config(config)
    registration = _sealed({
        "schema_version": "klax-v7-shadow-registration-v1",
        "campaign_id": CAMPAIGN_ID,
        "status": "REGISTERED_BEFORE_FIRST_TARGET",
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "target_dates": dates,
        "config_sha256": _file_hash(root / CONFIG),
        "goal_sha256": _file_hash(root / GOAL),
        "bindings": _bindings(root, config),
        "outcomes_read": False,
        "continuous_live_feed_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "order_authorization_count": 0,
    }, "registration_sha256")
    registration_path = root / REGISTRATION
    if registration_path.exists():
        existing = _read_object(registration_path)
        _verify_seal(existing, "registration_sha256")
        fixed_fields = (
            "campaign_id", "target_dates", "config_sha256", "goal_sha256",
            "bindings", "outcomes_read", "continuous_live_feed_used",
            "paper_orders_placed", "live_orders_placed", "order_authorization_count",
        )
        if any(existing.get(field) != registration.get(field) for field in fixed_fields):
            raise V7CampaignError("existing V7 registration differs")
        registration = existing
    else:
        _write_atomic(registration_path, registration, immutable=True)

    implementation_path = root / IMPLEMENTATION_FREEZE
    implementation = _sealed({
        "schema_version": "klax-v7-implementation-freeze-v1",
        "campaign_id": CAMPAIGN_ID,
        "registration_sha256": registration["registration_sha256"],
        "status": "FROZEN_BEFORE_FIRST_TARGET",
        "bindings": _implementation_bindings(root),
        "outcome_reader_separate_from_daily_predictor": True,
        "paper_orders_allowed": False,
        "live_orders_allowed": False,
        "order_authorization_count": 0,
    }, "implementation_sha256")
    _write_atomic(implementation_path, implementation, immutable=True)

    state_path = root / STATE
    if state_path.exists():
        state = _read_object(state_path)
        _verify_seal(state, "recovery_sha256")
        if state.get("registration_sha256") != registration["registration_sha256"]:
            raise V7CampaignError("recovery state references another registration")
    else:
        state = _sealed({
            "schema_version": "klax-v7-shadow-recovery-v1",
            "campaign_id": CAMPAIGN_ID,
            "registration_sha256": registration["registration_sha256"],
            "status": "WAITING_FOR_FIRST_TARGET_DATE",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "completed_acquisition_dates": [],
            "completed_prediction_freeze_dates": [],
            "failed_dates": [],
            "outcomes_read": False,
            "terminal_score_runs": 0,
            "continuous_live_feed_used": False,
            "paper_orders_placed": 0,
            "live_orders_placed": 0,
            "order_authorization_count": 0,
        }, "recovery_sha256")
        _write_atomic(state_path, state, immutable=False)
    return {"registration": registration, "state": state}


def status(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    registration_path, state_path = root / REGISTRATION, root / STATE
    if not registration_path.is_file() or not state_path.is_file():
        return {"campaign_id": CAMPAIGN_ID, "status": "NOT_REGISTERED"}
    registration = _read_object(registration_path)
    state = _read_object(state_path)
    implementation = _read_object(root / IMPLEMENTATION_FREEZE)
    _verify_seal(registration, "registration_sha256")
    _verify_seal(state, "recovery_sha256")
    _verify_seal(implementation, "implementation_sha256")
    config = _read_object(root / CONFIG)
    validate_config(config)
    current_bindings = _bindings(root, config)
    if registration.get("bindings") != current_bindings:
        raise V7CampaignError("a frozen V7 binding changed")
    if (
        implementation.get("registration_sha256") != registration["registration_sha256"]
        or implementation.get("bindings") != _implementation_bindings(root)
    ):
        raise V7CampaignError("a frozen V7 implementation binding changed")
    if state.get("registration_sha256") != registration["registration_sha256"]:
        raise V7CampaignError("registration/state identity mismatch")
    if any(int(state.get(field, -1)) != 0 for field in (
        "paper_orders_placed", "live_orders_placed", "order_authorization_count",
    )):
        raise V7CampaignError("zero-order invariant failed")
    completed = set(state.get("completed_prediction_freeze_dates", []))
    target = set(registration["target_dates"])
    if not completed <= target:
        raise V7CampaignError("state contains an unregistered target date")
    return {
        "campaign_id": CAMPAIGN_ID,
        "status": state["status"],
        "target_start": registration["target_dates"][0],
        "target_end": registration["target_dates"][-1],
        "target_date_count": len(registration["target_dates"]),
        "completed_prediction_freeze_dates": len(completed),
        "remaining_prediction_freeze_dates": len(target - completed),
        "outcomes_read": state["outcomes_read"],
        "terminal_score_runs": state["terminal_score_runs"],
        "order_authorization_count": state["order_authorization_count"],
        "registration_sha256": registration["registration_sha256"],
        "implementation_sha256": implementation["implementation_sha256"],
        "recovery_sha256": state["recovery_sha256"],
        "bindings_verified": True,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "status", "verify"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    result = register(args.project_root) if args.action == "register" else status(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

