"""Export the frozen pilot's registry and development-only scientific report."""
import csv
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v5b.campaign import checked, digest, locate, records_at, verify

root = Path(__file__).resolve().parents[1]
run = locate(root)
registration, state = verify(root, run)
assert state['status'] == 'FROZEN', 'Report requires the terminal strategy freeze'
frozen = checked(run/'strategy-freeze.json')
records = records_at(run)
leader = records[frozen['candidate_id']]
r = leader['result']
audit = checked(run/'artifact-verification.json')
assert audit['passed'] and audit['reproduced_results'] and audit['unique_candidates'] == len(records)

def signature(item):
    return digest([(t['climate_date'],t['market_ticker'],t['contract_side'],t['entry_price_cents'])
                   for t in item['result']['trades']])

passed = [x for x in records.values() if x['development_screen_passed']]
origins = Counter(x['hypothesis']['origin'] for x in records.values())
colonies = Counter(x['hypothesis']['colony'] for x in records.values())
rows = []
for key, x in sorted(records.items(), key=lambda pair:(pair[1]['epoch'], pair[0])):
    q = x['result']
    rows.append({'candidate_id':key,'hypothesis_id':x['hypothesis']['id'],
        'colony':x['hypothesis']['colony'],'origin':x['hypothesis']['origin'],
        'epoch':x['epoch'],'title':x['hypothesis']['title'],
        'selected_days':q['selected_days'],'aggregate_net_return':q['aggregate_realized_net_return'],
        'positive_folds':q['positive_fold_count'],'worst_fold':q['worst_nonempty_fold_return'],
        'failed_gates':'|'.join(x['gate_failures']),'trade_signature':signature(x)})
with (run/'candidate-registry.csv').open('w',newline='',encoding='utf-8') as f:
    w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)

folds='\n'.join(f"| {x['fold']} | {x['selected_days']} | {x['aggregate_realized_net_return']:.2%} |" for x in r['temporal_folds'])
counts='\n'.join(f'| {k} | {v} |' for k,v in sorted(colonies.items()))
provenance='\n'.join(f'| {k} | {v} |' for k,v in sorted(origins.items()))
report=f'''# V5B frozen development campaign: scientific conclusion

Campaign `{state['campaign_id']}` completed its bounded development search and froze exactly one strategy. **The 10% expected-return target is not independently confirmed.** Two specifications pass the registered development screens but select the same trades. Additional adverse-entry tests expose action and sample sensitivity. No account profits or historical fills are established.

## Scope and progression

The run evaluated **{len(records)} distinct parameter specifications**, representing **{len({signature(x) for x in records.values()})} distinct date/contract/side/price ledgers**, over 64 exposed development dates, June 1–August 3, 2026. It completed {state['epoch']} controller epochs and skipped {state['duplicates_skipped']} duplicate proposals. Epochs 7 and 8 evaluated no new strategies; they are exhaustion checks, not additional scientific evidence. Registration capped the pilot at eight epochs, 160 candidates and two hours, whichever bound applied first. It did not need to consume the full wall-clock allowance.

Actual agents proposed hypotheses and reviewed evidence between scored rounds. Deterministic evaluation and finite sensitivities were separate from that reasoning. Three subordinate research agents plus the coordinating agent worked within four available concurrent slots. Counts below describe candidate provenance, not independent replications or model calls.

| Proposing colony | Candidates |
|---|---:|
{counts}

| Proposal origin | Candidates |
|---|---:|
{provenance}

## Frozen strategy and simulated economics

Frozen candidate: `{frozen['candidate_id']}`. It buys NO contracts, uses inherited calibrated bracket probabilities, requires a modal probability gap of 0.10, ranks eligible contracts by estimated dollar profit, and retains the registered entry, spread, fee and evidence rules. The complete executable parameters are in `strategy-freeze.json`; this prose is not a substitute for them.

| Measure | Development result |
|---|---:|
| Selected dates / eligible dates | {r['selected_days']} / 64 |
| Aggregate net return on total entry outlay | {r['aggregate_realized_net_return']:.4%} |
| Total entry outlay, one contract per selected date | ${r['total_entry_outlay_dollars']:.4f} |
| Total simulated net profit | ${r['total_net_profit_dollars']:.4f} |
| Positive chronological folds | {r['positive_fold_count']} / 5 |
| Worst nonempty fold | {r['worst_nonempty_fold_return']:.4%} |
| Fixed-selection +2-cent stress | {r['adverse_stress']['2']['aggregate_realized_net_return']:.4%} |
| Best profitable day removed | {r['best_day_removed_return']:.4%} |
| Model-implied mean expected net return | {r['mean_expected_net_return']:.4%} |
| Empirical mean individual-trade return | {sum(t['realized_net_return'] for t in r['trades'])/len(r['trades']):.4%} |
| Multiclass Brier score | {r['multiclass_brier']:.6f} |

Aggregate return is total net profit divided by total entry outlay; it differs from averaging individual trade returns. The model-implied expected return uses estimated probabilities and is **not an independently measured true expectation**. Those probabilities remain overconfident in selected trades. Neither measure proves the user's desired sustainable 10% expected return.

Execution composition: **{r['execution_grade_counts']}**. B+ is weaker historical execution evidence, not verified fills. The three Grade-A selections all lost; this tiny subset is a warning and does not establish that every Grade-A strategy must lose.

| Fold | Selected dates | Aggregate net return |
|---|---:|---:|
{folds}

## What the iterations established

The inherited V5A baseline reproduced: 36 selections, +17.7007% aggregate return, four positive folds, but a -100% worst fold. Raw-probability and calibration changes did not generally repair it. Price floors and dollar-profit ranking changed a small number of contract choices; the modal-gap/NO combination eventually passed the fixed development screens. Fold 4 remains based on only two selections. The overall result is therefore sensitive to a few observations and to adaptive selection over the same development history.

There are {len(passed)} all-gate parameter specifications but only {len({signature(x) for x in passed})} distinct all-gate trade ledger. These are not independent successful discoveries. The preserved ranking chose one and no alternative is authorized for confirmation after outcomes are opened.

Fixed-selection price stress holds the chosen contracts fixed. Applying adverse entry prices before eligibility/ranking instead changes the actions: the 5-cent-floor NO variant returns +18.8320% but its worst fold is -12.1265%, failing stability. The 15-cent-floor adverse variant removes one losing Grade-B position, returns +19.2773%, but selects only 29 dates and fails the sample gate. Neither failure was waived. These are adaptive development falsifications, not independent test sets.

## Confirmation and data limits

No separately eligible untouched confirmation bundle is ready. V5A's August 4–31 holdout remains reserved, and V5B did not open it. Earlier candidate pools require exposure, weather, execution, contract and fee audits before any independent registration. An older period also tests seasonal transfer rather than future performance. `reports/v5b-confirmation-eligibility.md` records the metadata-only audit.

The feature audit identifies useful HRRR cloud/wind and GEFS summary inputs, but this frozen pilot does not execute regime models, run-to-run changes, member-level ensembles, alternate decision times, depth/partial-fill simulation or multi-leg portfolios. Adding these requires a separately frozen successor architecture; scalar mutations must not be described as having implemented them.

## Verification and known control limitations

All {len(records)} stored results reproduced exactly with the frozen evaluator under the Python network guard. The artifact verifier also checked independent Decimal fee/settlement/trade arithmetic, date boundaries, fingerprints, lineage, per-colony populations, gate results, reviews, the Pareto archive and preserved V5A bindings. See `artifact-verification.json`. Reproduction shares the original evaluator; the independent arithmetic checks are separately identified and do not validate true probabilities or actual fills.

The controls test suite reports 31 passes and one expected failure documenting a known overdue-review status bug: a waiting state checks the deadline only after review acceptance. This never permitted scoring beyond the deadline in this run. Recovery preserves budgets but does not guarantee the exact original partial-epoch proposal queue. No interruption occurred in this run. Final actual scientific review is stored separately because the frozen controller lacks a terminal-review action. These issues remain explicit hardening work, not silently repaired registered behavior.

No live feeds, credentials, purchases, paper orders or live orders were used. In-process socket blocking is not an OS-level sandbox or access audit. All claims here are offline development findings.

## Reproduction and deliverables

Use `docs/V5B_RUNBOOK.md`. Core artifacts in this directory: `registration.json`, `recovery-state.json`, `candidate-registry.csv`, `candidates/`, `agent-review-*.json`, `criticism-*.json`, `colony-populations.json`, `pareto-archive.json`, `strategy-freeze.json`, and `artifact-verification.json`. Independent review is in `reports/v5b-final-scientific-review.md`; the architecture and schema are in `docs/V5B_ARCHITECTURE.md` and `schemas/v5b-hypothesis.schema.json`.

The defensible conclusion is a development-screen-passing NO/modal selection policy with unresolved execution, calibration, sample and independent-confirmation risk. It is a research lead, not a trading authorization or a confirmed profit engine.
'''
(root/'reports/v5b-final-report.md').write_text(report,encoding='utf-8')
print(json.dumps({'campaign_id':state['campaign_id'],'unique_candidates':len(records),
    'distinct_trade_ledgers':len({signature(x) for x in records.values()}),
    'all_gate_specs':len(passed),'all_gate_trade_ledgers':len({signature(x) for x in passed}),
    'report':'reports/v5b-final-report.md'},indent=2))
