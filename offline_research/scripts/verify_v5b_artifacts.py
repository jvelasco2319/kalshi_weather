"""Read-only campaign audit; writes only its own verification report.

Does not score new strategies or open protected labels. Arithmetic checks are
independent of the evaluator; gate/Pareto checks deliberately reuse definitions.
"""
import argparse
from collections import Counter
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v5b.campaign import (checked, digest, filehash, gate_failures, locate, offline, pareto,
                          read, records_at, seal, verify, write)
from v5b.hypotheses import fingerprint, validate_hypothesis


def audit(root, reproduce=False):
    directory = locate(root)
    registration, state = verify(root, directory)
    records = records_at(directory)
    failures, checks = [], Counter()

    def check(condition, name, detail=''):
        checks[name] += 1
        if not condition:
            failures.append({'check': name, 'detail': detail})

    def close(a, b):
        return abs(float(a) - float(b)) < 1e-9

    dates = set(read(root/'data/manifests/v5a_outcome_blind_universe.json')
                ['split']['development_dates'])
    labels = {x['climate_date']:x['reported_high_f'] for x in
              read(root/'data/development/v5a/development_labels.json')['labels']}
    check(len(records) == state['unique_candidates'], 'candidate_count')
    check(len(records) <= registration['config']['max_unique_candidates'], 'candidate_budget')
    check(state['epoch'] <= registration['config']['max_epochs'], 'epoch_budget')
    hypothesis_ids = {x['hypothesis']['id'] for x in records.values()}
    origins, colonies = Counter(), Counter()
    if reproduce:
        from v5b.evaluation import load_development, evaluate_candidate
        with offline():
            context = load_development(root)
    for key, item in records.items():
        h, result = item['hypothesis'], item['result']
        if reproduce:
            with offline():
                reproduced = evaluate_candidate(context, item['parameters'])
            check(digest(reproduced) == digest(result), 'exact_result_reproduction', key)
        validate_hypothesis(h)
        check(key == item['candidate_id'] == fingerprint(item['parameters']), 'fingerprint', key)
        check(fingerprint(h['parameters']) == key, 'hypothesis_parameters', key)
        check(set(h['parent_ids']) <= hypothesis_ids, 'parent_lineage', key)
        check(item['gate_failures'] == gate_failures(result, registration['config']), 'gate_consistency', key)
        check(item['development_screen_passed'] == (not item['gate_failures']), 'promotion_consistency', key)
        check(not item['confirmation_passed'], 'no_confirmation_claim', key)
        check(result['evaluation_partition'] == 'development', 'development_partition', key)
        trades = result['trades']
        trade_dates = [t['climate_date'] for t in trades]
        check(set(trade_dates) <= dates, 'trade_dates', key)
        check(len(set(trade_dates)) == len(trades), 'one_trade_per_day', key)
        check(result['selected_days'] == len(trades), 'selected_count', key)
        for trade in trades:
            date = trade['climate_date']
            high = labels[date]
            strike = trade['strike_type']
            yes_won = (high < trade['cap_strike'] if strike == 'less' else
                       high > trade['floor_strike'] if strike == 'greater' else
                       trade['floor_strike'] <= high <= trade['cap_strike'])
            won = yes_won if trade['contract_side'] == 'YES' else not yes_won
            price = Decimal(str(trade['entry_price_cents'])) / 100
            quantum = Decimal('.01') if date < '2026-07-07' else Decimal('.0001')
            fee = (Decimal('.07')*price*(1-price)/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum
            cost = price+fee
            check(high == trade['reported_high_f'] and won == trade['won'], 'independent_settlement', key)
            check(close(fee, trade['fee_dollars']), 'independent_fee_arithmetic', key)
            check(close(cost, trade['entry_outlay_dollars']), 'independent_trade_outlay', key)
            check(close(Decimal(int(won))-cost, trade['net_profit_dollars']), 'independent_trade_profit', key)
            check(close((Decimal(int(won))-cost)/cost, trade['realized_net_return']), 'independent_trade_return', key)
        outlay = sum((Decimal(str(t['entry_outlay_dollars'])) for t in trades), Decimal(0))
        profit = sum((Decimal(str(t['net_profit_dollars'])) for t in trades), Decimal(0))
        check(close(outlay, result['total_entry_outlay_dollars']), 'aggregate_outlay', key)
        check(close(profit, result['total_net_profit_dollars']), 'aggregate_profit', key)
        check(close(profit/outlay if outlay else -1, result['aggregate_realized_net_return']), 'aggregate_return', key)
        folds = result['temporal_folds']
        check(sum(f['selected_days'] for f in folds) == len(trades), 'fold_counts', key)
        check(sum(f['selected_days'] > 0 and f['aggregate_realized_net_return'] > 0 for f in folds)
              == result['positive_fold_count'], 'positive_folds', key)
        check(close(min((f['aggregate_realized_net_return'] for f in folds if f['selected_days']), default=-1),
                    result['worst_nonempty_fold_return']), 'worst_nonempty_fold', key)
        for cost in ['1', '2', '3']:
            check(result['adverse_stress'][cost]['selection_fixed'] and
                  result['adverse_stress'][cost]['selected_days'] == len(trades), 'fixed_stress_sample', key)
        origins[h['origin']] += 1
        colonies[h['colony']] += 1

    archive = checked(directory/'pareto-archive.json')
    check(set(archive['candidate_ids']) == set(pareto(records)), 'pareto_recomputed')
    check(set(state['frontier_ids']) == set(archive['candidate_ids']), 'state_frontier')
    populations = checked(directory/'colony-populations.json')['populations']
    for colony, ids in populations.items():
        check(len(ids) <= registration['config']['population_size'], 'population_budget', colony)
        check(all(key in records and records[key]['hypothesis']['colony'] == colony for key in ids),
              'population_ownership', colony)
    for number in range(1, state['epoch']+1):
        epoch = checked(directory/f'epoch-{number:02}.json')
        expected = {k for k, v in records.items() if v['epoch'] == number}
        check(set(epoch['evaluated']) == expected, 'epoch_ledger', str(number))
        criticism = checked(directory/f'criticism-{number:02}.json')['checks']
        check({x['candidate_id'] for x in criticism} == expected, 'criticism_coverage', str(number))
        for critique in criticism:
            check(critique['proposing_colony'] != critique['reviewing_colony'], 'deterministic_cross_colony')
        if number < state['epoch'] or number in state['reviewed_epochs']:
            packet = checked(directory/f'agent-review-{number:02}.json')
            check(len({x['colony'] for x in packet['reviews']}) >= 2, 'agent_review_diversity', str(number))
            for review in packet['reviews']:
                check(all(k in records and records[k]['hypothesis']['colony'] != review['colony']
                          for k in review['candidate_ids']), 'agent_cross_colony', str(number))
    source_snapshot = read(root/'reports/v5b-v5a-input-audit.json')
    for name, expected in source_snapshot['files'].items():
        check(filehash(root/name) == expected, 'v5a_preservation', name)
    freeze_path = directory/'strategy-freeze.json'
    if freeze_path.exists():
        frozen = checked(freeze_path)
        check(frozen['candidate_id'] in records, 'freeze_candidate')
        check(frozen['candidate_sha256'] == filehash(directory/'candidates'/f"{frozen['candidate_id']}.json"), 'freeze_candidate_binding')
        check(frozen['registration_sha256'] == filehash(directory/'registration.json'), 'freeze_registration_binding')
        check(not frozen['holdout_access_authorized'] and not frozen['ten_percent_confirmed'], 'freeze_no_confirmation')
    output = seal({'campaign_id':state['campaign_id'], 'state_status':state['status'],
                   'passed':not failures, 'checks':dict(checks), 'failures':failures,
                   'reproduced_results':reproduce,
                   'unique_candidates':len(records), 'epochs':state['epoch'],
                   'candidate_origins':dict(origins), 'colonies':dict(colonies),
                   'all_gate_candidates':sum(x['development_screen_passed'] for x in records.values()),
                   'limitations':['No independent recalculation of forecast probabilities in this verifier.',
                       'Fee arithmetic checks the registered schedule; does not independently authenticate historical fee documents.',
                       'State declarations are not an OS-level network or file-access audit.',
                       'No protected labels read. No independent confirmation performed.']})
    write(directory/'artifact-verification.json', output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', default='.')
    parser.add_argument('--reproduce', action='store_true')
    args = parser.parse_args()
    result = audit(Path(args.project_root).resolve(), args.reproduce)
    print({k:result[k] for k in ['passed','epochs','unique_candidates','all_gate_candidates','failures']})
    raise SystemExit(0 if result['passed'] else 1)
