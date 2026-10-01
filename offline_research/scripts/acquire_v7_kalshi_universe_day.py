"""Acquire a safe, outcome-blind KXHIGHLAX universe for one elapsed V7 date."""
from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
for candidate in (PROJECT, PROJECT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from klax_lab.acquire_kalshi import HistoricalClient, acquire_metadata
from v7_shadow.campaign import CAMPAIGN_ID, target_dates


def seal(value: dict) -> dict:
    result = dict(value)
    result["self_sha256"] = sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return result


def source_name(rules: str) -> str:
    text = rules.casefold()
    if "weather company" in text:
        return "The Weather Company"
    if "climatological report" in text or "national weather service" in text:
        return "National Weather Service Climatological Report (Daily)"
    raise ValueError("unrecognized settlement source in contract rules")


def run(root: Path, target: date) -> dict:
    config = json.loads((root / "configs/v7_six_week_shadow.json").read_text(encoding="utf-8"))
    if target.isoformat() not in target_dates(config) or target >= datetime.now(UTC).date():
        raise ValueError("V7 market target must be registered and strictly historical")
    day_root = root / "runs/campaigns_v7" / CAMPAIGN_ID / "daily" / target.isoformat()
    output = day_root / "input/contract_universe.json"
    if output.is_file():
        value = json.loads(output.read_text(encoding="utf-8"))
        if value.get("outcomes_read") is not False:
            raise ValueError("cached universe is not outcome blind")
        return value
    workspace = day_root / "quarantine/kalshi_historical_metadata"
    coverage = acquire_metadata(
        HistoricalClient(workspace, max_bytes=100_000_000, min_interval=0.5, retries=2),
        target, target, max_pages=100, series="KXHIGHLAX",
    )
    markets = [row for row in coverage["contracts"] if row["climate_date"] == target.isoformat()]
    if len(markets) != 6 or not all(row["station_identity_screen"] for row in markets):
        raise RuntimeError(f"expected six eligible contracts, found {len(markets)}")
    records = []
    event = {row["event_ticker"] for row in markets}
    if len(event) != 1:
        raise RuntimeError("target contracts do not share one event")
    for market in markets:
        settlement_source = source_name(market["rules_primary"])
        common = {
            "climate_date": target.isoformat(),
            "event_ticker": market["event_ticker"],
            "market_ticker": market["ticker"],
            "market_type": market["market_type"],
            "strike_type": market["strike_type"],
            "floor_strike": market["floor_strike"],
            "cap_strike": market["cap_strike"],
            "rules_primary": market["rules_primary"],
            "settlement_sources": [{"name": settlement_source, "url": None}],
            "decision_time_utc": "18:00",
        }
        records.extend(({**common, "contract_side": side} for side in ("YES", "NO")))
    body = seal({
        "schema_version": "klax-v7-outcome-blind-universe-day-v1",
        "status": "FROZEN_OUTCOME_BLIND",
        "climate_date": target.isoformat(),
        "event_ticker": next(iter(event)),
        "records": sorted(records, key=lambda row: (row["market_ticker"], row["contract_side"])),
        "raw_metadata_quarantined": True,
        "outcomes_read": False,
        "settlement_labels_read": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + ".pending")
    pending.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(output)
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(run(Path(args.project_root).resolve(), args.date), indent=2))


if __name__ == "__main__":
    main()

