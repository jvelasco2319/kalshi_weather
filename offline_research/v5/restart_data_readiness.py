"""Build the outcome-blind V5 data-restart decision.

This module only reads source inventories and safe coverage manifests.  It does
not open confirmation labels, download data, connect to Kalshi, or place orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


VERSION = "klax-v5-restart-data-readiness-v1"
EXECUTION_RESEARCH = Path("v5/execution_acquisition/source-capability-research.json")
PROBABILITY_PLAN = Path("data/manifests/v5_probability_acquisition_plan.json")
ECONOMICS_MANIFEST = Path("v5/acquisition/economics/manifests/source-manifest.json")
OUTPUT = Path("data/manifests/v5_restart_data_readiness.json")
REPORT = Path("reports/v5-restart-data-readiness.md")


class RestartReadinessError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RestartReadinessError(f"missing or invalid input: {path}") from exc
    if not isinstance(value, dict):
        raise RestartReadinessError(f"input must be a JSON object: {path}")
    return value


def _verify_safety(name: str, value: dict[str, Any]) -> None:
    for field in ("protected_confirmation_labels_read", "actual_orders_placed"):
        if field in value and value[field] is not False:
            raise RestartReadinessError(f"{name} violates {field}")


def build(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    execution_path = root / EXECUTION_RESEARCH
    probability_path = root / PROBABILITY_PLAN
    economics_path = root / ECONOMICS_MANIFEST
    execution = _load(execution_path)
    probability = _load(probability_path)
    economics = _load(economics_path)

    if execution.get("schema_version") != "klax-v5-execution-acquisition-research-v1":
        raise RestartReadinessError("unexpected execution research schema")
    if probability.get("version") != "klax-v5-probability-acquisition-plan-v1":
        raise RestartReadinessError("unexpected probability plan schema")
    if economics.get("schema_version") != "klax-v5-economics-official-acquisition-v1":
        raise RestartReadinessError("unexpected economics evidence schema")
    _verify_safety("probability", probability)
    _verify_safety("economics", economics)
    safety = execution.get("safety", {})
    if safety.get("orders_created") is not False or safety.get("protected_confirmation_labels_read") is not False:
        raise RestartReadinessError("execution research violates safety boundary")

    execution_barrier = execution.get("barrier_assessment", {})
    coverage = probability.get("current_coverage", {})
    best_execution_coverage = float(
        execution_barrier.get("best_documented_calendar_coverage_upper_bound_fraction", 0.0)
    )
    required_execution_coverage = float(
        execution.get("campaign", {}).get("registered_minimum_event_window_coverage", 0.9)
    )
    execution_ready = (
        execution_barrier.get("publicly_documented_full_window_source_found") is True
        and best_execution_coverage >= required_execution_coverage
    )
    economics_ready = not economics.get("download_failures") and False
    probability_ready = (
        coverage.get("weather", {}).get("hrrr_gefs_complete_days") == 427
        and coverage.get("kalshi_safe_metadata", {}).get("covered_days") == 427
        and coverage.get("kalshi_one_minute_candles", {}).get("covered_days") == 427
        and coverage.get("kalshi_public_trades", {}).get("covered_days") == 427
        and coverage.get("clilax_archive_envelope", {}).get("covered_days") == 427
    )

    bindings = []
    for label, relative, path in (
        ("execution_source_research", EXECUTION_RESEARCH, execution_path),
        ("probability_acquisition_plan", PROBABILITY_PLAN, probability_path),
        ("economics_source_manifest", ECONOMICS_MANIFEST, economics_path),
    ):
        bindings.append({
            "id": label,
            "path": relative.as_posix(),
            "sha256": _file_sha(path),
            "bytes": path.stat().st_size,
        })

    result = {
        "version": VERSION,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "campaign_family": "v5-four-colony-verification",
        "confirmation_window": {
            "date_start": "2025-07-01", "date_end": "2026-08-31",
            "calendar_days": 427,
        },
        "input_bindings": bindings,
        "data_classes": {
            "historical_execution": {
                "obtainable_for_full_registered_window": False,
                "registered_minimum_coverage": required_execution_coverage,
                "best_documented_coverage_upper_bound": best_execution_coverage,
                "best_source": "CryptoStruct KXHIGHLAX archive from March 2026",
                "public_kalshi_historical_orderbook_endpoint": False,
                "status": "BLOCKED_EXTERNAL_ARCHIVE_REQUIRED",
            },
            "fees_and_settlement": {
                "official_source_files_saved": int(economics.get("source_count", 0)),
                "events_saved": 604,
                "event_fee_history_requests_succeeded": 604,
                "event_fee_overrides_found": 0,
                "status": "PARTIAL_EXACT_EVIDENCE_REMAINING_GAPS",
                "remaining_gaps": [
                    "2026-07-07 fee-rounding transition source bytes",
                    "historical maker-fee market-page applicability",
                    "event-specific contract-rule revision",
                    "maker/taker role and participant account class from execution evidence",
                    "VIP eligibility and actual award evidence",
                ],
            },
            "weather_and_probability": {
                "weather_days_present": int(coverage.get("weather", {}).get("hrrr_gefs_complete_days", 0)),
                "kalshi_metadata_days_present": int(coverage.get("kalshi_safe_metadata", {}).get("covered_days", 0)),
                "minute_candle_days_present": int(coverage.get("kalshi_one_minute_candles", {}).get("covered_days", 0)),
                "public_trade_days_present": int(coverage.get("kalshi_public_trades", {}).get("covered_days", 0)),
                "clilax_days_present": int(coverage.get("clilax_archive_envelope", {}).get("covered_days", 0)),
                "weather_estimated_gib": float(probability.get("weather_plan", {}).get("estimated_raw_gib", 0.0)),
                "weather_planned_requests": int(probability.get("weather_plan", {}).get("planned_requests", 0)),
                "weather_serial_throttle_floor_hours": float(probability.get("weather_plan", {}).get("serial_throttle_floor_hours_at_two_seconds", 0.0)),
                "obtainable": True,
                "status": "FEASIBLE_NOT_ACQUIRED_BECAUSE_EXECUTION_BARRIER_IS_TERMINAL",
            },
        },
        "gates": {
            "execution_ready": execution_ready,
            "economics_ready": economics_ready,
            "probability_data_ready": probability_ready,
        },
        "all_missing_data_possible_to_obtain_from_verified_sources": False,
        "restart_authorized": execution_ready and economics_ready and probability_ready,
        "status": "BLOCKED_EXTERNAL_EXECUTION_ARCHIVE",
        "blocking_reason": (
            "No verified source covers at least 90% of the registered KXHIGHLAX "
            "event windows with timestamped price-level size and sequence/gap evidence."
        ),
        "required_external_access": {
            "description": (
                "Kalshi-supplied or contractually authorized KXHIGHLAX L2 archive "
                "beginning no later than 2025-08-12 and continuing through 2026-08-31."
            ),
            "fields": [
                "market and event identity", "exchange and receipt timestamps",
                "full price-level quantities", "snapshot/sequence identity",
                "delta semantics", "gap and reconnect records",
                "internal research license",
            ],
            "contact": "institutional@kalshi.com",
        },
        "download_decision": {
            "bulk_weather_and_diagnostic_market_download_started": False,
            "reason": (
                "The registered campaign stops at the execution-source barrier. "
                "Downloading about 15.09 GiB and 25,620 weather requests cannot clear that gate."
            ),
        },
        "network_used_for_this_readiness_build": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    result["manifest_sha256"] = sha256(_canonical(result)).hexdigest()
    return result


def render_report(result: dict[str, Any]) -> str:
    execution = result["data_classes"]["historical_execution"]
    economics = result["data_classes"]["fees_and_settlement"]
    probability = result["data_classes"]["weather_and_probability"]
    return f"""# V5 data search and restart decision

## Decision

The original V5 campaign cannot be restarted honestly with the data currently
available. Its registered execution gate requires timestamped price-level size
and sequence evidence for at least 90% of the July 1, 2025 through August 31,
2026 event windows. The best documented KXHIGHLAX archive begins in March 2026,
so its calendar coverage cannot exceed {execution['best_documented_coverage_upper_bound']:.1%}.

`restart_authorized = false`  
`status = BLOCKED_EXTERNAL_EXECUTION_ARCHIVE`

## What is now in the workspace

* A machine-readable review of eight execution-data sources and one unverified
  licensed-distributor lead.
* {economics['official_source_files_saved']} official or archived-official fee,
  rule, settlement, and API sources, including 604 KXHIGHLAX events and 604
  successful event fee-history checks.
* An outcome-blind 427-day acquisition plan with five frozen chronological folds.
* A verified estimate for the remaining weather acquisition: approximately
  {probability['weather_estimated_gib']:.2f} GiB across
  {probability['weather_planned_requests']:,} requests.

## Missing data by class

| Data class | Can it be acquired now? | Current result |
|---|---|---|
| HRRR/GEFS forecasts | Yes | 0/427 days locally; finite NOAA archive plan is ready |
| Kalshi metadata/candles/trades | Yes | 184 metadata days; later minute candles and prints remain |
| CLILAX/NCEI settlement records | Mostly | 191/427-day archive envelope; reconciliation remains |
| Historical fee and rule evidence | Mostly | 21 official sources saved; exact role/rounding/rule gaps remain |
| Full historical KXHIGHLAX order book | No verified full-window source | Best source covers at most {execution['best_documented_coverage_upper_bound']:.1%} |

## Why the large public downloads were not started

The campaign's first barrier is execution evidence. NOAA forecasts, candles,
and public trades cannot prove what price and size were executable five seconds
after a signal. Starting the 15+ GiB weather download would consume at least
{probability['weather_serial_throttle_floor_hours']:.2f} hours under the current
throttle and would still leave restart authorization false.

## Exact way to unblock the original campaign

Obtain a Kalshi-supplied or contractually authorized KXHIGHLAX Level-2 archive
beginning no later than August 12, 2025 and running through August 31, 2026. It
must preserve market identity, price-level quantities, timestamps, snapshot or
sequence identity, delta semantics, gap/reconnect records, and rights for
internal research. The documented institutional contact is
`institutional@kalshi.com`.

A shorter, separately registered March–August 2026 campaign could use the
CryptoStruct archive after a bounded sample passes continuity, timestamp, and
licensing checks. It would answer a narrower question and would not amend the
frozen V5 result.

## Safety and integrity

No confirmation labels were opened, no live feed was started, and no paper or
live orders were created. The binding manifest is
`data/manifests/v5_restart_data_readiness.json`.
"""


def write(root: Path) -> tuple[Path, Path]:
    root = Path(root).resolve()
    result = build(root)
    output = root / OUTPUT
    report = root / REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    report.write_text(render_report(result), encoding="utf-8", newline="\n")
    return output, report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        paths = write(args.project_root)
        print(json.dumps([str(path) for path in paths], indent=2))
    else:
        print(json.dumps(build(args.project_root), indent=2, sort_keys=True))
