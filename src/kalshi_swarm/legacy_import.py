from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _contains(row: dict[str, Any], actual: float) -> bool:
    kind = str(row.get("strike_type") or "").lower()
    floor, cap = row.get("floor_strike"), row.get("cap_strike")
    if kind == "less":
        return cap is not None and actual < float(cap)
    if kind == "greater":
        return floor is not None and actual > float(floor)
    if floor is not None and cap is not None:
        return float(floor) <= actual <= float(cap)
    return False


def convert(research_root: str | Path, output: str | Path) -> int:
    root = Path(research_root).resolve()
    if not (root / "v5b" / "evaluation.py").is_file():
        raise FileNotFoundError(f"full research repo not found: {root}")
    sys.path.insert(0, str(root))
    from v5b.evaluation import load_development  # type: ignore
    from v8.regime_calibration import load_development_records  # type: ignore
    from v10.evaluate_combinations import build_features  # type: ignore

    context = load_development(root)
    feature_rows = build_features(root, load_development_records(root)[0])
    gradients = {item["record"].climate_date: item["observed_pressure_gradient_hpa"] for item in feature_rows}
    records: list[dict[str, Any]] = []
    for day in context["dates"]:
        all_rows = context["rows_by_date"][day]
        yes_rows = sorted(
            (row for row in all_rows if row["contract_side"] == "YES"),
            key=lambda row: (-10000 if row["floor_strike"] is None else float(row["floor_strike"]), row["market_ticker"]),
        )
        actual = float(context["labels"][day])
        winners = [index for index, row in enumerate(yes_rows) if _contains(row, actual)]
        if len(yes_rows) != 6 or len(winners) != 1:
            continue
        by_contract: dict[str, dict[str, Any]] = {}
        for row in all_rows:
            ticker = str(row["market_ticker"])
            quote = by_contract.setdefault(ticker, {"ticker": ticker})
            side = str(row["contract_side"]).lower()
            quote[f"{side}_ask_cents"] = row.get("execution_price_cents")
            quote[f"{side}_bid_cents"] = row.get("bid_price_cents")
            quote["evidence_grade"] = row.get("execution_evidence_grade")
        quotes = []
        for index, row in enumerate(yes_rows):
            quote = by_contract[row["market_ticker"]]
            quote.update({"bracket_index": index, "label": row.get("market_subtitle") or row["market_ticker"]})
            quotes.append(quote)
        records.append({
            "date": day,
            "base_probabilities": [context["probabilities"][(day, row["market_ticker"])] for row in yes_rows],
            "pressure_gradient_hpa": gradients.get(day),
            "outcome_index": winners[0],
            "quotes": quotes,
            "source": "verified_v5b_development_import",
        })
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the verified historical research cache")
    parser.add_argument("--research-root", required=True)
    parser.add_argument("--output", default="data/history.jsonl")
    args = parser.parse_args(argv)
    count = convert(args.research_root, args.output)
    print(f"Imported {count} verified historical dates into {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

