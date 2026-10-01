from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "configs/v5b_untouched_confirmation_plan.json"
OUTPUT_ROOT = PROJECT_ROOT / "data/raw/v5b_untouched/economics"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) V5B-untouched-research/1.0"
BASE = "https://external-api.kalshi.com/trade-api/v2"
MAX_REQUESTS = 60
MAX_BYTES = 20_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def date_span(start: str, end: str) -> list[date]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    values: list[date] = []
    current = first
    while current <= last:
        values.append(current)
        current = date.fromordinal(current.toordinal() + 1)
    return values


def registered_dates() -> list[date]:
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    values: list[date] = []
    for window in plan["confirmation_windows"]:
        if window["role"] == "sealed-v5a-holdout":
            continue
        values.extend(date_span(window["date_start"], window["date_end"]))
    if len(values) != 50 or len(values) != len(set(values)):
        raise RuntimeError("untouched economics date inventory differs")
    return values


def get(url: str, attempts: int = 4, timeout: int = 120) -> tuple[bytes, dict[str, str]]:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/pdf,application/json,*/*",
                    "Referer": "https://kalshi.com/regulatory/fee-schedule",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read(), {
                    "status": str(response.status),
                    "content_type": response.headers.get("Content-Type", ""),
                    "final_url": response.geturl(),
                }
        except Exception as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def write(name: str, payload: bytes) -> dict[str, object]:
    path = OUTPUT_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    days = registered_dates()
    requests = 0
    total_bytes = 0
    sources: list[dict[str, object]] = []

    fixed_sources = [
        (
            "kalshi-fee-schedule-current.pdf",
            "https://kalshi.com/docs/kalshi-fee-schedule.pdf",
            "official current exchange fee schedule",
        ),
        (
            "kxhiglax-series-fee-history.json",
            f"{BASE}/series/fee_changes?series_ticker=KXHIGHLAX&show_historical=true",
            "official historical series fee changes",
        ),
        (
            "kxhiglax-series-current.json",
            f"{BASE}/series/KXHIGHLAX",
            "official current series terms and fee multiplier",
        ),
    ]
    for name, url, purpose in fixed_sources:
        payload, headers = get(url)
        requests += 1
        total_bytes += len(payload)
        if name.endswith(".pdf") and not payload.startswith(b"%PDF"):
            raise RuntimeError("official fee-schedule response is not a PDF")
        if requests > MAX_REQUESTS or total_bytes > MAX_BYTES:
            raise RuntimeError("economics acquisition budget exceeded")
        sources.append({**write(name, payload), "url": url, "purpose": purpose, **headers})

    event_rows: list[dict[str, object]] = []
    for day in days:
        event_ticker = f"KXHIGHLAX-{day:%y%b%d}".upper()
        url = f"{BASE}/events/fee_changes?event_ticker={quote(event_ticker, safe='')}&limit=1000"
        payload, headers = get(url)
        requests += 1
        total_bytes += len(payload)
        if requests > MAX_REQUESTS or total_bytes > MAX_BYTES:
            raise RuntimeError("economics acquisition budget exceeded")
        value = json.loads(payload)
        changes = value.get("event_fee_change_arr", value.get("event_fee_changes", []))
        if not isinstance(changes, list):
            raise RuntimeError(f"unexpected event fee response: {event_ticker}")
        event_rows.append(
            {
                "event_ticker": event_ticker,
                "url": url,
                "fee_changes": changes,
                "response_headers": headers,
            }
        )

    event_payload = (json.dumps(event_rows, indent=2, sort_keys=True) + "\n").encode()
    sources.append(
        {
            **write("event-fee-changes.json", event_payload),
            "url": f"{BASE}/events/fee_changes?event_ticker=<registered-event>&limit=1000",
            "purpose": "official per-event fee changes for the 50 untouched dates",
        }
    )

    current_pdf = OUTPUT_ROOT / "kalshi-fee-schedule-current.pdf"
    prior_pdf = PROJECT_ROOT / "v5p/acquisition/economics/sources/kalshi-fee-schedule-effective-2026-07-07.pdf"
    current_hash = sha256(current_pdf)
    prior_hash = sha256(prior_pdf)
    series_history = json.loads((OUTPUT_ROOT / "kxhiglax-series-fee-history.json").read_text(encoding="utf-8"))
    series_current = json.loads((OUTPUT_ROOT / "kxhiglax-series-current.json").read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "v5b-untouched-economics-acquisition-v1",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "registered_date_count": len(days),
        "request_count": requests,
        "response_bytes": total_bytes,
        "sources": sources,
        "current_fee_schedule_matches_frozen_2026_07_07": current_hash == prior_hash,
        "current_fee_schedule_sha256": current_hash,
        "frozen_2026_07_07_fee_schedule_sha256": prior_hash,
        "series_fee_change_count": len(series_history.get("series_fee_change_arr", [])),
        "event_fee_change_count": sum(len(row["fee_changes"]) for row in event_rows),
        "series_fee_type": series_current.get("series", {}).get("fee_type"),
        "series_fee_multiplier": series_current.get("series", {}).get("fee_multiplier"),
        "settlement_outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "credentials_used": False,
        "live_feed_used": False,
        "actual_orders_placed": False,
    }
    manifest_path = OUTPUT_ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
