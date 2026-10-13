"""Frozen New York inference and PAPER decisions. JSON stdin/stdout; no orders.

Only named pure definitions are compiled from pinned research sources. The
Probalytics admission function is deliberately NOT reused for public books:
actual HTTP receipt clocks retain their weaker, explicitly stated provenance.
"""
from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import types
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
D = Decimal
Z = D(0)
ONE = D(1)
NS = 10**9
NY = ZoneInfo('America/New_York')
BASE = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot')
FROZEN = BASE / 'New York'
STRICT = BASE / 'Development/New York/NY-OCT1-10-SAFETY-REPLAY-v1-20261011'
RESEARCH = BASE / 'Development/New York/NY-REALITY-SIM-v1-20261012'
WEATHER = BASE / 'Development/New York/NY-ORIGINAL-DEMO-v2-20261011'
PROFILES = ('CONSERVATIVE_P09', 'SAFE5_TARGET_ONLY')
CODE_PINS = {
    str(STRICT / 'ny_safe.py'): '986ad78f732fc78eb120bec250df9a6cec987330b2664b110b0b16c9e8c6d21d',
    str(RESEARCH / 'ny_replay_kernel_v1.py'): '3d521fafd508720bd32a20379f6c601b4e5b2ef87d2afad7c65811d8e203b2d7',
    str(RESEARCH / 'policy-registry-v1.json'): 'b828bc7650c26bc72763c7d7666b2328c74196c93017477564319d145f1b33c2',
    str(FROZEN / 'manifest.json'): '13364e0a116b40bec739db0681b71e2de3b7f1f23a4f392afad9080f34828e00',
    str(STRICT / 'vendor/ny_v10_math_v1.py'): '2a7c79b1f96855fd6b2d5277cd76aa3b9decb354576727a391b31f004d52c4de',
    str(STRICT / 'vendor/ny_v10_contract_v1.py'): '6b9f5c9c0ee86b0e4faa22e02e7a42bcfb7db92811bf9903dfe775c9d0a5931b',
}


class Blocked(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def source_path(original):
    setting = os.environ.get('TEMP_DESK_SOURCE_MAP')
    if not setting:
        return Path(original)
    map_path = None if setting.lstrip().startswith('{') else Path(setting).resolve()
    data = json.loads(setting) if map_path is None else json.loads(map_path.read_bytes())
    mapping = data.get('mapping', data.get('sources', data))
    key = str(Path(original).resolve()).replace('\\', '/').casefold()
    normalized = {str(Path(k).resolve()).replace('\\', '/').casefold(): v for k, v in mapping.items()}
    require(key in normalized, 'FROZEN_SOURCE_NOT_IN_RELEASE:' + str(original))
    value = normalized[key]
    target = Path(value['path'] if isinstance(value, dict) else value)
    if not target.is_absolute() and map_path is not None:
        target = map_path.parent/target
    target = target.resolve()
    if isinstance(value, dict) and value.get('sha256'):
        require(sha256(target.read_bytes()).hexdigest() == value['sha256'], 'FROZEN_MAPPED_SOURCE_CHANGED')
    return target


def source_bytes(original, expected=None):
    raw = source_path(original).read_bytes()
    pin = expected or CODE_PINS.get(str(original))
    if pin:
        require(sha256(raw).hexdigest() == pin, 'FROZEN_SOURCE_CHANGED:' + str(original))
    return raw


def read_source(original, expected=None):
    return json.loads(source_bytes(original, expected))


def dependencies(request=None):
    manifest = read_source(FROZEN / 'manifest.json')
    paths = list(CODE_PINS)
    paths += [str(FROZEN / 'nyc-profile.json')]
    for month in ('2026-09', '2026-10'):
        checkpoint = manifest['checkpoints'][month]
        paths += [str(FROZEN / checkpoint[k]) for k in ('state', 'selection', 'trader_settings')]
    paths += [str(WEATHER / 'weather.py')]
    paths += [str(WEATHER / 'vendor' / p) for p in (
        'shared_weather_v1.py', 'shared_weather_v2.py', 'collect_nyc_weather_v1.py',
        'extract_nyc_points_v2.py', 'city-source-profiles-v1.json', 'city-grid-geometry-v1.json')]
    pins = {p: sha256(source_bytes(Path(p))).hexdigest() for p in paths}
    return {'dependencies': paths, 'pins': pins, 'no_training_or_transport_dependencies': True}


def clock(day, hour=10, minute=0):
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=NY).timestamp()) * NS


def describe(request=None):
    return {'city': 'New York', 'station': 'KNYC', 'series': 'KXHIGHNY',
        'profiles': [{'profile_id': 'CONSERVATIVE_P09', 'name': 'Conservative P09',
            'daily_fraction': '0.10', 'displayed_depth_fraction': '1',
            'entry_local': '10:00', 'exit': 'OFFICIAL_SETTLEMENT'},
            {'profile_id': 'SAFE5_TARGET_ONLY', 'name': 'Targeted safety',
            'daily_fraction': '0.05', 'stage_fraction': '0.025',
            'displayed_depth_fraction': '0.50', 'entry_local': '10:00-13:00',
            'exit': 'OFFICIAL_SETTLEMENT', 'closed_drawdown_stop': '0.10'}],
        'timezone': 'America/New_York', 'saved_live_month': '2026-10',
        'forecast_snapshot_local': '10:00', 'no_automatic_new_month_checkpoint': True,
        'paper_only': True, 'orders_sent': 0,
        'qualifications': ['Public books without event timestamps use actual receipt freshness only.',
            'Temperature probabilities retain the saved whole-degree physical-day hypothesis.',
            'Official binary outcome and cash availability are supplied by the common paper ledger.',
            'Late/missing before-10am weather cannot become a timely historical issuance.']}


def selected_definitions(path, names, env):
    tree = ast.parse(source_bytes(path), filename=str(path))
    found = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    require({n.name for n in found} == set(names), 'FROZEN_DEFINITION_MISSING')
    exec(compile(ast.Module(body=found, type_ignores=[]), str(path), 'exec'), env)
    return env


def frozen_model():
    env = {'D': D, 'ZERO': Z, 'ONE': ONE, 'NS': NS, 'NY': NY, 'ROOT': STRICT,
        'Path': Path, 'json': json, 'math': math, 'require': require,
        'number': lambda x: numeric(x), 'clock': clock}
    tree = ast.parse(source_bytes(STRICT / 'ny_safe.py'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'FrozenNY')
    cls = deepcopy(cls)
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in (
        'conservative_no', 'probabilities_at_location')]
    exec(compile(ast.Module(body=[cls], type_ignores=[]), '<pinned FrozenNY math>', 'exec'), env)

    class Model(env['FrozenNY']):
        def __init__(self):
            self.manifest = read_source(FROZEN / 'manifest.json')
            self.cache = {}

        def load(self, month):
            require(month in ('2026-09', '2026-10'), 'EXPLICIT_NEW_MONTH_CHECKPOINT_REQUIRED')
            if month not in self.cache:
                c = self.manifest['checkpoints'][month]
                data = {k: read_source(FROZEN / c[k], self.manifest['files'][c[k]]['sha256'])
                    for k in ('state', 'selection', 'trader_settings')}
                require(data['state']['self_sha256'] == c['state_self_sha256']
                    and data['selection']['branch'] == c['selected_branch'], 'FROZEN_CHECKPOINT_MISMATCH')
                self.cache[month] = {'checkpoint': c, 'state': data['state'],
                    'settings': data['trader_settings'],
                    'calibration': data['trader_settings']['probability_adjustments']}
            return self.cache[month]
    return Model()


def numeric(value):
    require(not isinstance(value, bool), 'BOOLEAN_NUMBER')
    value = D(str(value))
    require(value.is_finite(), 'NONFINITE_NUMBER')
    return value


def contract_partition(rows, day):
    require(len(rows) == 6 and len({c['ticker'] for c in rows}) == 6, 'SIX_CONTRACT_UNIVERSE_REQUIRED')
    expected = 'KXHIGHNY-' + date.fromisoformat(day).strftime('%y%b%d').upper()
    out = []
    for j, c in enumerate(rows):
        require(c['ticker'].startswith(expected + '-'), 'WRONG_DATED_NY_CONTRACT')
        lo, hi = c.get('lower'), c.get('upper')
        lo = None if lo is None else numeric(lo)
        hi = None if hi is None else numeric(hi)
        # Common UI bounds are half-open; legacy test fixtures carry explicit inclusivity.
        low = None if lo is None else int(lo.to_integral_value(rounding=ROUND_CEILING)) if c.get('lower_inclusive', True) else int(lo.to_integral_value(rounding=ROUND_FLOOR)) + 1
        high = None if hi is None else int(hi.to_integral_value(rounding=ROUND_FLOOR)) if c.get('upper_inclusive', False) else int(hi.to_integral_value(rounding=ROUND_CEILING)) - 1
        require(low is None or high is None or low <= high, 'EMPTY_INTEGER_CONTRACT_CLASS')
        out.append({'ticker': c['ticker'], 'lower': low, 'upper': high,
            'lower_inclusive': True, 'upper_inclusive': True, 'ordinal_index': j})
    require(out[0]['lower'] is None and out[-1]['upper'] is None, 'OPEN_TAILS_REQUIRED')
    require(all(a['upper'] is not None and b['lower'] is not None and a['upper'] + 1 == b['lower']
        for a, b in zip(out, out[1:])), 'COMPLETE_INTEGER_PARTITION_REQUIRED')
    return out


def weather_math():
    env = {'date': date, 'datetime': datetime, 'timedelta': timedelta, 'timezone': timezone,
        'Decimal': D, 'ROUND_CEILING': ROUND_CEILING, 'ROUND_FLOOR': ROUND_FLOOR,
        'sha256': sha256, 'json': json, 'math': math, 're': __import__('re'), 'Path': Path,
        'UTC': timezone.utc, 'HOUR_NS': 3600*NS, 'MINUTE_NS': 60*NS,
        'LABEL_KEYS': {'high_f', 'reported_high_f', 'official_high_f', 'outcome', 'outcome_position',
            'ordinal_label', 'payout', 'winning_market_ticker', 'expiration_value'}}
    selected_definitions(STRICT/'vendor/ny_v10_contract_v1.py',
        ('canonical', 'seal', 'verify_seal', 'ns', 'day_clock', 'finite', 'no_labels', 'validate_frame'), env)
    selected_definitions(STRICT/'vendor/ny_v10_math_v1.py', ('feature_map',), env)
    return env


def native_frame(day, weather):
    base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    start = base+timedelta(hours=5); end = start+timedelta(hours=24)
    ns = weather_math()['ns']; anchor = clock(day)
    return {'city_id': 'KXHIGHNY', 'station_id': 'KNYC', 'climate_date': day,
        'city_context': {'series_ticker': 'KXHIGHNY', 'physical_station_id': 'KNYC',
            'climate_day_start_utc': start.isoformat(), 'climate_day_end_utc': end.isoformat(),
            'decision_at_utc': datetime.fromtimestamp(anchor/NS, timezone.utc).isoformat()},
        'climate_start_ns': ns(start.isoformat()), 'climate_end_ns': ns(end.isoformat()),
        'decision_ns': anchor, 'feature_cutoff_ns': anchor, 'required_field_count': 68,
        'fields': deepcopy(weather['fields']), 'observations': [], 'expected_temperature_valid_ns': {
            'hrrr': list(range(ns(start.isoformat()), ns(end.isoformat()), 3600*NS)),
            'gefs_avg': [ns(base.isoformat())+lead*3600*NS for lead in range(3, 31, 3)],
            'gefs_spr': [ns(base.isoformat())+lead*3600*NS for lead in range(3, 31, 3)]}}


def forecast(request):
    day = request['day']
    historical = request.get('historical_parity') is True
    require(day[:7] == '2026-10' or historical and day[:7] == '2026-09', 'EXPLICIT_NEW_MONTH_CHECKPOINT_REQUIRED')
    at = int(request['at_ns']); anchor = clock(day)
    require(historical or day == datetime.now(NY).date().isoformat(), 'CURRENT_DAY_CAPTURE_REQUIRED')
    preparing=request.get('purpose')=='prepare_publication'
    require(historical or at >= anchor or preparing and anchor-900*NS<=at<anchor, 'FIXED_10AM_FORECAST_NOT_YET_DUE')
    model = frozen_model(); saved = model.load(day[:7]); contracts = contract_partition(request['contracts'], day)
    weather = request.get('weather', request.get('native_weather', {}))
    frame = deepcopy(request.get('frame', weather.get('frame')))
    if frame is None and weather.get('fields'):
        frame = native_frame(day, weather)
    require(isinstance(frame, dict), 'NATIVE_WEATHER_FRAME_REQUIRED')
    require(frame['station_id'] == 'KNYC' and frame['city_id'] == 'KXHIGHNY'
        and frame['climate_date'] == day and frame['feature_cutoff_ns'] == anchor, 'NATIVE_WEATHER_IDENTITY_OR_CLOCK')
    math_env = weather_math(); math_env['no_labels'](frame)
    if not historical:
        require(weather.get('current_day_capture') is True and weather.get('complete_native_capture') is True
            and weather.get('received_before_original_10am') is True, 'TIMELY_COMPLETE_NATIVE_CAPTURE_REQUIRED')
        require(len(frame['fields']) == 68, 'ORIGINAL_68_FIELD_CAPTURE_REQUIRED')
        for field in frame['fields']:
            sr = field.get('source_ref', {})
            require(sr.get('delivery_basis') == 'REAL_LOCAL_HTTP_RECEIPT'
                and 0 <= int(sr.get('received_ns', -1)) <= anchor, 'ACTUAL_BEFORE_10AM_WEATHER_RECEIPT_REQUIRED')
            native, point = sr['native_field'], sr['point_packet']
            for key, hashkey in (('source_path', 'source_sha256'), ('receipt_path', 'receipt_sha256')):
                require(sha256(Path(native[key]).read_bytes()).hexdigest() == native[hashkey], 'NATIVE_WEATHER_BYTES_CHANGED')
            receipt = json.loads(Path(native['receipt_path']).read_bytes())
            index_path = Path(str(native['index_binding']['path'])+'.json')
            require(sha256(index_path.read_bytes()).hexdigest() == native['index_binding']['receipt_sha256'],
                'NATIVE_INDEX_RECEIPT_CHANGED')
            index = json.loads(index_path.read_bytes())
            received = max(math_env['ns'](receipt['retrieved_at_utc']), math_env['ns'](index['retrieved_at_utc']))
            require(sr['received_ns'] == received and field['available_ns'] ==
                max(received, math_env['ns'](native['information_available_utc'])), 'WEATHER_RECEIPT_TIME_REWRAPPED')
            require(all(field[k] == native[k] for k in ('object_id', 'field_id', 'model', 'member_id', 'lead_hours'))
                and field['issue_ns'] == math_env['ns'](native['native_metadata']['nominal_issue_utc'])
                and field['valid_ns'] == math_env['ns'](native['native_metadata']['valid_time_utc']),
                'NATIVE_WEATHER_FIELD_IDENTITY_REWRAPPED')
            require(sha256(Path(point['path']).read_bytes()).hexdigest() == point['sha256'], 'NATIVE_POINT_BYTES_CHANGED')
            point_packet = json.loads(Path(point['path']).read_bytes())
            require(point_packet['source'] == native, 'POINT_NATIVE_BINDING_CHANGED')
            packet = point_packet['points'][0]
            require(packet['station_id'] == 'KNYC' and packet['target_latitude'] == 40.7789
                and packet['target_longitude'] == -73.9692, 'WRONG_NATIVE_GRID_POINT')
            require(all(field[k] == packet[k] for k in ('value', 'units', 'raw_value', 'raw_units', 'is_missing')), 'NATIVE_WEATHER_VALUE_REWRAPPED')
    features = math_env['feature_map'](frame); raw = saved['state']['raw']
    require(set(features) == set(raw['feature_names']), 'EXACT_11_TEMPERATURE_FEATURES_REQUIRED')
    vals = [features[k] for k in raw['feature_names']]
    mu = raw['intercept'] + sum((vals[i]-mean)/scale*coef for i, mean, scale, coef in
        zip(raw['retained_columns'], raw['means'], raw['scales'], raw['coefficients'], strict=True))
    probabilities = model.probabilities_at_location(day[:7], mu, contracts)
    ordered = sorted(probabilities.values(), reverse=True)
    result = {'date': day, 'contracts': contracts, 'station': 'KNYC', 'series': 'KXHIGHNY',
        'forecast_event_ns': at, 'feature_cutoff_ns': anchor, 'forecast_expires_ns': anchor+7200*NS,
        'probabilities_yes': probabilities, 'gap': ordered[0]-ordered[1], 'location_f': mu,
        'features': features, 'fits': 0, 'branch': saved['checkpoint']['selected_branch'],
        'state_self_sha256': saved['checkpoint']['state_self_sha256'],
        'model_id': saved['checkpoint']['model_id'],
        'timely_native_capture': not historical and anchor <= at <= anchor+NS,
        'prepared': preparing and at<anchor,'scheduled_publication_ns':anchor if preparing else None,
        'historical_parity_only': historical, 'paper_only': True,
        'status': 'NWS_PHYSICAL_FORECAST_ONLY', 'trading_target_admitted': False,
        'source_frame_sha256': sha256(canonical(frame)).hexdigest(),
        'settlement_mapping': 'SAVED_WHOLE_DEGREE_PHYSICAL_DAY_HYPOTHESIS'}
    if request.get('target_binding'):
        from tempdesk.policy_receipts import verify
        proof=verify(request['target_binding'],'ny',day,request.get('mapping_contracts',request['contracts']),at)
        result.update(trade_eligible=not preparing and anchor<=at<anchor+NS,trading_target_admitted=True,status='PAPER_DAILY_CLI_PROXY_FORECAST',paper_target_mapping=request['target_binding'],target_equivalence_guaranteed=False,forecast_scope=proof['hypothesis'])
    return {'forecast': result, 'forecasts': {p: deepcopy(result) for p in PROFILES}, 'fits': 0,'prepared':preparing}


def target_admission(request):
    if request.get('historical_parity') is True:
        return
    binding = request.get('target_binding')
    if binding and request.get('paper_mapping_mode')=='VERIFIED_DAILY_CLI_PROXY_v1':
        from tempdesk.policy_receipts import verify
        verify(binding,'ny',request['day'],request.get('mapping_contracts',request['contracts']),int(request['at_ns']))
        return
    require(isinstance(binding, dict) and binding.get('path') and binding.get('sha256'),
        'TWC_PHYSICAL_TO_SETTLEMENT_BINDING_UNPROVEN')
    raw = Path(binding['path']).read_bytes()
    require(sha256(raw).hexdigest() == binding['sha256'], 'TARGET_BINDING_RECEIPT_CHANGED')
    proof = json.loads(raw)
    require(proof.get('validation') == 'INDEPENDENTLY_VERIFIED'
        and proof.get('date') == request['day'] and proof.get('station') == 'KNYC'
        and proof.get('series') == 'KXHIGHNY'
        and proof.get('physical_to_settlement_equivalence_proven') is True
        and proof.get('received_ns', int(request['at_ns'])+1) <= int(request['at_ns']),
        'TWC_PHYSICAL_TO_SETTLEMENT_BINDING_UNPROVEN')


def public_book(packet, ticker, at, *, safety=False):
    require(isinstance(packet, dict), 'PUBLIC_BOOK_MISSING')
    received = int(packet.get('received_ns', -1))
    require(0 <= at-received <= 5*NS, 'STALE_OR_FUTURE_PUBLIC_RECEIPT')
    basis = packet.get('source_age_clock')
    require(basis in ('RECEIPT_CLOCK', 'SERVER_EVENT_CLOCK', 'EVENT_CLOCK'), 'PUBLIC_AGE_CLOCK_UNLABELED')
    event = packet.get('server_time_ns')
    if basis != 'RECEIPT_CLOCK':
        require(type(event) is int and 0 <= at-event <= 5*NS and event <= received, 'STALE_OR_FUTURE_PUBLIC_EVENT')
    else:
        event = received  # Actual new receipt, explicitly not a manufactured exchange event.
    levels = {}
    for action in ('bids', 'asks'):
        ladder = []
        for row in packet.get(action, []):
            price, size = (row.get('price'), row.get('size', row.get('quantity'))) if isinstance(row, dict) else row
            ladder.append((numeric(price), numeric(size)))
        require(ladder and all(Z < p < ONE and q > Z for p, q in ladder), 'EMPTY_OR_INVALID_PUBLIC_DEPTH')
        prices = [p for p, q in ladder]
        require(len(set(prices)) == len(prices) and prices == sorted(prices, reverse=action == 'bids'), 'PUBLIC_LADDER_ORDER_OR_DUPLICATE')
        levels[action] = ladder
    bid, ask = levels['bids'][0][0], levels['asks'][0][0]
    require(bid <= ask and (not safety or bid < ask), 'CROSSED_OR_LOCKED_PUBLIC_BOOK')
    require(bid == numeric(packet['bid']) and ask == numeric(packet['ask']), 'PUBLIC_TOP_LADDER_MISMATCH')
    identity = sha256(canonical(packet)).hexdigest()
    fingerprint = sha256(canonical({k: [[str(p.normalize()), str(q.normalize())] for p, q in levels[k]]
        for k in ('bids', 'asks')})).hexdigest()
    return {'ticker': ticker, 'asks': levels['asks'], 'bid': bid, 'event_ns': event,
        'fingerprint': fingerprint, 'identity': identity, 'indexed_after_assumed_clock': None,
        'native_states': ['PUBLIC_READ_ONLY_' + basis], 'source_ref': packet.get('source_ref'),
        'source_age_clock': basis, 'exchange_event_freshness_proven': basis != 'RECEIPT_CLOCK'}


def wallet_class():
    env = {'D': D, 'ZERO': Z, 'Z': Z, 'ONE': ONE, 'NS': NS, 'NY': NY, 'math': math,
        'number': numeric, 'require': require, 'Blocked': Blocked, 'ROUND_CEILING': ROUND_CEILING,
        'PENDING': {'PENDING', 'UNKNOWN_EXECUTION'},
        'floor': lambda x: int(x.to_integral_value(rounding=ROUND_FLOOR)), 'admit': public_book,
        'ceil_cent': lambda p: int((p*100).to_integral_value(rounding=ROUND_CEILING)),
        'fee': lambda q, p: (D('.07')*q*p*(ONE-p)/D('.01')).to_integral_value(rounding=ROUND_CEILING)*D('.01')}
    env['strict'] = types.SimpleNamespace(SafetyError=Blocked, PENDING={'PENDING', 'UNKNOWN_EXECUTION'})
    tree = ast.parse(source_bytes(STRICT/'ny_safe.py'))
    native = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'OfflineTrader')
    methods = [deepcopy(n) for n in native.body if isinstance(n, ast.FunctionDef) and n.name in (
        'reserved', 'exposure', 'remaining_budget', 'risk', 'confirmed')]
    kernel = ast.parse(source_bytes(RESEARCH/'ny_replay_kernel_v1.py'))
    research = next(n for n in kernel.body if isinstance(n, ast.ClassDef) and n.name == 'Wallet')
    methods += [deepcopy(n) for n in research.body if isinstance(n, ast.FunctionDef) and n.name in ('fee', 'original', 'safe_decide')]
    # Keep native original selection/allocation/reservation byte-for-byte AST, but
    # separate its immediate historical arrival loop from prospective decisions.
    original = next(n for n in methods if n.name == 'original')
    class RemoveHistoricalArrival(ast.NodeTransformer):
        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'arrive':
                return ast.Pass()
            return self.generic_visit(node)
    RemoveHistoricalArrival().visit(original)
    cls = ast.ClassDef(name='NativeDecisions', bases=[], keywords=[], body=methods, decorator_list=[])
    tree = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    exec(compile(tree, '<pinned native NY decision kernels>', 'exec'), env)

    class PaperWallet(env['NativeDecisions']):
        def __init__(self, request):
            self.name = request['profile_id']; require(self.name in PROFILES, 'UNKNOWN_NY_PROFILE')
            self.cfg = read_source(RESEARCH/'policy-registry-v1.json')['profiles']['SAFE5_TARGET_ONLY']
            self.depth = D('0.50') if self.name.startswith('SAFE') else ONE
            self.model = frozen_model(); self.reasons = Counter(); self.trace = []
            supplied = request.get('policy_state', request.get('state', {})) or {}
            if 'policy_state' in supplied:
                supplied = supplied['policy_state']
            self.s = deepcopy(supplied)
            if not self.s or 'orders' not in self.s:
                self.s = {'initial': '100.00', 'cash': '100.00', 'realized': '0', 'peak_closed': '100.00',
                    'paused': False, 'orders': {}, 'positions': {}, 'opening': {}, 'daily_spend': {},
                    'daily_net': {}, 'confirmations': {}, 'profile_id': self.name}
            require(self.s.get('profile_id', self.name) == self.name, 'POLICY_STATE_PROFILE_MISMATCH')
            self.s['profile_id'] = self.name
            require(numeric(self.s['initial']) == D('100'), 'INDEPENDENT_100_WALLET_REQUIRED')
            self.orders = list(self.s['orders'].values())
            if 'available_cash' in request:
                self.s['cash'] = str(numeric(request['available_cash']) + self.reserved(self.s))
            if 'opening_cash' in request:
                self.s['opening'].setdefault(request['day'], str(numeric(request['opening_cash'])))
            self.conditional = any(o['status'] == 'UNKNOWN_EXECUTION' for o in self.orders)

        def record(self, action, at, **row):
            self.trace.append({'action': action, 'at_ns': at, **row})

        def reserve(self, order):
            oid = f'{self.name}-{order["date"]}-{len(self.orders)+1:04d}'
            order.update(order_id=oid, method=self.name, status='PENDING')
            self.s['orders'][oid] = order; self.orders.append(order)
            return order
    return PaperWallet


def validate_forecast(request):
    f = deepcopy(request['forecast']); day = request['day']; at = int(request['at_ns'])
    require(f['date'] == day and f['feature_cutoff_ns'] == clock(day), 'FORECAST_DATE_OR_SNAPSHOT_MISMATCH')
    saved = frozen_model().load(day[:7]); state = saved['checkpoint']['state_self_sha256']
    require(f.get('state_self_sha256', f.get('fitted_state_self_sha256')) == state, 'FORECAST_STATE_PIN_MISMATCH')
    require(request.get('historical_parity') is True or f.get('timely_native_capture') is True
        and f.get('historical_parity_only') is False, 'RETROSPECTIVE_FORECAST_CANNOT_TRADE')
    require(f['forecast_event_ns'] <= at <= f['forecast_expires_ns'], 'FORECAST_EXPIRED_OR_FUTURE')
    contracts = contract_partition(request.get('contracts', f['contracts']), day)
    require(set(f['probabilities_yes']) == {c['ticker'] for c in contracts}, 'COMPLETE_FORECAST_UNIVERSE_REQUIRED')
    p = [numeric(x) for x in f['probabilities_yes'].values()]
    require(all(Z <= x <= ONE for x in p) and abs(sum(p)-ONE) <= D('1e-10'), 'INVALID_PROBABILITY_VECTOR')
    f['contracts'] = contracts
    return f


def common_order(order):
    return {'ticker': order['ticker'], 'side': 'NO', 'quantity': order['quantity'],
        'limit_price': order['limit'], 'reserved_outlay': order['reservation'],
        'fee_rate': '.07', 'fee_quantum': '.01' if order['method'].startswith('SAFE') else '.0001',
        'fee_rounding': 'TOTAL_OUTLAY_CEILING',
        'arrival_delay_seconds': 5, 'native_order': deepcopy(order)}


def decide(request):
    target_admission(request)
    at, day = int(request['at_ns']), request['day']; f = validate_forecast(request)
    wallet = wallet_class()(request); before = len(wallet.orders)
    if wallet.name == 'CONSERVATIVE_P09':
        require(clock(day) <= at <= clock(day)+NS, 'FIXED_10AM_ENTRY_SLOT_MISSED')
        require(not any(o['date'] == day for o in wallet.orders) and not request.get('submitted'), 'DAILY_INITIAL_ALREADY_USED')
        f['forecast_event_ns'] = at
        wallet.original(f, {str(at): request.get('quotes', {})})
    else:
        require(clock(day) <= at <= clock(day, 13), 'OUTSIDE_SAFE_ENTRY_WINDOW')
        stage = int(request.get('stage', 2 if any(o['date'] == day for o in wallet.orders) else 1))
        require(stage in (1, 2), 'INVALID_NATIVE_STAGE')
        wallet.safe_decide(f, request.get('quotes', {}), at, stage)
    orders = [common_order(o) for o in wallet.orders[before:]]
    return {'orders': orders, 'policy_state': wallet.s, 'reasons': dict(wallet.reasons),
        'status': 'INTENT' if orders else 'NO_TRADE', 'paper_only': True,
        'exchange_event_freshness_proven': all(q.get('source_age_clock') != 'RECEIPT_CLOCK'
            for q in request.get('quotes', {}).values()), 'orders_sent': 0}


def arrival(request):
    wallet = wallet_class()(request); at = int(request['at_ns'])
    common = request['frozen_order']; order = common.get('native_order', common)
    require(order['method'] == wallet.name and at >= order['arrival_ns'], 'ORDER_OR_ARRIVAL_CLOCK_MISMATCH')
    require(order['order_id'] in wallet.s['orders'], 'DURABLE_NATIVE_ORDER_RESERVATION_REQUIRED')
    o = wallet.s['orders'][order['order_id']]
    require(o['status'] in ('PENDING', 'UNKNOWN_EXECUTION'), 'ORDER_ALREADY_RECONCILED')
    require(canonical({k: o[k] for k in ('ticker', 'quantity', 'limit', 'reservation', 'arrival_ns')}) ==
        canonical({k: order[k] for k in ('ticker', 'quantity', 'limit', 'reservation', 'arrival_ns')}), 'FROZEN_ORDER_CHANGED')
    try:
        book = public_book(request.get('quote', request.get('quotes', {}).get(o['ticker'])), o['ticker'], at,
            safety=wallet.name.startswith('SAFE'))
        require(at <= o['arrival_ns']+NS, 'ARRIVAL_FIXED_SLOT_MISSED')
    except Blocked as e:
        o.update(status='UNKNOWN_EXECUTION', reason=str(e))
        return {'status': 'UNKNOWN', 'quantity': 0, 'principal': '0', 'fee': '0', 'fills': [],
            'reservation_retained': o['reservation'], 'reason': str(e), 'policy_state': wallet.s, 'paper_only': True}
    try:
        quantity = o['quantity']; fills = []
        if wallet.name.startswith('SAFE'):
            require(at <= request['forecast']['forecast_expires_ns'], 'FORECAST_EXPIRED_AT_ARRIVAL')
            need = quantity
            for price, size in book['asks']:
                if price > numeric(o['limit']):
                    break
                q = min(need, int((size*wallet.depth).to_integral_value(rounding=ROUND_FLOOR)))
                if q:
                    fills.append((q, price)); need -= q
                if not need:
                    break
            require(not need, 'ARRIVAL_DEPTH_OR_LIMIT_INSUFFICIENT')
        else:
            price, size = book['asks'][0]
            require(price <= numeric(o['limit']), 'ARRIVAL_ASK_ABOVE_FROZEN_LIMIT')
            require(int(size.to_integral_value(rounding=ROUND_FLOOR)) >= quantity, 'ARRIVAL_DEPTH_BELOW_FIXED_QUANTITY')
            fills = [(quantity, price)]
        principal = sum((q*p for q, p in fills), Z)
        fees = sum((wallet.fee(q, p) for q, p in fills), Z); amount = principal+fees
        require(amount <= numeric(o['reservation']), 'ARRIVAL_EXCEEDS_FROZEN_RESERVATION')
        conservative_cost = principal + D('.02')*quantity if wallet.name.startswith('SAFE') else amount
        expected = quantity*numeric(o['probability'])-conservative_cost
        if wallet.name.startswith('SAFE'):
            require(expected >= numeric(wallet.cfg['minimum_expected_dollars']), 'ARRIVAL_EDGE_OR_RESERVATION_FAILED')
        else:
            require(expected/amount >= D('.10'), 'ARRIVAL_EXPECTED_RETURN_BELOW_HURDLE')
    except Blocked as e:
        o.update(status='ASSUMED_TERMINAL_NO_FILL', reason=str(e), amount='0', filled_quantity=0)
        return {'status': 'CANCELLED', 'quantity': 0, 'principal': '0', 'fee': '0', 'fills': [],
            'reason': str(e), 'policy_state': wallet.s, 'paper_only': True}
    o.update(status='PAPER_ASSUMED_FILL', amount=str(amount), principal=str(principal), fees=str(fees),
        filled_quantity=quantity, fills=[{'quantity': q, 'price': str(p), 'fee': str(wallet.fee(q, p))} for q, p in fills])
    wallet.s['cash'] = str(numeric(wallet.s['cash'])-amount)
    day = o['date']; wallet.s['daily_spend'][day] = str(numeric(wallet.s['daily_spend'].get(day, '0'))+amount)
    position = wallet.s['positions'].setdefault(o['ticker'], {'ticker': o['ticker'], 'date': day,
        'quantity': 0, 'basis': '0', 'initial_bid': o['initial_bid'], 'first_fill_ns': at})
    position['quantity'] += quantity; position['basis'] = str(numeric(position['basis'])+amount)
    return {'status': 'FILLED', 'quantity': quantity, 'filled_quantity': quantity,
        'principal': str(principal), 'fee': str(fees), 'filled_price': str(principal/quantity),
        'fills': deepcopy(o['fills']),
        'policy_state': wallet.s, 'paper_only': True, 'actual_exchange_fill_proven': False,
        'source_age_clock': book['source_age_clock']}


def settle(request):
    """Apply a public official binary outcome to PAPER state, at cash release.

    Do not pass already-updated common available_cash: this action starts from
    the persisted pre-settlement policy state and returns the exact cash delta.
    Public settlement is not an authenticated account-credit receipt.
    """
    clean = {k: v for k, v in request.items() if k not in ('available_cash', 'opening_cash')}
    wallet = wallet_class()(clean); at = int(request['at_ns'])
    resolution = request['settlement']; ticker = resolution['ticker']
    position = wallet.s['positions'].get(ticker)
    require(position is not None, 'UNKNOWN_POLICY_POSITION')
    if not position['quantity']:
        return {'status': 'ALREADY_SETTLED', 'payout': '0', 'net': '0', 'policy_state': wallet.s}
    binding = resolution['source_receipt']; raw = Path(binding['path']).read_bytes()
    require(sha256(raw).hexdigest() == binding['sha256'], 'OFFICIAL_RESULT_RECEIPT_CHANGED')
    envelope = json.loads(raw); body = envelope.get('response', envelope.get('data', envelope))
    if isinstance(body, str):
        body = json.loads(body)
    markets = body.get('markets', [body['market']] if 'market' in body else [])
    matches = [m for m in markets if m['ticker'] == ticker]
    require(len(matches) == 1, 'DATED_OFFICIAL_BINARY_MARKET_REQUIRED')
    market = matches[0]
    require(market['status'] == 'finalized' and market['result'] in ('yes', 'no'), 'OFFICIAL_BINARY_RESULT_NOT_FINAL')
    require(market['event_ticker'] == 'KXHIGHNY-'+date.fromisoformat(position['date']).strftime('%y%b%d').upper(), 'OFFICIAL_OUTCOME_EVENT_MISMATCH')
    official_ns = weather_math()['ns'](market['settlement_ts'])
    available = int(resolution['available_ns']); received = int(binding['received_ns'])
    require(official_ns <= available <= at and received <= available and received >= 0,
        'OFFICIAL_OUTCOME_OR_PAPER_CASH_NOT_YET_AVAILABLE')
    yes = int(market['result'] == 'yes')
    require(numeric(market['settlement_value_dollars']) == yes, 'NONBINARY_OFFICIAL_PAYOUT')
    payout = D(position['quantity']*(1-yes)); basis = numeric(position['basis']); net = payout-basis
    wallet.s['cash'] = str(numeric(wallet.s['cash'])+payout)
    wallet.s['realized'] = str(numeric(wallet.s['realized'])+net)
    settled_day = datetime.fromtimestamp(available/NS, NY).date().isoformat()
    wallet.s['daily_net'][settled_day] = str(numeric(wallet.s['daily_net'].get(settled_day, '0'))+net)
    quantity = position['quantity']; position.update(quantity=0, basis='0')
    closed = numeric(wallet.s['initial'])+numeric(wallet.s['realized'])
    wallet.s['peak_closed'] = str(max(closed, numeric(wallet.s['peak_closed'])))
    if wallet.name.startswith('SAFE') and closed <= numeric(wallet.s['peak_closed'])*(ONE-numeric(wallet.cfg['closed_drawdown_fraction'])):
        wallet.s['paused'] = True
    return {'status': 'SETTLED', 'ticker': ticker, 'quantity': quantity,
        'yes_result': yes, 'payout': str(payout), 'net': str(net), 'settled_basis': str(basis),
        'at_ns': available, 'policy_state': wallet.s, 'paper_only': True,
        'official_binary_result_proven': True, 'account_cash_receipt_proven': False,
        'cash_clock': 'PUBLIC_FINAL_RESULT_PLUS_EXPLICIT_PAPER_AVAILABILITY'}


def collect_weather(request):
    from tempdesk.weather_retry import install as install_weather_retry
    install_weather_retry()
    # Collection modules contain public NOAA GET/range requests only. Explicit
    # action loads them; no auth, demo exchange, original runner or service.
    day = request['day']; require(day[:7] == '2026-10', 'EXPLICIT_NEW_MONTH_CHECKPOINT_REQUIRED')
    capture = Path(request['capture_dir']).resolve()
    runtime_root = Path(os.environ.get('TEMP_DESK_RUNTIME_ROOT', str(Path(__file__).resolve().parents[2]/'runtime'))).resolve()
    require(capture == runtime_root or runtime_root in capture.parents,
        'WEATHER_CAPTURE_MUST_BE_IN_DASHBOARD_RUNTIME')
    modules = {}
    for name in ('shared_weather_v1', 'shared_weather_v2', 'collect_nyc_weather_v1', 'extract_nyc_points_v2'):
        module = types.ModuleType(name); module.__file__ = str(source_path(WEATHER/'vendor'/(name+'.py')))
        sys.modules[name] = module
        tree = ast.parse(source_bytes(WEATHER/'vendor'/(name+'.py')))
        tree.body = [node for node in tree.body if not isinstance(node, ast.If)]
        exec(compile(tree, module.__file__, 'exec'), module.__dict__); modules[name] = module
    env = {'ThreadPoolExecutor': __import__('concurrent.futures', fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor,
        'as_completed': __import__('concurrent.futures', fromlist=['as_completed']).as_completed,
        'datetime': datetime, 'timedelta': timedelta, 'Path': Path, 'uuid': __import__('uuid'),
        'HERE': WEATHER, 'read': lambda p: json.loads(Path(p).read_bytes()),
        'save': lambda p, x: (Path(p).parent.mkdir(parents=True, exist_ok=True), Path(p).write_text(json.dumps(x, allow_nan=False))),
        'digest': lambda p: sha256(Path(p).read_bytes()).hexdigest(), 'require': require,
        'now': lambda: datetime.now(timezone.utc), 'ns': weather_math()['ns'], 'UTC': timezone.utc,
        'NY': NY, 'anchor': clock, 'NS': NS, 'verify_sources': dependencies,
        'collector': modules['collect_nyc_weather_v1'], 'extractor': modules['extract_nyc_points_v2'],
        'object_key': modules['shared_weather_v2'].object_key,
        'object_url': modules['shared_weather_v2'].object_url, 'BROAD': modules['shared_weather_v2'].BROAD}
    tree = ast.parse(source_bytes(WEATHER/'weather.py'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('source_plan', 'collect')]
    # Redirect only read-only registry references to frozen copies; collector DATA
    # and point packet DATA are assigned its new Dashboard capture by collect().
    class RegistryRedirect(ast.NodeTransformer):
        def visit_Assign(self, node):
            node = self.generic_visit(node)
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('registry', 'geometry'):
                node.value = ast.Call(func=ast.Name(id='source_path', ctx=ast.Load()), args=[node.value], keywords=[])
            return node
    env['source_path'] = source_path
    selected = [RegistryRedirect().visit(node) for node in selected]
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), '<pinned NYC NOAA collector>', 'exec'), env)
    packet = env['collect'](day, capture)
    if packet['complete_native_capture']:
        packet['frame'] = native_frame(day, packet)
    return {'weather': packet, 'capture_dir': str(capture), 'orders_sent': 0}


def dispatch(request):
    action = request.get('action', 'describe')
    require(action in ('describe', 'dependencies', 'collect_weather', 'forecast', 'decide', 'arrival', 'settle'), 'UNKNOWN_ACTION')
    return globals()[action](request)


if __name__ == '__main__':
    try:
        result = dispatch(json.load(sys.stdin))
    except Exception as error:
        result = {'status': 'BLOCKED', 'error': str(error), 'error_type': type(error).__name__,
            'orders': [], 'orders_sent': 0, 'paper_only': True}
    json.dump(result, sys.stdout, allow_nan=False, separators=(',', ':'))
    sys.stdout.write('\n')
