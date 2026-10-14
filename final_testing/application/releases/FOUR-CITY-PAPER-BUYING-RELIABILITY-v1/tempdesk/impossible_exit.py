"""LA hard-exit overlay v1. No forecast thresholds and no exchange transport.

Inputs are a lower bound on the FINAL, settlement-rounded daily high, from a
validated settlement-compatible producer. Ordinary preliminary NWS observations
are never converted into such a certificate. A missing certificate means WAIT.
"""
from decimal import Decimal, ROUND_FLOOR
import hashlib, json

POLICY_ID = 'LA-IMPOSSIBLE-ONLY-EXIT-v1'
INTERVAL_SECONDS = 30
NS = 1_000_000_000
D = Decimal


def rules_hash(contracts):
    rules = '\n'.join(c['raw'].get('rules_primary', '') + '\n' +
                      c['raw'].get('rules_secondary', '') for c in contracts)
    return hashlib.sha256(rules.encode()).hexdigest()


def assess(position, contract, proof, market, now_ns):
    """Return a hard signal only for an authenticated, irrevocable target floor.

    A finite YES range is dead once the certified final-high lower bound exceeds
    its inclusive upper bound. NO on an unbounded upper range is dead once that
    range has been reached. NO on a finite range cannot be killed by a lower
    bound: the high may still rise out of the range.
    """
    base = {'policy_id': POLICY_ID, 'orders': [], 'paper_only': True,
            'actual_orders': 0, 'ticker': position['ticker'], 'side': position['side']}
    def wait(reason):
        return {**base, 'status': 'HOLD', 'reason': reason, 'definitely_impossible': False}
    if type(now_ns) is not int or now_ns < 0:
        raise ValueError('Actual integer receipt/processing clock required')
    if position.get('quantity', 0) <= 0:
        return wait('NO_POSITION')
    if contract.get('ticker') != position['ticker']:
        return wait('CONTRACT_MISMATCH')
    if contract.get('raw', {}).get('status') not in ('active', 'open'):
        return wait('MARKET_NOT_OPEN')
    if not proof:
        return wait('SETTLEMENT_COMPATIBLE_HIGH_FLOOR_UNAVAILABLE')
    provider = market.get('settlement_target', {}).get('provider')
    certified_floor = proof.get('schema') == 'SETTLEMENT_HIGH_FLOOR_v1' and proof.get('irreversible') is True
    published_daily = (proof.get('schema') == 'SETTLEMENT_DAILY_REPORT_v1' and
                       proof.get('official_nonpreliminary') is True and
                       proof.get('material_revision_possible') is True and
                       proof.get('source_url') == 'https://weather.com/kalshi/api/climate/primary?date='+position['day'])
    if (not (certified_floor or published_daily) or
        proof.get('validated_by_source_producer') is not True or
        proof.get('metric') != 'FINAL_SETTLEMENT_ROUNDED_DAILY_HIGH_F' or
        proof.get('provider') != provider or provider == 'UNVERIFIED' or
        proof.get('station') != 'KLAX' or proof.get('day') != position['day'] or
        proof.get('day') != market.get('day') or
        proof.get('event_ticker') != market.get('event_ticker') or
        proof.get('rules_sha256') != rules_hash(market['contracts'])):
        return wait('SETTLEMENT_HIGH_FLOOR_SCOPE_OR_AUTHORITY_UNVERIFIED')
    received, measured = proof.get('received_ns'), proof.get('measured_ns')
    if (type(received) is not int or type(measured) is not int or
        not 0 <= measured <= received <= now_ns or
        now_ns-received > 120*NS or
        received < position.get('first_fill_ns', 0)):
        return wait('SETTLEMENT_HIGH_FLOOR_CLOCK_UNSUPPORTED')
    source_hash = proof.get('source_sha256', '')
    if len(source_hash) != 64 or any(c not in '0123456789abcdef' for c in source_hash):
        return wait('SETTLEMENT_HIGH_FLOOR_SOURCE_UNVERIFIED')
    floor = proof.get('integer_lower_bound_f')
    if type(floor) is not int:
        return wait('SETTLEMENT_ROUNDING_OR_LOWER_BOUND_UNVERIFIED')
    lower, upper = contract.get('lower'), contract.get('upper')
    # Dashboard contracts use integer half-open intervals [lower, upper).
    if published_daily:
        # An official daily report states the completed day's exact high, rather
        # than an hourly lower bound. This remains subject to material revisions.
        inside = (lower is None or floor >= lower) and (upper is None or floor < upper)
        dead = (position['side'] == 'YES' and not inside) or (position['side'] == 'NO' and inside)
    else:
        dead = ((position['side'] == 'YES' and upper is not None and floor >= upper) or
                (position['side'] == 'NO' and upper is None and lower is not None and floor >= lower))
    if not dead:
        return wait('HOLD_NOT_PROVABLY_IMPOSSIBLE')
    return {**base, 'status': 'SELL_SIGNAL', 'reason': 'OFFICIAL_COMPLETED_DAILY_REPORT_INVALIDATES_POSITION' if published_daily else 'CERTIFIED_FINAL_HIGH_FLOOR_INVALIDATES_POSITION',
            'definitely_impossible': True, 'proof': proof,
            'material_revision_possible': bool(published_daily),
            'proof_sha256': hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()}


def sell_quantity(position, quote, now_ns, fraction=D(1), max_age_seconds=60):
    """Offer supported whole quantity at the best positive bid, including partial exits.

    Quantity and limit are frozen at decision. An arrival may fail; no $0 sale
    or missing-book fill is ever fabricated. Cash/fees belong to native arrival.
    """
    if not quote or quote.get('ticker') != position['ticker']:
        return None, 'BUYER_BOOK_UNAVAILABLE'
    received = quote.get('received_ns')
    if type(received) is not int or not 0 <= received <= now_ns or now_ns-received > max_age_seconds*NS:
        return None, 'BUYER_BOOK_STALE_OR_FUTURE'
    bids = quote.get(position['side'].lower()+'_bids')
    if bids is None:
        return None, 'BUYER_BOOK_UNAVAILABLE'
    if not bids:
        return None, 'NO_BUYERS'
    price, displayed = map(lambda x:D(str(x)), bids[0])
    if not price.is_finite() or not displayed.is_finite() or not 0 < price < 1 or displayed <= 0:
        return None, 'NO_POSITIVE_EXECUTABLE_BID'
    quantity = min(position['quantity'], int((displayed*fraction).to_integral_value(rounding=ROUND_FLOOR)))
    if quantity <= 0:
        return None, 'NO_SUPPORTED_WHOLE_CONTRACT_BUYERS'
    return {'quantity': quantity, 'limit_price': str(price), 'partial_position_exit': quantity < position['quantity']}, None


def market_monitor(market, observed, now_ns):
    """Publish readiness honestly. NWS preliminary values remain display-only."""
    proof = observed.get('settlement_high_floor')
    return {'policy_id': POLICY_ID, 'check_interval_seconds': INTERVAL_SECONDS,
            'checked_ns': now_ns, 'provider': market.get('settlement_target', {}).get('provider'),
            'observation_provider': observed.get('provider', 'NATIONAL_WEATHER_SERVICE'),
            'source_status': 'CERTIFICATE_AVAILABLE_FOR_POSITION_CHECK' if proof else 'WAITING_FOR_VERIFIED_SETTLEMENT_TEMPERATURE',
            'soft_forecast_exit_enabled': False, 'positions': [], 'orders_sent': 0,
            'note': 'Checks repeat every 30 seconds. Published temperatures may update less often. Selling requires buyers.'}
