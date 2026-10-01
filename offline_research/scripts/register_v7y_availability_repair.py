"""Register the authorized, outcome-blind V7Y 18Z repair before normalization."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = Path("configs/v7y_hrrr_gefs_availability_policy.json")
SNAPSHOT = Path("runs/v7y_weather_backfill/repair-prestate.json")


def seal(value: dict, field: str) -> dict:
    body = {k: v for k, v in value.items() if k != field}
    value[field] = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def immutable(path: Path, value: dict) -> None:
    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        field = "snapshot_sha256" if path == ROOT / SNAPSHOT else "policy_sha256"
        if old.get(field) != seal(dict(old), field)[field]:
            raise ValueError(f"registration seal differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def main() -> None:
    for run in ("v7y-calendar-2025", "v7y-hrrr-gefs-calendar-2025"):
        folder = ROOT / "runs/replays" / run
        if any((folder / name).exists() for name in ("prediction-freeze.json", "summary.json", "weather-integrity-freeze.json")):
            raise ValueError("availability repair must precede V7Y freeze and scoring")
    recovery_path = ROOT / "runs/v7y_weather_backfill/recovery-state.json"
    state = json.loads(recovery_path.read_text(encoding="utf-8-sig"))
    if state.get("recovery_sha256") != seal(dict(state), "recovery_sha256")["recovery_sha256"]:
        raise ValueError("pre-repair recovery hash differs")
    if state.get("protected_labels_read") is not False or state.get("live_orders_placed") != 0 or state.get("paper_orders_placed") != 0:
        raise ValueError("pre-repair safety boundary differs")
    paths = [
        ROOT / "v5/probability_cache_readiness.py",
        ROOT / "v5/acquire_probability_evidence.py",
        ROOT / "src/klax_lab/weather_sources_v3.py",
        ROOT / "src/klax_lab/weather_decode_v3.py",
        ROOT / "src/klax_lab/weather_normalize_v3.py",
    ]
    legacy = ROOT / "data/normalized/v5p_probability_features"
    for day in state["completed_dates"]:
        paths.extend(sorted((legacy / f"date={day}").glob("*")))
    raw = ROOT / "data/raw/v5p/weather"
    for day in state["failed_dates"]:
        for folder in sorted(raw.glob(f"*-{day.replace('-', '')}-*")):
            paths.extend(sorted(folder.glob("*")))
    records = {
        path.relative_to(ROOT).as_posix(): {"bytes": path.stat().st_size, "sha256": file_hash(path)}
        for path in paths if path.is_file()
    }
    snapshot = seal({
        "schema_version": "v7y-availability-repair-prestate-v1",
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "authorization": "User requested apply the fix on 2026-09-29",
        "prior_recovery_state": state,
        "prior_recovery_file_sha256": file_hash(recovery_path),
        "preserved_artifacts": records,
        "protected_labels_read": False,
        "network_used": False,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
    }, "snapshot_sha256")
    immutable(ROOT / SNAPSHOT, snapshot)
    saved_snapshot = json.loads((ROOT / SNAPSHOT).read_text(encoding="utf-8"))
    policy = seal({
        "schema_version": "v7y-weather-availability-policy-v1",
        "policy_id": "v7y_max_nominal_plus_6h_archived_last_modified_18z_v1",
        "status": "REGISTERED_BEFORE_V7Y_FREEZE",
        "decision_time_utc": "18:00:00",
        "availability_delay_hours": 6,
        "effective_available_at": "max(nominal_model_cycle_plus_6_hours, archived_HTTP_Last-Modified_if_present)",
        "row_admission_rule": "effective_information_available_at_utc_must_be_at_or_before_18:00:00_UTC",
        "historical_publication_time_proven": False,
        "fixed_decision_time_changed": False,
        "protected_labels_read": False,
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "scope": "All V7Y calendar-2025 HRRR/GEFS rows at the existing 18:00 UTC decision",
        "preserved_legacy_normalized_data": True,
        "new_output_root": "data/normalized/v7y_weather_features",
        "eligible_decision_times": "Preserve actual eligible 12:00, 15:00, and 18:00 schedules for each row",
        "repair_prestate_path": SNAPSHOT.as_posix(),
        "repair_prestate_sha256": saved_snapshot["snapshot_sha256"],
        "reason": "Shared V5 all-schedule eligibility incorrectly rejected December files available before V7Y's registered 18:00 decision",
    }, "policy_sha256")
    immutable(ROOT / POLICY, policy)
    saved_policy = json.loads((ROOT / POLICY).read_text(encoding="utf-8"))
    print(json.dumps({"policy_sha256": saved_policy["policy_sha256"], "snapshot_sha256": saved_snapshot["snapshot_sha256"], "preserved_artifact_count": len(saved_snapshot["preserved_artifacts"])}, indent=2))


if __name__ == "__main__":
    main()
