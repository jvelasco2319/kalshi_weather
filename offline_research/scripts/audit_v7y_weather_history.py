"""Incrementally audit completed V7Y HRRR/GEFS weather dates without network access."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.weather_sources_v3 import verify_cache_only
from scripts.acquire_v7y_weather_history import (
    CACHE,
    STATE,
    artifact_hash,
    canonical_hash,
    file_hash,
    load_state,
)
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT,
    POLICY_ID,
    validate_normalized_day,
    verify_policy_registration,
)
from v5.acquire_probability_evidence import build_v5_daily_weather_plans


AUDIT = Path("runs/v7y_weather_backfill/incremental-audit.json")
SCHEMA = "v7y-weather-backfill-incremental-audit-v2"
POLICY_BINDING_KEYS = (
    "path",
    "bytes",
    "sha256",
    "policy_sha256",
    "repair_prestate_path",
    "repair_prestate_bytes",
    "repair_prestate_file_sha256",
    "repair_prestate_sha256",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value["audit_sha256"] = artifact_hash(value, "audit_sha256")
    return value


def load_prior(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("schema_version") != SCHEMA:
        return None
    if value.get("audit_sha256") != artifact_hash(value, "audit_sha256"):
        raise ValueError("V7Y incremental audit seal or schema differs")
    if value.get("network_used") is not False or value.get("protected_labels_read") is not False:
        raise ValueError("V7Y incremental audit safety boundary differs")
    return value


def manifest_path(root: Path, day: str) -> Path:
    return root / OUTPUT_ROOT / f"date={day}" / "manifest.json"


def policy_registration_binding(registration: dict[str, Any]) -> dict[str, Any]:
    return {key: registration[key] for key in POLICY_BINDING_KEYS}


def repair_prestate_binding(root: Path, registration: dict[str, Any]) -> dict[str, Any]:
    relative = registration["policy"].get("repair_prestate_path")
    if not isinstance(relative, str):
        raise ValueError("V7Y repair prestate path is missing")
    path = root / relative
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    prior = value.get("prior_recovery_state")
    if (
        value.get("snapshot_sha256") != artifact_hash(value, "snapshot_sha256")
        or registration["policy"].get("repair_prestate_sha256") != value.get("snapshot_sha256")
        or not isinstance(prior, dict)
        or prior.get("recovery_sha256") != canonical_hash(prior)
    ):
        raise ValueError("V7Y repair prestate binding differs")
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": file_hash(path),
        "snapshot_sha256": value["snapshot_sha256"],
        "prior_recovery_sha256": prior["recovery_sha256"],
    }


def audit_date(root: Path, day: str) -> dict[str, Any]:
    target = date.fromisoformat(day)
    path = manifest_path(root, day)
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_normalized_day(root, target, manifest)
    hrrr_plan, gefs_plan = build_v5_daily_weather_plans(target)
    cache = root / CACHE
    hrrr = verify_cache_only(hrrr_plan, cache)
    gefs = verify_cache_only(gefs_plan, cache)
    if (
        hrrr.get("status") != "CACHE_COMPATIBILITY_PASS"
        or hrrr.get("objects_verified") != 4
        or hrrr.get("network_used") is not False
        or hrrr.get("protected_final_read") is not False
        or gefs.get("status") != "CACHE_COMPATIBILITY_PASS"
        or gefs.get("objects_verified") != 16
        or gefs.get("network_used") is not False
        or gefs.get("protected_final_read") is not False
    ):
        raise ValueError(f"raw cache verification differs: {day}")
    outputs = {record["model"]: record for record in manifest["outputs"]}
    return {
        "climate_date": day,
        "status": "PASS",
        "raw": {
            "hrrr_objects": 4,
            "hrrr_selected_fields": 24,
            "hrrr_cached_bytes": hrrr["cached_bytes_verified"],
            "gefs_objects": 16,
            "gefs_selected_fields": 16,
            "gefs_cached_bytes": gefs["cached_bytes_verified"],
            "network_used": False,
            "protected_labels_read": False,
        },
        "normalized": {
            "manifest_sha256": manifest["manifest_sha256"],
            "manifest_file_sha256": file_hash(path),
            "availability_policy_id": manifest["availability_policy_id"],
            "availability_policy_registration": manifest["availability_policy_registration"],
            "hrrr_rows": outputs["hrrr"]["rows"],
            "hrrr_sha256": outputs["hrrr"]["sha256"],
            "gefs_rows": outputs["gefs"]["rows"],
            "gefs_sha256": outputs["gefs"]["sha256"],
            "decision_time_utc": manifest["decision_time_utc"],
            "protected_confirmation_labels_read": manifest["protected_confirmation_labels_read"],
            "actual_orders_placed": manifest["actual_orders_placed"],
        },
    }


def reusable(root: Path, record: dict[str, Any]) -> bool:
    day = record.get("climate_date")
    path = manifest_path(root, str(day))
    if record.get("status") != "PASS" or not path.is_file():
        return False
    normalized = record.get("normalized", {})
    if normalized.get("manifest_file_sha256") != file_hash(path):
        return False
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    try:
        validate_normalized_day(root, date.fromisoformat(str(day)), manifest)
    except Exception:
        return False
    return all(
        normalized.get(f"{model}_sha256") == output.get("sha256")
        for model, output in {row["model"]: row for row in manifest["outputs"]}.items()
    )


def run(root: Path, full: bool = False) -> dict[str, Any]:
    state_path = root / STATE
    state = load_state(state_path)
    registration = verify_policy_registration(root)
    repair_prestate = repair_prestate_binding(root, registration)
    state_registration = {
        "path": registration["path"],
        "sha256": registration["sha256"],
        "policy_sha256": registration["policy_sha256"],
    }
    if (
        state.get("availability_registration") != state_registration
        or state.get("availability_policy_id") != POLICY_ID
        or state.get("normalized_output_root") != OUTPUT_ROOT.as_posix()
        or state.get("availability_repair_parent_recovery_sha256")
        != repair_prestate["prior_recovery_sha256"]
    ):
        raise ValueError("V7Y recovery policy registration differs")
    snapshot_sha256 = state["recovery_sha256"]
    completed = list(state["completed_dates"])
    normalized_completed = list(state.get("normalization_completed_dates", []))
    if normalized_completed != completed:
        raise ValueError("V7Y normalized completion coverage differs from completed dates")
    prior = load_prior(root / AUDIT)
    prior_by_day = {
        row["climate_date"]: row
        for row in (prior or {}).get("dates", [])
    }
    rows: list[dict[str, Any]] = []
    reused = 0
    newly_audited = 0
    for day in completed:
        old = prior_by_day.get(day)
        if not full and old is not None and reusable(root, old):
            rows.append(old)
            reused += 1
        else:
            rows.append(audit_date(root, day))
            newly_audited += 1
    if len(rows) != len(completed) or [row["climate_date"] for row in rows] != completed:
        raise ValueError("audit coverage differs from recovery snapshot")
    document = seal({
        "schema_version": SCHEMA,
        "status": "PASS",
        "campaign_id": state["campaign_id"],
        "snapshot_at_utc": now(),
        "recovery_path": STATE.as_posix(),
        "recovery_sha256": snapshot_sha256,
        "recovery_status": state["status"],
        "availability_policy_id": POLICY_ID,
        "availability_policy_registration": policy_registration_binding(registration),
        "availability_repair_parent_recovery_sha256": state.get(
            "availability_repair_parent_recovery_sha256"
        ),
        "availability_repair_prestate": repair_prestate,
        "normalized_output_root": OUTPUT_ROOT.as_posix(),
        "target_date_count": state["target_date_count"],
        "completed_date_count": len(completed),
        "normalization_completed_date_count": len(normalized_completed),
        "unavailable_date_count": len(state["unavailable_dates"]),
        "failed_date_count": len(state["failed_dates"]),
        "newly_audited_date_count": newly_audited,
        "reused_verified_date_count": reused,
        "raw_hrrr_object_count": len(rows) * 4,
        "raw_gefs_object_count": len(rows) * 16,
        "raw_selected_field_count": len(rows) * 40,
        "normalized_hrrr_row_count": len(rows) * 24,
        "normalized_gefs_row_count": len(rows) * 16,
        "dates": rows,
        "network_used": False,
        "protected_labels_read": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "limitations": [
            "This snapshot covers only dates marked complete when the recovery state was read.",
            "Previously audited raw objects are reused incrementally when their normalized bindings remain unchanged; use --full to rehash all raw objects.",
            "Archive retrieval timestamps do not prove original model publication time.",
        ],
    })
    output = root / AUDIT
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_suffix(output.suffix + ".pending")
    pending.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(output)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    result = run(Path(args.project_root).resolve(), full=args.full)
    print(json.dumps({
        "status": result["status"],
        "completed_date_count": result["completed_date_count"],
        "newly_audited_date_count": result["newly_audited_date_count"],
        "reused_verified_date_count": result["reused_verified_date_count"],
        "raw_object_count": result["raw_hrrr_object_count"] + result["raw_gefs_object_count"],
        "normalized_row_count": result["normalized_hrrr_row_count"] + result["normalized_gefs_row_count"],
        "audit_sha256": result["audit_sha256"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
