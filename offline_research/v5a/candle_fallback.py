"""Build outcome-blind Grade-B 18:00 quote fallbacks for V5A.

The public Kalshi one-minute candles do not contain order-book quantity or
prove a fill.  They are used only where the paid full-book archive has no
usable price.  The last candle ending no later than 18:00 UTC is accepted only
when it is at most 60 seconds old, which avoids look-ahead past the registered
decision time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


PAID_PATH = Path("data/manifests/v5a_paid_execution_outcome_blind.json")
MANIFEST_GLOB = "data/raw/v5p/kalshi_workspace/data/manifests/kalshi_candles_1m_*.json"
OUTPUT_PATH = Path("data/manifests/v5a_grade_b_candle_fallback_outcome_blind.json")
SCHEMA = "klax-v5a-grade-b-candle-fallback-outcome-blind-v1"


class CandleFallbackError(ValueError):
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
        raise CandleFallbackError(f"JSON object required: {path}")
    return value


def _verify_self_hash(value: Mapping[str, Any]) -> None:
    if value.get("self_sha256") != _canonical_hash(value):
        raise CandleFallbackError("self_sha256 mismatch")


def _cents(value: Any) -> int | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise CandleFallbackError("candle price is invalid") from exc
    cents = decimal * 100
    if cents != cents.to_integral_value() or not Decimal(0) <= cents <= Decimal(100):
        raise CandleFallbackError("candle price is not an exact cent")
    return int(cents)


def _extract_quote(payload: Mapping[str, Any], *, decision_epoch: int,
                   contract_side: str) -> dict[str, Any] | None:
    rows = payload.get("candlesticks")
    if not isinstance(rows, list):
        raise CandleFallbackError("candlestick payload is malformed")
    candidates = [
        row for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("end_period_ts"), int)
        and 0 <= decision_epoch - int(row["end_period_ts"]) <= 60
    ]
    if not candidates:
        return None
    candle = max(candidates, key=lambda row: int(row["end_period_ts"]))
    if contract_side == "YES":
        ask = _cents((candle.get("yes_ask") or {}).get("close"))
        bid = _cents((candle.get("yes_bid") or {}).get("close"))
    elif contract_side == "NO":
        yes_bid = _cents((candle.get("yes_bid") or {}).get("close"))
        yes_ask = _cents((candle.get("yes_ask") or {}).get("close"))
        ask = None if yes_bid is None else 100 - yes_bid
        bid = None if yes_ask is None else 100 - yes_ask
    else:
        raise CandleFallbackError("contract side is invalid")
    if ask is None:
        return None
    end_epoch = int(candle["end_period_ts"])
    return {
        "quote_end_at_utc": datetime.fromtimestamp(end_epoch, UTC).isoformat().replace("+00:00", "Z"),
        "quote_age_seconds_at_decision": decision_epoch - end_epoch,
        "best_bid_proxy_cents": bid,
        "best_ask_proxy_cents": ask,
    }


def build(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    paid_path = workspace / PAID_PATH
    paid = _load(paid_path)
    _verify_self_hash(paid)
    if paid.get("outcomes_read") is not False or paid.get("protected_confirmation_labels_read") is not False:
        raise CandleFallbackError("paid execution source is not outcome-blind")

    sources: dict[tuple[str, str], dict[str, Any]] = {}
    source_manifests = []
    for manifest_path in sorted(workspace.glob(MANIFEST_GLOB)):
        manifest = _load(manifest_path)
        if manifest.get("status") != "complete" or not str(manifest.get("execution_grade", "")).startswith("B:"):
            raise CandleFallbackError("candle manifest evidence grade differs")
        source_manifests.append({
            "path": manifest_path.relative_to(workspace).as_posix(),
            "sha256": _file_hash(manifest_path),
        })
        for row in manifest.get("contracts", []):
            if not isinstance(row, Mapping) or row.get("status") != "downloaded" or int(row.get("rows", 0)) <= 0:
                continue
            raw_path = workspace / "data/raw/v5p/kalshi_workspace" / str(row["path"])
            if not raw_path.is_file() or _file_hash(raw_path) != row.get("source_sha256"):
                raise CandleFallbackError("raw candle source binding differs")
            key = (str(row["climate_date"]), str(row["ticker"]))
            sources[key] = {
                "path": raw_path.relative_to(workspace).as_posix(),
                "sha256": row["source_sha256"],
            }

    records = []
    for row in paid.get("records", []):
        if row.get("evidence_grade") != "UNAVAILABLE":
            continue
        climate_date = str(row["climate_date"])
        ticker = str(row["market_ticker"])
        source = sources.get((climate_date, ticker))
        if source is None:
            continue
        decision = datetime.fromisoformat(climate_date + "T18:00:00+00:00")
        payload = _load(workspace / source["path"])
        if payload.get("ticker") != ticker:
            raise CandleFallbackError("raw candle ticker differs")
        quote = _extract_quote(
            payload, decision_epoch=int(decision.timestamp()),
            contract_side=str(row["contract_side"]),
        )
        if quote is None:
            continue
        records.append({
            "climate_date": climate_date,
            "event_ticker": row["event_ticker"],
            "market_ticker": ticker,
            "contract_side": row["contract_side"],
            "decision_at_utc": decision.isoformat().replace("+00:00", "Z"),
            **quote,
            "evidence_grade": "B",
            "assumed_fill": True,
            "displayed_quantity_verified": False,
            "promotion_eligible_execution": False,
            "source": source,
        })
    records.sort(key=lambda row: (
        row["climate_date"], row["market_ticker"], row["contract_side"]
    ))
    dates = sorted({row["climate_date"] for row in records})
    body = {
        "schema_version": SCHEMA,
        "paid_execution_manifest": {
            "path": PAID_PATH.as_posix(),
            "sha256": _file_hash(paid_path),
            "self_sha256": paid["self_sha256"],
        },
        "source_manifests": source_manifests,
        "decision_time_utc": "18:00",
        "maximum_quote_age_seconds": 60,
        "record_count": len(records),
        "date_count": len(dates),
        "dates": dates,
        "records": records,
        "grade_b_is_assumed_fill": True,
        "outcomes_read": False,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def write(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    value = build(workspace)
    path = workspace / OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)
    return value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    value = build(args.project_root) if args.no_write else write(args.project_root)
    print(json.dumps({
        "output": OUTPUT_PATH.as_posix(),
        "self_sha256": value["self_sha256"],
        "record_count": value["record_count"],
        "date_count": value["date_count"],
        "dates": value["dates"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
