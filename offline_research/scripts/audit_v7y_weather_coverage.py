"""Read-only integrity and coverage audit for the V7Y HRRR/GEFS backfill."""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.weather_sources_v3 import verify_cache_only
from scripts.audit_v7y_weather_history import policy_registration_binding, repair_prestate_binding
from scripts.v7y_weather_cache import (
    OUTPUT_ROOT,
    POLICY_ID,
    validate_normalized_day,
    verify_policy_registration,
)
from v5.acquire_probability_evidence import build_v5_daily_weather_plans


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_state_hash(state: dict) -> str:
    body = {key: value for key, value in state.items() if key != "recovery_sha256"}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(root: Path) -> dict:
    state_path = root / "runs/v7y_weather_backfill/recovery-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    registration = verify_policy_registration(root)
    repair_prestate = repair_prestate_binding(root, registration)
    expected_registration = {
        "path": registration["path"],
        "sha256": registration["sha256"],
        "policy_sha256": registration["policy_sha256"],
    }
    state_hash_valid = state.get("recovery_sha256") == _canonical_state_hash(state)
    start, end = date.fromisoformat(state["date_start"]), date.fromisoformat(state["date_end"])
    expected = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    completed = list(state["completed_dates"])
    unavailable = list(state["unavailable_dates"])
    normalized_completed = list(state.get("normalization_completed_dates", []))
    problems: list[str] = []
    checked = []
    if not state_hash_valid:
        problems.append("recovery-state hash mismatch")
    if (
        state.get("availability_registration") != expected_registration
        or state.get("availability_policy_id") != POLICY_ID
        or state.get("normalized_output_root") != OUTPUT_ROOT.as_posix()
        or state.get("availability_repair_parent_recovery_sha256")
        != repair_prestate["prior_recovery_sha256"]
    ):
        problems.append("recovery-state availability policy binding differs")
    if len(expected) != state["target_date_count"]:
        problems.append("target date count mismatch")
    if completed != sorted(set(completed)):
        problems.append("completed dates are duplicated or unsorted")
    if unavailable != sorted(set(unavailable)):
        problems.append("unavailable dates are duplicated or unsorted")
    if normalized_completed != completed:
        problems.append("normalized completion coverage differs from completed dates")
    if set(completed) & set(unavailable):
        problems.append("completed and unavailable dates overlap")
    if not (set(completed) | set(unavailable)).issubset(set(expected)):
        problems.append("state contains out-of-range dates")

    for day_text in completed:
        target = date.fromisoformat(day_text)
        folder = root / OUTPUT_ROOT / f"date={day_text}"
        manifest_path = folder / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            validate_normalized_day(root, target, manifest)
            coverage = manifest.get("coverage", {})
            if not coverage.get("hrrr", {}).get("complete") or not coverage.get("gefs", {}).get("complete"):
                raise ValueError("normalized coverage incomplete")
            for output in manifest.get("outputs", []):
                path = root / output["path"]
                if path.stat().st_size != output["bytes"] or _sha(path) != output["sha256"]:
                    raise ValueError(f"output hash or size mismatch: {path}")
            hrrr, gefs = build_v5_daily_weather_plans(target)
            h_raw = verify_cache_only(hrrr, root / "data/raw/v5p/weather")
            g_raw = verify_cache_only(gefs, root / "data/raw/v5p/weather")
            checked.append({
                "date": day_text,
                "manifest_sha256": manifest["manifest_sha256"],
                "hrrr_raw_objects": h_raw["objects_verified"],
                "gefs_raw_objects": g_raw["objects_verified"],
                "hrrr_rows": coverage["hrrr"]["rows"],
                "gefs_rows": coverage["gefs"]["rows"],
            })
        except Exception as exc:
            problems.append(f"{day_text}: {type(exc).__name__}: {exc}")

    done = set(completed) | set(unavailable)
    return {
        "schema": "v7y-weather-coverage-audit-v2",
        "campaign_id": state.get("campaign_id"),
        "state_status": state.get("status"),
        "process_id": state.get("process_id"),
        "state_hash_valid": state_hash_valid,
        "availability_policy_id": POLICY_ID,
        "availability_policy_registration": policy_registration_binding(registration),
        "availability_repair_parent_recovery_sha256": state.get(
            "availability_repair_parent_recovery_sha256"
        ),
        "availability_repair_prestate": repair_prestate,
        "normalization_completed_dates": len(normalized_completed),
        "target_dates": len(expected),
        "completed_dates": len(completed),
        "unavailable_dates": len(unavailable),
        "pending_dates": len(expected) - len(done),
        "first_pending_date": next((item for item in expected if item not in done), None),
        "last_completed_date": max(completed, default=None),
        "verified_completed_dates": len(checked),
        "integrity_pass": not problems and len(checked) == len(completed),
        "problems": problems,
        "checked": checked,
    }


def main() -> None:
    root = PROJECT
    result = audit(root)
    output = root / "runs/v7y_weather_backfill/coverage-audit.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "checked"}, indent=2))
    if not result["integrity_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
