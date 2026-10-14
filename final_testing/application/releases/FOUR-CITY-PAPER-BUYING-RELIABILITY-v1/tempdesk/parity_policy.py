"""Versioned current-policy history identity; never relabel earlier money."""
from copy import deepcopy
import hashlib
import json

POLICY_NAMESPACE = 'CURRENT_LIVE_PARITY_v1'
LEGACY_NAMESPACE = 'LEGACY_ARCHIVE_NATIVE_EXITS_v1'
LA_EXIT_POLICY = 'LA-IMPOSSIBLE-ONLY-EXIT-v1'
NATIVE_BINDINGS = {
    'la': 'Original F3/F6 entry kernels and legacy V8D; current completed-report-only protection overlay',
    'ny': 'Exact saved native PaperWallet.original / safe_decide / arrive / settle_due; archive capacity semantics remain explicit',
    'miami': 'Exact saved _plan and _economic_functions()[arrive]; same saved price, fee, quantity and whole-contract screens',
    'phoenix': 'Exact saved native_policy for both profiles; original static morning vector availability proxy remains historical, not an actual online calculation receipt',
}

LA_RULES = {
    'F3_V10_PHYSICAL_FORECAST_EXIT': ('V10 · completed-report protection', 'YES or NO, 17–20 UTC · up to 10% including fees · sell only after an official completed daily report invalidates the position; otherwise hold to settlement'),
    'F6_HYBRID_STAGED5': ('Hybrid · staged completed-report protection', 'YES or NO, 17–20 UTC · 2.5% + 2.5% · unchanged risk controls · completed-report protection; otherwise hold to settlement'),
    'V8D_NO18_SETTLEMENT': ('V8D · completed-report protection', 'NO at 18 UTC · up to 10% including fees · completed-report protection; otherwise hold to settlement'),
}

def identity(city, profile_id):
    value = dict(namespace=POLICY_NAMESPACE, city=city, profile_id=profile_id,
        live_exit_policy=LA_EXIT_POLICY if city == 'la' else 'UNCHANGED_NATIVE_OFFICIAL_SETTLEMENT',
        comparison_scope='CURRENT_BUY_SELL_POLICY_WITH_EXPLICIT_ARCHIVAL_TIMING_ASSUMPTIONS')
    value['policy_fingerprint'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value

def mark_result(result):
    """Transport annotation only; no quantity, cash, clock or fee changes."""
    marked = deepcopy(result)
    marked['policy_namespace'] = POLICY_NAMESPACE
    marked['historical_delivery_proven'] = False
    marked['live_rest_timing_equivalence_proven'] = False
    marked['native_rule_binding'] = NATIVE_BINDINGS[marked['city']]
    marked['parity_limitations'] = [
        'Saved forecast coefficients, native buy/sell screens and fee conventions remain pinned; this does not establish original historical first delivery.',
        'Archive decisions use registered native clock cutoffs and archival event/index availability assumptions; online preparation/publication receipts are actual wall-clock times.',
        'Historical fills are conditional separate-wallet simulations. They do not prove simultaneous shared-liquidity portfolio fills or real exchange fills.',
    ]
    for profile in marked.get('profiles', []):
        meta = identity(marked['city'], profile['profile_id'])
        profile.update(meta)
        if marked['city'] == 'la':
            profile['name'], profile['rule_summary'] = LA_RULES[profile['profile_id']]
            profile['soft_forecast_exit_enabled'] = False
            profile['exit_evidence_scope'] = 'RECORDED_COMPLETED_REPORT_ONLY; without authentic pre-close proof, hold to official settlement'
        for row in profile.get('days', []):
            row.update(policy_namespace=POLICY_NAMESPACE, policy_fingerprint=meta['policy_fingerprint'])
    return marked
