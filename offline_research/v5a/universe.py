"""Freeze the 92-day V5A outcome-blind date and contract universe."""

from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping


START = date(2026, 6, 1)
END = date(2026, 8, 31)
CONFIG = Path("configs/v5a_paid_depth_readiness.json")
CLOSURE = Path("data/manifests/v5a_priority_acquisition_closure.json")
PAID = Path("data/manifests/v5a_paid_execution_outcome_blind.json")
FALLBACK = Path("data/manifests/v5a_grade_b_candle_fallback_outcome_blind.json")
RULES = Path("data/manifests/v5a_event_rules_outcome_blind.json")
OUTPUT = Path("data/manifests/v5a_outcome_blind_universe.json")
SEAL = Path("data/manifests/v5a_holdout_seal.json")
SCHEMA = "klax-v5a-outcome-blind-universe-v1"
SEAL_SCHEMA = "klax-v5a-holdout-seal-v1"


class V5AUniverseError(ValueError):
    pass


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V5AUniverseError(f"missing or invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise V5AUniverseError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise V5AUniverseError(f"{field} mismatch")


def _safe(value: Mapping[str, Any]) -> None:
    for field in (
        "protected_confirmation_labels_read", "outcomes_read",
        "outcomes_propagated", "protected_confirmation_labels_read_by_planner",
    ):
        if field in value and value[field] is not False:
            raise V5AUniverseError(f"outcome safety differs: {field}")
    for field in ("actual_orders_placed", "live_feed_used"):
        if field in value and value[field] is not False:
            raise V5AUniverseError(f"trading safety differs: {field}")


def _days() -> list[str]:
    return [
        (START + timedelta(days=offset)).isoformat()
        for offset in range((END - START).days + 1)
    ]


def _weather_bindings(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for climate_date in _days():
        path = root / "data/normalized/v5p_probability_features" / f"date={climate_date}" / "manifest.json"
        value = _load(path)
        _verify(value, "manifest_sha256")
        _safe(value)
        if (
            value.get("climate_date") != climate_date
            or value.get("decision_time_utc") != "18:00"
            or value.get("status") != "NORMALIZED_FEATURES_ONLY"
            or value.get("coverage", {}).get("hrrr", {}).get("complete") is not True
            or value.get("coverage", {}).get("gefs", {}).get("complete") is not True
            or value.get("refit_performed") is not False
            or value.get("network_used") is not False
        ):
            raise V5AUniverseError(f"weather feature manifest incomplete: {climate_date}")
        for output in value.get("outputs", []):
            child = root / str(output.get("path"))
            if (
                not child.is_file()
                or _file_hash(child) != output.get("sha256")
                or child.stat().st_size != output.get("bytes")
            ):
                raise V5AUniverseError(f"weather output binding differs: {climate_date}")
        result[climate_date] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_hash(path),
            "manifest_sha256": value["manifest_sha256"],
        }
    return result


def build(root: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    workspace = Path(root).resolve()
    config_path = workspace / CONFIG
    config = _load(config_path)
    if (
        config.get("schema_version") != "klax-v5a-paid-depth-registration-v1"
        or config.get("offline_only") is not True
        or config.get("live_or_paper_orders_authorized") is not False
        or config.get("source_window", {}).get("calendar_days") != 92
    ):
        raise V5AUniverseError("V5A registration differs")

    closure = _load(workspace / CLOSURE)
    paid = _load(workspace / PAID)
    fallback = _load(workspace / FALLBACK)
    rules = _load(workspace / RULES)
    for value in (closure, paid, fallback, rules):
        _verify(value)
        _safe(value)
    if (
        closure.get("status") != "CLOSED_92_DAY_WINDOW"
        or closure.get("weather_complete_date_count") != 92
        or closure.get("weather_unavailable_dates") != []
        or closure.get("registration", {}).get("sha256") != _file_hash(config_path)
    ):
        raise V5AUniverseError("92-day acquisition closure differs")
    if (
        rules.get("event_count") != 92
        or rules.get("event_rules_bound_count") != 92
        or fallback.get("paid_execution_manifest", {}).get("self_sha256") != paid.get("self_sha256")
        or paid.get("probability_scoring_date_count") != 92
    ):
        raise V5AUniverseError("rule or execution source binding differs")

    weather = _weather_bindings(workspace)
    paid_by_key = {
        (row["climate_date"], row["market_ticker"], row["contract_side"]): row
        for row in paid["records"]
    }
    fallback_by_key = {
        (row["climate_date"], row["market_ticker"], row["contract_side"]): row
        for row in fallback["records"]
    }
    events_by_date = {event["climate_date"]: event for event in rules["events"]}
    dates = _days()
    development_count = math.floor(len(dates) * 0.70)
    development_dates = dates[:development_count]
    holdout_dates = dates[development_count:]
    partitions = {target: "development" for target in development_dates} | {
        target: "holdout" for target in holdout_dates
    }

    records = []
    executable_dates: set[str] = set()
    fully_priced_dates: set[str] = set()
    for climate_date in dates:
        event = events_by_date.get(climate_date)
        if event is None or event.get("contract_partition_exact") is not True:
            raise V5AUniverseError(f"event rule partition missing: {climate_date}")
        day_records = []
        for market in event["markets"]:
            for side in ("YES", "NO"):
                key = (climate_date, market["ticker"], side)
                paid_row = paid_by_key.get(key)
                fallback_row = fallback_by_key.get(key)
                if paid_row is not None and paid_row["evidence_grade"] in {"A", "B_PLUS"}:
                    grade = paid_row["evidence_grade"]
                    ask = paid_row["best_ask_cents"]
                    bid = paid_row["best_bid_cents"]
                    quote_at = paid_row["quote_at_utc"]
                    assumed_fill = False
                    source_kind = "PAID_FULL_BOOK"
                elif fallback_row is not None:
                    grade = "B"
                    ask = fallback_row["best_ask_proxy_cents"]
                    bid = fallback_row["best_bid_proxy_cents"]
                    quote_at = fallback_row["quote_end_at_utc"]
                    assumed_fill = True
                    source_kind = "PUBLIC_ONE_MINUTE_CANDLE"
                else:
                    grade = "UNAVAILABLE"
                    ask = bid = quote_at = None
                    assumed_fill = None
                    source_kind = None
                row = {
                    "climate_date": climate_date,
                    "partition": partitions[climate_date],
                    "event_ticker": event["event_ticker"],
                    "market_ticker": market["ticker"],
                    "contract_side": side,
                    "market_type": market["market_type"],
                    "strike_type": market["strike_type"],
                    "floor_strike": market["floor_strike"],
                    "cap_strike": market["cap_strike"],
                    "rules_primary": market["rules_primary"],
                    "settlement_sources": event["settlement_sources"],
                    "decision_time_utc": "18:00",
                    "execution_evidence_grade": grade,
                    "execution_price_cents": ask,
                    "bid_price_cents": bid,
                    "quote_at_utc": quote_at,
                    "assumed_fill": assumed_fill,
                    "execution_source_kind": source_kind,
                    "probability_ready": True,
                    "fee_rule_ready": True,
                    "settlement_rule_revision_exact": True,
                    "weather_manifest_sha256": weather[climate_date]["manifest_sha256"],
                }
                day_records.append(row)
                records.append(row)
        if any(row["execution_evidence_grade"] != "UNAVAILABLE" for row in day_records):
            executable_dates.add(climate_date)
        if all(row["execution_evidence_grade"] != "UNAVAILABLE" for row in day_records):
            fully_priced_dates.add(climate_date)

    grade_counts = {
        grade: sum(row["execution_evidence_grade"] == grade for row in records)
        for grade in ("A", "B_PLUS", "B", "UNAVAILABLE")
    }
    bindings = {
        "registration": {"path": CONFIG.as_posix(), "sha256": _file_hash(config_path)},
        "acquisition_closure": {"path": CLOSURE.as_posix(), "sha256": _file_hash(workspace / CLOSURE)},
        "paid_execution": {"path": PAID.as_posix(), "sha256": _file_hash(workspace / PAID), "self_sha256": paid["self_sha256"]},
        "grade_b_fallback": {"path": FALLBACK.as_posix(), "sha256": _file_hash(workspace / FALLBACK), "self_sha256": fallback["self_sha256"]},
        "event_rules": {"path": RULES.as_posix(), "sha256": _file_hash(workspace / RULES), "self_sha256": rules["self_sha256"]},
        "weather_by_date": weather,
    }
    body = {
        "schema_version": SCHEMA,
        "status": "FROZEN_OUTCOME_BLIND",
        "campaign_family": config["campaign_family"],
        "series_ticker": "KXHIGHLAX",
        "station": "KLAX",
        "date_start": START.isoformat(),
        "date_end": END.isoformat(),
        "calendar_date_count": 92,
        "probability_ready_date_count": 92,
        "execution_available_date_count": len(executable_dates),
        "execution_abstention_dates": sorted(set(dates) - executable_dates),
        "fully_priced_date_count": len(fully_priced_dates),
        "contract_side_record_count": len(records),
        "execution_grade_counts": grade_counts,
        "split": {
            "method": "chronological_floor_70_percent_development_remainder_holdout",
            "development_dates": development_dates,
            "holdout_dates": holdout_dates,
            "development_date_count": len(development_dates),
            "holdout_date_count": len(holdout_dates),
        },
        "optimization_permitted": len(development_dates) >= 10,
        "records": records,
        "source_bindings": bindings,
        "holdout_access_authorized": False,
        "protected_confirmation_labels_read": False,
        "outcomes_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    seal = {
        "schema_version": SEAL_SCHEMA,
        "status": "SEALED",
        "universe_self_sha256": body["self_sha256"],
        "holdout_dates": holdout_dates,
        "holdout_dates_sha256": sha256(json.dumps(
            holdout_dates, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest(),
        "label_payload_present": False,
        "label_payload_path": None,
        "holdout_labels_opened": False,
        "holdout_evaluations_consumed": 0,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    seal["self_sha256"] = _canonical_hash(seal)
    return body, seal


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if _load(path) != dict(value):
            raise V5AUniverseError(f"immutable frozen artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def freeze(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    universe, seal = build(workspace)
    _write_immutable(workspace / OUTPUT, universe)
    _write_immutable(workspace / SEAL, seal)
    return universe


def verify(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    universe = _load(workspace / OUTPUT)
    seal = _load(workspace / SEAL)
    _verify(universe)
    _verify(seal)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("outcomes_read") is not False
        or universe.get("holdout_access_authorized") is not False
        or seal.get("universe_self_sha256") != universe.get("self_sha256")
        or seal.get("label_payload_present") is not False
        or seal.get("holdout_labels_opened") is not False
        or seal.get("holdout_evaluations_consumed") != 0
    ):
        raise V5AUniverseError("frozen universe safety differs")
    return {
        "schema_version": "klax-v5a-universe-verification-v1",
        "status": "VERIFIED",
        "universe_self_sha256": universe["self_sha256"],
        "holdout_seal_self_sha256": seal["self_sha256"],
        "calendar_date_count": universe["calendar_date_count"],
        "development_date_count": universe["split"]["development_date_count"],
        "holdout_date_count": universe["split"]["holdout_date_count"],
        "execution_available_date_count": universe["execution_available_date_count"],
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = freeze(args.project_root) if args.action == "freeze" else verify(args.project_root)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
