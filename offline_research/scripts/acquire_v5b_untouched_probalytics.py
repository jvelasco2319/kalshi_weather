from __future__ import annotations

import argparse
import base64
import csv
import getpass
import hashlib
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ENDPOINT = "https://clickhouse.probalytics.io:8443/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "configs/v5b_untouched_confirmation_plan.json"
OUTPUT_ROOT = PROJECT_ROOT / "data/raw/v5b_untouched/probalytics"
TARGET_OFFSETS = (0, 5, 30, 60)
WINDOW_START = "17:55:00"
WINDOW_END = "18:06:00"
MAX_REQUESTS = 120
MAX_BYTES = 250_000_000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def date_span(start: date, end: date) -> list[date]:
    values: list[date] = []
    current = start
    while current <= end:
        values.append(current)
        current += timedelta(days=1)
    return values


def registered_dates(plan: dict[str, object]) -> list[date]:
    values: list[date] = []
    for window in plan["confirmation_windows"]:  # type: ignore[index]
        role = str(window["role"])
        if role == "sealed-v5a-holdout":
            continue
        values.extend(
            date_span(
                date.fromisoformat(str(window["date_start"])),
                date.fromisoformat(str(window["date_end"])),
            )
        )
    if len(values) != 50 or len(values) != len(set(values)):
        raise RuntimeError("expected exactly 50 unique paid-depth acquisition dates")
    return values


def event_prefix(day: date) -> str:
    return f"KXHIGHLAX-{day:%y%b%d}".upper() + "-%"


def coverage_query(day: date) -> str:
    start = f"{day.isoformat()} {WINDOW_START}"
    end = f"{day.isoformat()} {WINDOW_END}"
    return f"""
SELECT toDate(timestamp) AS observation_date,
       count() AS snapshots,
       uniqExact(market_platform_id) AS contracts,
       uniqExact(outcome.id) AS outcomes,
       countIf(state = 'VERIFIED') AS verified,
       countIf(state = 'INTERMEDIATE') AS intermediate,
       countIf(continuity = 'CONTIGUOUS') AS contiguous,
       countIf(continuity = 'RESET') AS resets,
       countIf(continuity = 'UNKNOWN') AS unknown,
       min(timestamp) AS first_snapshot,
       max(timestamp) AS last_snapshot
FROM orderbook_snapshots
WHERE platform = 'KALSHI'
  AND market_platform_id LIKE '{event_prefix(day)}'
  AND timestamp >= toDateTime64('{start}', 9, 'UTC')
  AND timestamp < toDateTime64('{end}', 9, 'UTC')
GROUP BY observation_date
FORMAT CSVWithNames
""".strip()


def target_query(day: date) -> str:
    start = f"{day.isoformat()} {WINDOW_START}"
    end = f"{day.isoformat()} {WINDOW_END}"
    base = f"{day.isoformat()} 18:00:00"
    offsets = ", ".join(str(value) for value in TARGET_OFFSETS)
    return f"""
WITH toDateTime64('{base}', 9, 'UTC') + toIntervalSecond(target_offset_s) AS target_ts
SELECT '{day.isoformat()}' AS climate_date,
       market_id,
       market_platform_id,
       outcome.id AS outcome_id,
       outcome.platform_id AS outcome_platform_id,
       outcome.name AS outcome_name,
       outcome.index AS outcome_index,
       target_offset_s,
       target_ts,
       countIf(timestamp <= target_ts) AS before_observations,
       maxIf(timestamp, timestamp <= target_ts) AS before_timestamp,
       argMaxIf(indexed_at, timestamp, timestamp <= target_ts) AS before_indexed_at,
       argMaxIf(bids, timestamp, timestamp <= target_ts) AS before_bids,
       argMaxIf(asks, timestamp, timestamp <= target_ts) AS before_asks,
       argMaxIf(hash, timestamp, timestamp <= target_ts) AS before_hash,
       argMaxIf(state, timestamp, timestamp <= target_ts) AS before_state,
       argMaxIf(continuity, timestamp, timestamp <= target_ts) AS before_continuity,
       argMaxIf(path_index, timestamp, timestamp <= target_ts) AS before_path_index,
       countIf(timestamp >= target_ts) AS after_observations,
       minIf(timestamp, timestamp >= target_ts) AS after_timestamp,
       argMinIf(indexed_at, timestamp, timestamp >= target_ts) AS after_indexed_at,
       argMinIf(bids, timestamp, timestamp >= target_ts) AS after_bids,
       argMinIf(asks, timestamp, timestamp >= target_ts) AS after_asks,
       argMinIf(hash, timestamp, timestamp >= target_ts) AS after_hash,
       argMinIf(state, timestamp, timestamp >= target_ts) AS after_state,
       argMinIf(continuity, timestamp, timestamp >= target_ts) AS after_continuity,
       argMinIf(path_index, timestamp, timestamp >= target_ts) AS after_path_index
FROM orderbook_snapshots
ARRAY JOIN [{offsets}] AS target_offset_s
WHERE platform = 'KALSHI'
  AND market_platform_id LIKE '{event_prefix(day)}'
  AND timestamp >= toDateTime64('{start}', 9, 'UTC')
  AND timestamp < toDateTime64('{end}', 9, 'UTC')
GROUP BY market_id,
         market_platform_id,
         outcome_id,
         outcome_platform_id,
         outcome_name,
         outcome_index,
         target_offset_s,
         target_ts
ORDER BY market_platform_id, outcome_index, target_offset_s
FORMAT JSONEachRow
""".strip()


class Client:
    def __init__(self, username: str, password: str) -> None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": "kalshi-offline-research/1.0",
        }
        self.context = ssl.create_default_context()

    def query(self, sql: str, timeout: int = 300) -> bytes:
        params = urllib.parse.urlencode({"database": "probalytics"})
        request = urllib.request.Request(
            ENDPOINT + "?" + params,
            data=sql.encode("utf-8"),
            method="POST",
            headers=self.headers,
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=timeout + 30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ClickHouse HTTP {exc.code}: {detail}") from exc


def parse_coverage(payload: bytes) -> dict[str, object]:
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    if not rows:
        return {
            "snapshots": 0,
            "contracts": 0,
            "outcomes": 0,
            "verified": 0,
            "intermediate": 0,
            "contiguous": 0,
            "resets": 0,
            "unknown": 0,
            "first_snapshot": None,
            "last_snapshot": None,
        }
    row = rows[0]
    integer_fields = (
        "snapshots",
        "contracts",
        "outcomes",
        "verified",
        "intermediate",
        "contiguous",
        "resets",
        "unknown",
    )
    parsed: dict[str, object] = {key: int(row[key]) for key in integer_fields}
    parsed["first_snapshot"] = row["first_snapshot"]
    parsed["last_snapshot"] = row["last_snapshot"]
    return parsed


def initial_state(plan: dict[str, object], days: list[date]) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "v5b-untouched-probalytics-acquisition-v1",
        "source": "Probalytics ClickHouse historical Kalshi full-depth order books",
        "source_url": "https://app.probalytics.io/sql",
        "plan_path": str(PLAN_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "plan_sha256": sha256_file(PLAN_PATH),
        "registered_dates": [day.isoformat() for day in days],
        "decision_time_utc": "18:00:00",
        "target_offsets_seconds": list(TARGET_OFFSETS),
        "daily_window_utc": f"{WINDOW_START}-{WINDOW_END}",
        "completed_dates": [],
        "failed_dates": [],
        "charged_requests": 0,
        "downloaded_bytes": 0,
        "limits": {"maximum_requests": MAX_REQUESTS, "maximum_bytes": MAX_BYTES},
        "created_at_utc": now,
        "updated_at_utc": now,
        "credentials_persisted": False,
        "network_scope": "finite_historical_acquisition",
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
        "status": "RUNNING",
    }


def run(client: Client) -> None:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    days = registered_dates(plan)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = OUTPUT_ROOT / "recovery-state.json"
    state = initial_state(plan, days)
    if state_path.exists():
        prior = json.loads(state_path.read_text(encoding="utf-8"))
        fixed = (
            prior.get("schema_version") == state["schema_version"]
            and prior.get("plan_sha256") == state["plan_sha256"]
            and prior.get("registered_dates") == state["registered_dates"]
        )
        if not fixed:
            raise RuntimeError("existing recovery state does not match the frozen plan")
        state = prior
        state["status"] = "RUNNING"
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()

    completed = set(state.get("completed_dates", []))
    for day in days:
        day_text = day.isoformat()
        day_root = OUTPUT_ROOT / f"date={day_text}"
        manifest_path = day_root / "manifest.json"
        if day_text in completed and manifest_path.exists():
            continue
        if int(state["charged_requests"]) + 2 > MAX_REQUESTS:
            raise RuntimeError("request budget exhausted")

        day_root.mkdir(parents=True, exist_ok=True)
        coverage_sql = coverage_query(day)
        target_sql = target_query(day)
        try:
            coverage_payload = client.query(coverage_sql)
            state["charged_requests"] = int(state["charged_requests"]) + 1
            target_payload = client.query(target_sql)
            state["charged_requests"] = int(state["charged_requests"]) + 1
            added_bytes = len(coverage_payload) + len(target_payload)
            if int(state["downloaded_bytes"]) + added_bytes > MAX_BYTES:
                raise RuntimeError("download byte budget exhausted")

            coverage = parse_coverage(coverage_payload)
            target_rows = sum(1 for line in target_payload.splitlines() if line.strip())
            coverage_path = day_root / "coverage.csv"
            targets_path = day_root / "target_books.jsonl"
            coverage_path.write_bytes(coverage_payload)
            targets_path.write_bytes(target_payload)
            (day_root / "coverage.sql").write_text(coverage_sql + "\n", encoding="utf-8")
            (day_root / "target_books.sql").write_text(target_sql + "\n", encoding="utf-8")
            artifacts = [
                {
                    "path": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in (coverage_path, targets_path)
            ]
            day_manifest = {
                "schema_version": "v5b-untouched-probalytics-day-v1",
                "climate_date": day_text,
                "event_ticker_prefix": event_prefix(day),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "coverage": coverage,
                "target_rows": target_rows,
                "artifacts": artifacts,
                "contains_settlement_outcomes": False,
                "campaign_evidence_candidate": bool(coverage["snapshots"] and target_rows),
                "credentials_persisted": False,
            }
            atomic_json(manifest_path, day_manifest)
            completed.add(day_text)
            state["completed_dates"] = sorted(completed)
            state["failed_dates"] = [
                item for item in state.get("failed_dates", []) if item.get("date") != day_text
            ]
            state["downloaded_bytes"] = int(state["downloaded_bytes"]) + added_bytes
            state["last_completed_date"] = day_text
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(state_path, state)
            print(
                json.dumps(
                    {
                        "date": day_text,
                        "snapshots": coverage["snapshots"],
                        "contracts": coverage["contracts"],
                        "target_rows": target_rows,
                        "completed": len(completed),
                        "total": len(days),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:
            state.setdefault("failed_dates", []).append(
                {"date": day_text, "type": type(exc).__name__, "message": str(exc)}
            )
            state["status"] = "FAILED"
            state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            atomic_json(state_path, state)
            raise
        time.sleep(0.05)

    state["status"] = "COMPLETE"
    state["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    state["updated_at_utc"] = state["completed_at_utc"]
    atomic_json(state_path, state)
    print(json.dumps(state, indent=2, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username")
    args = parser.parse_args()
    username = args.username or os.environ.get("PROBALYTICS_USERNAME", "").strip()
    password = os.environ.get("PROBALYTICS_PASSWORD") or getpass.getpass(
        "Probalytics ClickHouse password: "
    )
    if not username:
        username = input("Probalytics ClickHouse username: ").strip()
    if not username or not password:
        raise SystemExit("Credentials are required")
    try:
        run(Client(username, password))
    finally:
        password = ""


if __name__ == "__main__":
    main()
