"""Freeze the 78-date V5B confirmation universe without reading outcomes."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from v5a.paid_depth import classify_primary_row


PLAN = Path("configs/v5b_untouched_confirmation_plan.json")
V5A_CONFIG = Path("configs/v5a_paid_depth_readiness.json")
V5A_UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
METADATA = Path("data/raw/v5b_untouched/probalytics/market_metadata.jsonl")
METADATA_MANIFEST = Path("data/raw/v5b_untouched/probalytics/market_metadata_manifest.json")
TOP_OF_BOOK = Path("data/normalized/v5b_untouched_probalytics/target_top_of_book.parquet")
DEPTH_MANIFEST = Path("data/normalized/v5b_untouched_probalytics/manifest.json")
ECONOMICS_MANIFEST = Path("data/raw/v5b_untouched/economics/manifest.json")
ECONOMICS_BOUNDS = Path("v5p/acquisition/economics/manifests/evaluator-bounds.json")
OUTPUT = Path("data/manifests/v5b_untouched_outcome_blind_universe.json")
SCHEMA = "klax-v5b-untouched-outcome-blind-universe-v1"
WEATHER_ROOT = Path("data/normalized/v5p_probability_features")

_TICKER_DATE = re.compile(
    r"^KXHIGHLAX-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})-(?:B|T)"
)
_BETWEEN = re.compile(r"\bbetween\s+(-?\d+)\s*-\s*(-?\d+)\s*°", re.IGNORECASE)
_LESS = re.compile(r"\bless\s+than\s+(-?\d+)\s*°", re.IGNORECASE)
_GREATER = re.compile(r"\bgreater\s+than\s+(-?\d+)\s*°", re.IGNORECASE)


class ConfirmationUniverseError(ValueError):
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
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ConfirmationUniverseError(f"JSON object required: {path}")
    return value


def _verify_hash(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise ConfirmationUniverseError(f"{field} mismatch")


def _safe(value: Mapping[str, Any]) -> None:
    for field in (
        "protected_confirmation_labels_read", "settlement_outcomes_read",
        "outcomes_read", "holdout_labels_opened", "actual_orders_placed",
        "live_feed_used",
    ):
        if field in value and value[field] is not False:
            raise ConfirmationUniverseError(f"safety boundary differs: {field}")


def _dates(start: str, end: str) -> list[str]:
    left, right = date.fromisoformat(start), date.fromisoformat(end)
    return [
        (left + timedelta(days=offset)).isoformat()
        for offset in range((right - left).days + 1)
    ]


def _planned_dates(plan: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    roles: dict[str, str] = {}
    for window in plan["confirmation_windows"]:
        for day in _dates(window["date_start"], window["date_end"]):
            if day in roles:
                raise ConfirmationUniverseError("confirmation windows overlap")
            roles[day] = str(window["role"])
    dates = sorted(roles)
    if len(dates) != 78:
        raise ConfirmationUniverseError("confirmation plan must contain exactly 78 dates")
    return dates, roles


def _ticker_date(ticker: str) -> str:
    match = _TICKER_DATE.match(ticker)
    if match is None:
        raise ConfirmationUniverseError(f"unexpected KXHIGHLAX ticker: {ticker}")
    parsed = datetime.strptime(
        f"{match.group('yy')}{match.group('mon')}{match.group('dd')}", "%y%b%d"
    ).date()
    return parsed.isoformat()


def parse_market_rule(row: Mapping[str, Any]) -> dict[str, Any]:
    """Parse one exact, outcome-blind market rule from Probalytics metadata."""

    ticker = str(row["platform_id"])
    description = str(row["description"])
    rule = description.split("\n\n", 1)[0].strip()
    if not rule or "then the market resolves to Yes" not in rule:
        raise ConfirmationUniverseError(f"unrecognized rule text: {ticker}")
    between, less, greater = _BETWEEN.search(rule), _LESS.search(rule), _GREATER.search(rule)
    matched = sum(value is not None for value in (between, less, greater))
    if matched != 1:
        raise ConfirmationUniverseError(f"ambiguous threshold rule: {ticker}")
    if between:
        lower, upper = int(between.group(1)), int(between.group(2))
        if upper != lower + 1:
            raise ConfirmationUniverseError(f"non-adjacent bracket: {ticker}")
        strike_type, floor, cap = "between", lower, upper
    elif less:
        strike_type, floor, cap = "less", None, int(less.group(1))
    else:
        strike_type, floor, cap = "greater", int(greater.group(1)), None
    if "National Weather Service's Climatological Report (Daily)" in rule:
        source = {
            "name": "NWS Climatological Report",
            "url": "https://forecast.weather.gov/product.php?site=LOX&product=CLI&issuedby=LAX",
        }
    elif "according to The Weather Company" in rule:
        source = {"name": "The Weather Company", "url": None}
    else:
        raise ConfirmationUniverseError(f"settlement source is not exact: {ticker}")
    return {
        "climate_date": _ticker_date(ticker),
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_ticker": ticker,
        "market_type": "binary",
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
        "rules_primary": rule,
        "settlement_sources": [source],
        "settlement_rule_revision_exact": True,
        "market_metadata_id": str(row["id"]),
        "market_url": str(row["url"]),
    }


def _weather_binding(root: Path, climate_date: str) -> dict[str, Any]:
    path = root / WEATHER_ROOT / f"date={climate_date}" / "manifest.json"
    value = _load(path)
    _verify_hash(value, "manifest_sha256")
    _safe(value)
    if (
        value.get("status") != "NORMALIZED_FEATURES_ONLY"
        or value.get("climate_date") != climate_date
        or value.get("decision_time_utc") != "18:00"
        or value.get("coverage", {}).get("hrrr", {}).get("complete") is not True
        or value.get("coverage", {}).get("gefs", {}).get("complete") is not True
        or value.get("network_used") is not False
        or value.get("refit_performed") is not False
    ):
        raise ConfirmationUniverseError(f"weather feature manifest incomplete: {climate_date}")
    for output in value.get("outputs", []):
        child = root / str(output["path"])
        if (
            not child.is_file()
            or _file_hash(child) != output.get("sha256")
            or child.stat().st_size != output.get("bytes")
        ):
            raise ConfirmationUniverseError(f"weather output binding differs: {climate_date}")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _file_hash(path),
        "manifest_sha256": value["manifest_sha256"],
    }


def _new_market_rules(root: Path) -> dict[str, list[dict[str, Any]]]:
    rows = [
        json.loads(line)
        for line in (root / METADATA).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    unique: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (str(row["id"]), str(row["platform_id"]))
        prior = unique.get(key)
        if prior is not None and prior != row:
            raise ConfirmationUniverseError("duplicate metadata identity has conflicting content")
        unique[key] = row
    parsed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_tickers: set[str] = set()
    for row in unique.values():
        market = parse_market_rule(row)
        if market["market_ticker"] in seen_tickers:
            raise ConfirmationUniverseError("platform ticker maps to multiple market identities")
        seen_tickers.add(market["market_ticker"])
        parsed[market["climate_date"]].append(market)
    for day, markets in parsed.items():
        if len(markets) != 6:
            raise ConfirmationUniverseError(f"expected six unique markets for {day}")
    return dict(parsed)


def build(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    plan = _load(workspace / PLAN)
    dates, roles = _planned_dates(plan)
    old_universe = _load(workspace / V5A_UNIVERSE)
    _verify_hash(old_universe)
    _safe(old_universe)
    if old_universe.get("status") != "FROZEN_OUTCOME_BLIND":
        raise ConfirmationUniverseError("V5A source universe is not frozen")
    config = _load(workspace / V5A_CONFIG)
    grading = config["execution_grading"]
    metadata_manifest = _load(workspace / METADATA_MANIFEST)
    depth_manifest = _load(workspace / DEPTH_MANIFEST)
    economics = _load(workspace / ECONOMICS_MANIFEST)
    economics_bounds = _load(workspace / ECONOMICS_BOUNDS)
    _verify_hash(economics_bounds)
    for value in (metadata_manifest, depth_manifest, economics, economics_bounds):
        _safe(value)
    if (
        metadata_manifest.get("registered_date_count") != 50
        or metadata_manifest.get("settlement_outcomes_read") is not False
        or economics.get("registered_date_count") != 50
        or economics.get("event_fee_change_count") != 0
        or economics.get("series_fee_change_count") != 0
        or economics.get("current_fee_schedule_matches_frozen_2026_07_07") is not True
    ):
        raise ConfirmationUniverseError("new source coverage or fee binding differs")

    new_rules = _new_market_rules(workspace)
    top = pd.read_parquet(workspace / TOP_OF_BOOK)
    primary = top.loc[top["target_offset_s"] == int(grading["arrival_delay_seconds"])]
    paid_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in primary.to_dict(orient="records"):
        classified = classify_primary_row(raw, grading)
        key = (
            classified["climate_date"], classified["market_ticker"],
            classified["contract_side"],
        )
        if key in paid_by_key:
            raise ConfirmationUniverseError(f"duplicate paid execution key: {key}")
        paid_by_key[key] = classified

    old_by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in old_universe["records"]:
        old_by_date[str(row["climate_date"])].append(row)

    weather = {day: _weather_binding(workspace, day) for day in dates}
    records: list[dict[str, Any]] = []
    for day in dates:
        if roles[day] == "sealed-v5a-holdout":
            source_rows = old_by_date.get(day, [])
            if len(source_rows) != 12:
                raise ConfirmationUniverseError(f"V5A holdout contract partition differs: {day}")
            for source in source_rows:
                row = deepcopy(dict(source))
                row["partition"] = "confirmation"
                row["confirmation_window_role"] = roles[day]
                row["weather_manifest_sha256"] = weather[day]["manifest_sha256"]
                records.append(row)
            continue

        markets = new_rules.get(day, [])
        if len(markets) != 6:
            raise ConfirmationUniverseError(f"new market rules missing: {day}")
        for market in sorted(markets, key=lambda item: item["market_ticker"]):
            for side in ("YES", "NO"):
                execution = paid_by_key.get((day, market["market_ticker"], side))
                if execution is None or execution["evidence_grade"] == "UNAVAILABLE":
                    grade = "UNAVAILABLE"
                    ask = bid = quote_at = None
                    assumed_fill = None
                    source_kind = None
                else:
                    grade = execution["evidence_grade"]
                    ask = execution["best_ask_cents"]
                    bid = execution["best_bid_cents"]
                    quote_at = execution["quote_at_utc"]
                    assumed_fill = False
                    source_kind = "PAID_FULL_BOOK"
                records.append({
                    **market,
                    "contract_side": side,
                    "partition": "confirmation",
                    "confirmation_window_role": roles[day],
                    "decision_time_utc": "18:00",
                    "execution_evidence_grade": grade,
                    "execution_price_cents": ask,
                    "bid_price_cents": bid,
                    "quote_at_utc": quote_at,
                    "assumed_fill": assumed_fill,
                    "execution_source_kind": source_kind,
                    "probability_ready": True,
                    "fee_rule_ready": True,
                    "weather_manifest_sha256": weather[day]["manifest_sha256"],
                })

    keys = [(r["climate_date"], r["market_ticker"], r["contract_side"]) for r in records]
    if len(records) != 78 * 12 or len(keys) != len(set(keys)):
        raise ConfirmationUniverseError("confirmation contract-side partition differs")
    grade_counts = Counter(row["execution_evidence_grade"] for row in records)
    source_counts = Counter(
        row["settlement_sources"][0]["name"] for row in records
    )
    selected_sources = {
        "plan": PLAN,
        "v5a_config": V5A_CONFIG,
        "v5a_universe": V5A_UNIVERSE,
        "market_metadata": METADATA,
        "market_metadata_manifest": METADATA_MANIFEST,
        "paid_top_of_book": TOP_OF_BOOK,
        "paid_depth_manifest": DEPTH_MANIFEST,
        "economics_manifest": ECONOMICS_MANIFEST,
        "economics_evaluator_bounds": ECONOMICS_BOUNDS,
    }
    body = {
        "schema_version": SCHEMA,
        "status": "FROZEN_OUTCOME_BLIND",
        "campaign_family": "v5b_untouched_confirmation",
        "primary_strategy_candidate_id": plan["primary_strategy"]["candidate_id"],
        "series_ticker": "KXHIGHLAX",
        "station": "KLAX",
        "decision_time_utc": "18:00",
        "calendar_date_count": len(dates),
        "dates": dates,
        "date_roles": roles,
        "probability_ready_date_count": len(dates),
        "execution_available_date_count": len({
            row["climate_date"] for row in records
            if row["execution_evidence_grade"] != "UNAVAILABLE"
        }),
        "fully_priced_date_count": sum(
            all(r["execution_evidence_grade"] != "UNAVAILABLE" for r in records if r["climate_date"] == day)
            for day in dates
        ),
        "contract_side_record_count": len(records),
        "execution_grade_counts": {
            grade: grade_counts.get(grade, 0)
            for grade in ("A", "B_PLUS", "B", "UNAVAILABLE")
        },
        "settlement_source_record_counts": dict(sorted(source_counts.items())),
        "records": sorted(records, key=lambda row: (
            row["climate_date"], row["market_ticker"], row["contract_side"]
        )),
        "source_bindings": {
            name: {"path": path.as_posix(), "sha256": _file_hash(workspace / path)}
            for name, path in selected_sources.items()
        } | {"weather_by_date": weather},
        "selection_frozen": False,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "outcomes_read": False,
        "network_used": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def freeze(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    value = build(workspace)
    path = workspace / OUTPUT
    if path.exists():
        existing = _load(path)
        if existing != value:
            raise ConfirmationUniverseError("immutable confirmation universe differs")
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)
    return value


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    print(json.dumps(freeze(args.project_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
