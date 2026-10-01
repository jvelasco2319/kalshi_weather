"""Create the durable exact friend-method result report."""
from __future__ import annotations

import json
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def pct(value: float) -> str:
    return f"{value:+.2%}"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    pointer = read(root / "runs/friend_method_exact_current.json")
    run_dir = root / pointer["run_path"]
    summary = read(run_dir / "summary.json")
    results = summary["results"]
    primary = results[summary["primary_result_id"]]
    single = results["kalshi_fixed_pst_f008_f031/single_best_bucket"]
    folds = primary["temporal_folds"]
    ranking_rows = summary["comparison_ranking"]
    rank_lines = []
    for index, row in enumerate(ranking_rows, 1):
        rank_lines.append(
            f"| {index} | {row['name']} | {pct(row['return'])} | {row['selected_dates']} | "
            f"{row['positive_folds']}/5 | {pct(row['worst_fold'])} | {row['status']} |"
        )
    fold_lines = [
        f"| {row['fold']} | {row['selected_dates']} | {row['selected_trades']} | "
        f"{pct(row['aggregate_realized_net_return'])} |"
        for row in folds
    ]
    grade_counts = primary["execution_grade_counts"]
    weights = primary["forecast_metrics"]["mean_weights"]
    selection = primary["selection_calibration"]
    brier_improvement = 1.0 - primary["multiclass_brier"] / primary["baseline_multiclass_brier"]
    content = f"""# Exact friend-method offline test

## Conclusion

The exact GFS/NAM/NBM implementation **failed the development screen**. The predeclared primary Kalshi-window multi-bucket strategy produced {pct(primary['aggregate_realized_net_return'])} simulated net return across {primary['selected_trades']} trades on {primary['selected_dates']} dates. It lost ${-primary['total_net_profit_dollars']:.4f} on ${primary['total_entry_outlay_dollars']:.4f} of simulated entry outlay. Only {selection['wins']} of {primary['selected_trades']} selected contracts won.

The supplied-fixture and Kalshi-corrected NBM windows produced identical daily highs on all 92 archived dates. After correcting the common-random-number bootstrap control, their predictions and results are identical. The alternate window does not rescue the method.

This is a result on repeatedly exposed development history. It is not independent confirmation, an actual trading result, or authorization to trade. The protected holdout remained unopened.

## Frozen test

- Run: `{summary['run_id']}`
- Primary: `{summary['primary_result_id']}`
- Development dates: 64; chronological warmup: 20; common scoring dates: 44
- Effective forecast sources: GFS, NAM, and NBM; GFS Seamless is a duplicate alias
- Entries: long YES, one contract per qualifying bucket, historical fee engine, maximum five-cent spread
- Execution evidence: A, B+, and B; B/B+ are not verified fills
- Four registered candidates: two NBM windows crossed with single-best and multi-bucket selection
- Independent deterministic reproduction: passed with network denied

## Exact results

| Variant | Selected dates | Trades | Simulated net return | Positive folds | Worst fold | Passed all gates |
|---|---:|---:|---:|---:|---:|---|
| Kalshi window, multi-bucket | {primary['selected_dates']} | {primary['selected_trades']} | {pct(primary['aggregate_realized_net_return'])} | {primary['positive_fold_count']}/5 | {pct(primary['worst_nonempty_fold_return'])} | No |
| Kalshi window, single best | {single['selected_dates']} | {single['selected_trades']} | {pct(single['aggregate_realized_net_return'])} | {single['positive_fold_count']}/5 | {pct(single['worst_nonempty_fold_return'])} | No |
| Fixture window, multi-bucket | {results['supplied_fixture_f007_f030/multi_bucket']['selected_dates']} | {results['supplied_fixture_f007_f030/multi_bucket']['selected_trades']} | {pct(results['supplied_fixture_f007_f030/multi_bucket']['aggregate_realized_net_return'])} | {results['supplied_fixture_f007_f030/multi_bucket']['positive_fold_count']}/5 | {pct(results['supplied_fixture_f007_f030/multi_bucket']['worst_nonempty_fold_return'])} | No |
| Fixture window, single best | {results['supplied_fixture_f007_f030/single_best_bucket']['selected_dates']} | {results['supplied_fixture_f007_f030/single_best_bucket']['selected_trades']} | {pct(results['supplied_fixture_f007_f030/single_best_bucket']['aggregate_realized_net_return'])} | {results['supplied_fixture_f007_f030/single_best_bucket']['positive_fold_count']}/5 | {pct(results['supplied_fixture_f007_f030/single_best_bucket']['worst_nonempty_fold_return'])} | No |

The primary method failed the sample, realized-return, positive-fold, worst-fold, two-cent adverse-fill, and best-day-removed gates. It passed only the estimated-return, evidence-quality, and overall multiclass-calibration checks.

## Forecast quality versus selected-trade calibration

The forecast layer was useful in aggregate but unreliable for the trades it selected.

| Measure | Result |
|---|---:|
| Daily-high MAE | {primary['forecast_metrics']['mae_location_f']:.3f}°F |
| Daily-high RMSE | {primary['forecast_metrics']['rmse_location_f']:.3f}°F |
| Mean forecast error | {primary['forecast_metrics']['mean_error_f']:+.3f}°F |
| Mean predictive sigma | {primary['forecast_metrics']['mean_sigma_f']:.3f}°F |
| Multiclass bucket Brier | {primary['multiclass_brier']:.4f} |
| Inherited reference Brier | {primary['baseline_multiclass_brier']:.4f} |
| Relative Brier improvement | {brier_improvement:.2%} |
| Average GFS weight | {weights['gfs']:.2%} |
| Average NAM weight | {weights['nam']:.2%} |
| Average NBM weight | {weights['nbm']:.2%} |

The expanding constrained fit assigned GFS zero average weight and split the effective signal almost equally between NAM and NBM. This is a fitted development result rather than proof that GFS has no future value.

For selected contracts, the mean central probability was {selection['mean_central_probability']:.2%} and the mean conservative probability was {selection['mean_conservative_probability']:.2%}, while the observed win rate was only {selection['win_rate']:.2%}. The central probability exceeded the observed win rate by {selection['central_probability_minus_win_rate']:.2%}. This selection-specific overconfidence explains why apparently large estimated edges did not become profits.

## Robustness and execution evidence

- Evidence grades: A={grade_counts['A']}, B+={grade_counts['B_PLUS']}, B={grade_counts['B']}.
- Grade-A-only fixed-selection return: {pct(primary['grade_a_sensitivity']['aggregate_realized_net_return'])} across {primary['grade_a_sensitivity']['selected_dates']} dates.
- Two-cent adverse-entry stress: {pct(primary['adverse_stress']['2']['aggregate_realized_net_return'])}.
- Return after removing the best date: {pct(primary['best_date_removed_return'])}.
- The best date contributed ${primary['best_date_net_profit_dollars']:.4f}; without it, the method deteriorated sharply.

| Fold | Selected dates | Trades | Simulated net return |
|---:|---:|---:|---:|
{chr(10).join(fold_lines)}

Three of five folds lost every dollar of simulated entry outlay. The two positive folds do not offset that instability.

## Comparison with existing methods

| Rank | Method | Simulated development return | Selected dates | Positive folds | Worst fold | Status |
|---:|---|---:|---:|---:|---:|---|
{chr(10).join(rank_lines)}

The earlier friend proxy's +12.49% was not validated by the exact requested weather models. The exact implementation ranks below the frozen V5B lead, V5A, both prior friend proxy variants, V6's diagnostic leader, and V5B-next. It should be treated as a rejected method in its current form.

## Interpretation

The weather model improved overall bucket probabilities relative to the inherited reference, but the economic selector concentrated on a subset where the probabilities were badly overstated. Better aggregate forecast skill did not identify profitable mispricing. The near-equal NAM/NBM ensemble, Gaussian tails, location-only bootstrap refits, and five-cent conservative-edge rule are the most relevant components for a future diagnostic study, but this registered test does not authorize tuning them until a favorable result appears.

The strongest remaining development lead is still the frozen V5B NO strategy at +26.83%, with V5A as a fragile positive comparator. Neither is independently confirmed. The exact friend method does not enter the viable top tier.

## Audit artifacts

- `registration.json`: frozen code, config, data, and evaluation bindings
- `forecasts-*.json`: all chronological exact-model probability forecasts
- `result-*.json`: complete trade, abstention, stress, fold, calibration, and evidence ledgers
- `summary.json`: comparison ranking and compact metrics
- `verification.json`: independent offline reproduction

The preliminary run `friend-method-exact-20260928T053424556349Z` is preserved but superseded because its window-specific bootstrap seeds allowed identical inputs to receive different Monte Carlo draws. `reports/friend-method-exact-preliminary-run-note.md` records that correction.
"""
    output = root / "reports/friend-method-exact-results.md"
    output.write_text(content, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
