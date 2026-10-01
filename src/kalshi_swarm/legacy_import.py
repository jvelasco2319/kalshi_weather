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
    from v10.evaluate_combinations import build_features, candidate_catalog, walk_forward  # type: ignore

    context = load_development(root)
    development_records = load_development_records(root)[0]
    feature_rows = build_features(root, development_records)
    config = json.loads((root / "configs" / "v10_meteorological_combinations.json").read_text(encoding="utf-8"))
    catalog = candidate_catalog(config["feature_blocks"], config["combination_rule"]["conditional_blend_weights"])
    predictions = walk_forward(feature_rows, catalog)
    raw_by_date = {record.climate_date: record for record in development_records}
    v8_by_date = {row["climate_date"]: row for row in predictions["V8_CONTROL"]}
    v10_by_date = {row["climate_date"]: row for row in predictions["V10-pressure_and_flow-W75"]}
    if set(v8_by_date) != set(v10_by_date) or len(v8_by_date) != 329:
        raise RuntimeError("V8/V10 walk-forward probability history differs")

    by_date: dict[str, dict[str, Any]] = {}
    for day in sorted(v8_by_date):
        raw = raw_by_date[day]
        v8_row = v8_by_date[day]
        v10_row = v10_by_date[day]
        by_date[day] = {
            "date": day,
            "base_probabilities": list(raw.raw_probabilities),
            "pressure_gradient_hpa": v10_row["observed_pressure_gradient_hpa"],
            "outcome_index": int(raw.outcome_position),
            "quotes": [],
            "historical_model_probabilities": {
                "V5B": list(raw.raw_probabilities),
                "V8": list(v8_row["probabilities"]),
                "V10": list(v10_row["probabilities"]),
            },
            "source": "v10_walk_forward_development_probability_only",
            "evidence_scope": "EXPOSED_DEVELOPMENT_PROBABILITY_ONLY",
        }

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
        execution_record = {
            "date": day,
            "base_probabilities": [context["probabilities"][(day, row["market_ticker"])] for row in yes_rows],
            "outcome_index": winners[0],
            "quotes": quotes,
            "source": "verified_v5b_development_import",
            "evidence_scope": "EXPOSED_DEVELOPMENT_EXECUTION",
        }
        if day in by_date:
            by_date[day]["quotes"] = quotes
            by_date[day]["evidence_scope"] = "EXPOSED_DEVELOPMENT_PROBABILITY_AND_EXECUTION"
        else:
            execution_record["unavailable_models"] = {
                "V10": "pressure feature unavailable for the execution-evidence period"
            }
            by_date[day] = execution_record
    records = [by_date[day] for day in sorted(by_date)]
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
    print(f"Imported {count} historical dates with separated probability and execution evidence into {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

