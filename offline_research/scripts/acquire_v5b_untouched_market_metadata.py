from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from acquire_v5b_untouched_probalytics import Client, PROJECT_ROOT, OUTPUT_ROOT, PLAN_PATH, registered_dates


OUTPUT_PATH = OUTPUT_ROOT / "market_metadata.jsonl"
MANIFEST_PATH = OUTPUT_ROOT / "market_metadata_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata_query(days) -> str:
    clauses = [
        f"startsWith(platform_id, 'KXHIGHLAX-{day:%y%b%d}-'.upper())" for day in days
    ]
    # Python has already uppercased month abbreviations; remove the SQL-looking
    # suffix introduced above so the generated query remains plain ClickHouse.
    prefixes = [f"KXHIGHLAX-{day:%y%b%d}-".upper() for day in days]
    where = "\n       OR ".join(f"startsWith(platform_id, '{prefix}')" for prefix in prefixes)
    return f"""
SELECT id,
       platform_id,
       slug,
       url,
       title,
       description,
       category,
       tags,
       market_type,
       outcomes AS contract_sides,
       created_at,
       opened_at,
       closes_at,
       end_date
FROM markets
WHERE platform = 'KALSHI'
  AND ({where})
ORDER BY platform_id
FORMAT JSONEachRow
""".strip()


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

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    days = registered_dates(plan)
    sql = metadata_query(days)
    lowered = sql.lower()
    prohibited = (
        "resolution_winning_outcome_id",
        "resolution_outcome_payouts",
        "resolution_type",
        "resolution_resolved_at",
    )
    if any(name in lowered for name in prohibited):
        raise RuntimeError("metadata query crosses the settlement-outcome boundary")
    payload = Client(username, password).query(sql)
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    registered_prefixes = {f"KXHIGHLAX-{day:%y%b%d}-".upper() for day in days}
    if any(not any(str(row["platform_id"]).startswith(prefix) for prefix in registered_prefixes) for row in rows):
        raise RuntimeError("metadata response contains an unregistered market")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(payload)
    manifest = {
        "schema_version": "v5b-untouched-probalytics-market-metadata-v1",
        "source": "Probalytics ClickHouse markets table",
        "source_url": "https://app.probalytics.io/sql",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "registered_date_count": len(days),
        "row_count": len(rows),
        "artifact": {
            "path": str(OUTPUT_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "bytes": OUTPUT_PATH.stat().st_size,
            "sha256": sha256(OUTPUT_PATH),
        },
        "selected_columns": [
            "id",
            "platform_id",
            "slug",
            "url",
            "title",
            "description",
            "category",
            "tags",
            "market_type",
            "contract_sides",
            "created_at",
            "opened_at",
            "closes_at",
            "end_date",
        ],
        "settlement_outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "credentials_persisted": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    password = ""


if __name__ == "__main__":
    main()
