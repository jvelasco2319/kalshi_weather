"""Render the sealed V5C-V5F result as a durable Markdown report."""
from __future__ import annotations

import argparse
from pathlib import Path

from v5b.campaign import checked
from scripts.run_v5_successor_suite import POINTER


def pct(value: float) -> str:
    return f"{100 * value:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    pointer = checked(root / POINTER)
    directory = root / pointer["run_path"]
    summary = checked(directory / "summary.json")
    verification = checked(directory / "verification.json")
    records = [("Frozen V5B common-44", checked(directory / "baseline-common-44.json"))]
    records.extend((version, checked(directory / f"result-{version.lower()}.json")) for version in ("V5F", "V5D", "V5E", "V5C"))
    rows = []
    for name, result in records:
        rows.append(
            f"| {name} | {result['selected_days']} | {pct(result['aggregate_realized_net_return'])} | "
            f"{result['positive_fold_count']}/5 | {pct(result['worst_nonempty_fold_return'])} | "
            f"{pct(result['adverse_stress']['2']['aggregate_realized_net_return'])} | "
            f"{pct(result['best_day_removed_return'])} | {result['multiclass_brier']:.4f} | "
            f"{'PASS' if result['original_v5b_all_gates_passed'] else 'FAIL'} |"
        )
    v5f = records[1][1]
    brier_improvement = 1 - v5f["multiclass_brier"] / records[0][1]["multiclass_brier"]
    text = f"""# V5C-V5F successor results

The finite successor study is complete. **None of V5C-V5F passed the original V5B development gates or reached the 10% return target.** The frozen V5B policy remains the strongest method on the shared 44-date cohort.

V5F, the fixed 50/50 stack of frozen HRRR/GEFS probabilities and heavy-tail GFS/NAM/NBM probabilities, was the best successor at **{pct(v5f['aggregate_realized_net_return'])}** over {v5f['selected_days']} dates. That is positive, but it is below the 10% screen, below the unchanged V5B common-window result of {pct(records[0][1]['aggregate_realized_net_return'])}, and becomes {pct(v5f['best_day_removed_return'])} after removing its best day.

## Registered comparison

| Method | Dates | Simulated return | Positive folds | Worst fold | +2c stress | Best day removed | Brier | Original gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

All returns use total entry outlay after the exact historical fee engine. Execution grades remain quote-evidence grades rather than verified fills.

## What each successor established

- **V5C, consensus veto:** Requiring the exact-model YES probability to remain at or below 25% cut the sample to 7 dates and returned {pct(records[4][1]['aggregate_realized_net_return'])}. Simple consensus removed too many useful V5B opportunities and did not isolate a profitable subset.
- **V5D, disagreement abstention:** The 15-point probability-agreement and 6F source-range limits left only 2 dates and returned {pct(records[2][1]['aggregate_realized_net_return'])}. Exact-model disagreement was common: 29 of 44 dates exceeded the 6F source-range limit. This rule is too sparse to support a conclusion.
- **V5E, heavier tails:** The 80/20 two-sigma mixture improved the multiclass Brier score to {records[3][1]['multiclass_brier']:.4f}, but its selected trades returned {pct(records[3][1]['aggregate_realized_net_return'])}. Better global probability calibration did not translate into better trade selection.
- **V5F, fixed stack:** The stack improved Brier by {100*brier_improvement:.2f}% relative to the frozen common-window baseline and remained slightly positive under a two-cent adverse-fill stress at {pct(v5f['adverse_stress']['2']['aggregate_realized_net_return'])}. Its last two chronological folds were {pct(v5f['temporal_folds'][3]['aggregate_realized_net_return'])} and {pct(v5f['temporal_folds'][4]['aggregate_realized_net_return'])}; its best-day-removed result was negative; and its three grade-A selections lost 100%. It is a diagnostic challenger, not a promoted strategy.

## Scientific interpretation

The added GFS/NAM/NBM information improved probability accuracy when blended with the existing model, but it diluted the economic selection that made V5B positive. The result reinforces the distinction between forecast quality and trading value. V5B's economic edge on these exposed dates appears to depend on a particular subset of contracts that broad probability blending does not preserve.

The next justified study would hold V5B fixed and test whether the external ensemble can predict **when V5B fails**, using a new, untouched period or a preregistered cross-fitted meta-filter. Searching more thresholds on these same 44 dates would increase overfitting and would not provide stronger confirmation.

## Integrity and scope

- Run: `{summary['run_id']}`
- Registration SHA-256: `{summary['registration_sha256']}`
- Summary SHA-256: `{summary['self_sha256']}`
- Verification SHA-256: `{verification['self_sha256']}`
- Frozen bindings: {verification['binding_count']}
- Independent ledger/arithmetic checks: {verification['ledger_arithmetic_checks']}
- Exact replays: {len(verification['versions_reproduced'])} successors plus the common baseline
- Protected holdout read: no
- Holdout access authorized: no
- Network used during scoring: no
- Paper or live orders: zero

The failed pre-evaluation registration `v5c-v5f-development-20260928T141751955236Z` produced no successor results. It is preserved with its failure record. The completed run above is the only V5C-V5F result used here.

## Reproduction

```powershell
$env:PYTHONPATH = 'src;.'
.\\.venv\\Scripts\\python.exe scripts\\verify_v5_successor_suite.py --project-root .
.\\.venv\\Scripts\\python.exe scripts\\report_v5_successor_suite.py --project-root .
```
"""
    target = root / "reports/V5C_V5F_SUCCESSOR_RESULTS.md"
    target.write_text(text, encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

