"""Write the final human-readable V6 development report from verified artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from scripts.verify_v6_artifacts import verify
from v5b.campaign import checked


def pct(value):
    return f"{100 * value:.2f}%"


def build(root):
    root = Path(root).resolve()
    verification = verify(root, reproduce=True)
    pointer = checked(root / "runs/v6_current_campaign.json")
    directory = root / pointer["run_path"]
    state = checked(directory / "recovery-state.json")
    summary = checked(directory / "development-summary.json")
    ranking = checked(directory / "method-ranking.json")
    records = {p.stem: checked(p) for p in (directory / "candidates").glob("*.json")}
    rows = []
    for entry in ranking["ranked_unique_behaviors"]:
        item = records[entry["candidate_id"]]; result = item["result"]
        rows.append("| {rank} | `{method}` | `{stage}` | `{policy}` | {days} | {ret} | {expected} | {folds}/5 | {worst} | {gates} |".format(
            rank=entry["rank"], method=item["parameters"]["method_id"], stage=item["parameters"]["stage"],
            policy=item["parameters"]["policy_id"], days=result["selected_days"],
            ret=pct(result["aggregate_realized_net_return"]), expected=pct(result["mean_expected_net_return"]),
            folds=result["positive_fold_count"], worst=pct(result["worst_nonempty_fold_return"]),
            gates="PASS" if item["development_screen_passed"] else ", ".join(item["gate_failures"])))
    group_counts = Counter(item["hypothesis"]["colony"] for item in records.values())
    blocked = checked(directory / "exhaustion-certificate.json").get("blocked", {}) if (directory / "exhaustion-certificate.json").exists() else {}
    conclusion = ("One or more methods passed every registered development gate. They remain unconfirmed because the cohort was repeatedly exposed."
                  if summary["winner_ids"] else
                  "No behaviorally distinct method passed every registered development gate within the finite catalog.")
    text = f"""# V6 multi-method tournament report

Campaign: `{state['campaign_id']}`  
Status: `{state['status']}`  
Stop reason: `{state.get('stop_reason')}`

## Conclusion

{conclusion} The 10% objective is **not independently confirmed**. No live or paper order was authorized or created.

## Scope and accounting

- Registered finite catalog: 28 specifications.
- Evaluated candidates: {len(records)}.
- Objectively precursor-blocked descendants: {len(blocked)}.
- Unique trade behaviors: {summary['unique_behaviors']}.
- All-gate ranked winners: {len(summary['winner_ids'])}.
- Common scoring cohort: 44 dates after 20 warmup dates.
- Execution evidence remains historical A/B+/B evidence; it does not prove a fill.
- Independent verifier: `{verification['status']}` with {verification['candidate_count']} reproduced candidates.

Group counts: {', '.join(f'`{key}` {value}' for key, value in sorted(group_counts.items()))}.

## Ranked distinct behaviors

| Rank | Method | Stage | Policy | Days | Realized net return | Mean expected return | Positive folds | Worst fold | Gate result |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
{chr(10).join(rows) if rows else '| — | — | — | — | 0 | — | — | — | — | No evaluated behavior |'}

## Interpretation boundary

These are simulated development results on repeatedly inspected historical dates. Ranking removes duplicate trade ledgers, but it does not make the remaining methods statistically independent. The frozen strategy artifact authorizes no holdout read and no order. A later confirmation requires a separately registered, genuinely untouched period and one fixed strategy or preregistered ensemble.

## Inspectable evidence

The campaign directory contains registration and recovery state, readiness and input hashes, the frozen reference replay, allocation ledgers, proposal queues, complete candidate ledgers, cross-group reviews, Pareto archive, behavior ranking, exhaustion certificate, strategy freeze, and artifact-verification output.
"""
    output = root / "reports/v6-final-scientific-report.md"
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(text, encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    print(build(parser.parse_args().project_root))


if __name__ == "__main__": main()
