"""Isolated Miami saved-model paper adapter; JSON stdin/stdout, no order API.

October weights, native forecast sampling and both P09 baskets are unchanged.
Monitoring calculations retain actual late receipt clocks. They cannot create a
timely 10am signal. The caller owns cash, reservations and shared liquidity.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from email.utils import parsedate_to_datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import uuid
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
NS = 10**9
HOUR = 3600 * NS
UTC = timezone.utc
FROZEN = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Miami/Frozen/MIAMI-V10-P09-v1')
BUNDLE = FROZEN / 'experiments/MIAMI-V10-P09-v2-1h/model/portable-v2'
REFERENCE = FROZEN / 'runtime/reference_trader'
COLLECTOR = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Development/Miami/MIAMI-ORIGINAL-DEMO-v2-20261011/live_weather.py')
PROFILES = {
    'SELECTED_CHAMPION': {'model_id': 'MIAMI-IMPROVED-2026-10-SIMPLEX_MIX',
        'weather_choice': 'SIMPLEX_MIX', 'rule_id': 'CHAMPION_PRICE10_65',
        'minimum_price_cents': 10, 'maximum_price_cents': 65, 'minimum_probability_gap': 0.},
    'RAW_ORIGINAL': {'model_id': 'MIAMI-2026-10-PASTONLY-V10-RAW',
        'weather_choice': 'RAW', 'rule_id': 'ORIGINAL_P09',
        'minimum_price_cents': 5, 'maximum_price_cents': 80, 'minimum_probability_gap': .10},
}
_OPS = None
_STATES = None
_ECON = None
_WEATHER = None


def source(path):
    """Resolve exact original source identity; a supplied map forbids fallback."""
    original = str(Path(path)).replace('\\', '/')
    setting = os.environ.get('TEMP_DESK_SOURCE_MAP')
    if not setting:
        return Path(path)
    inline = setting.lstrip().startswith('{')
    map_file = None if inline else Path(setting).resolve()
    wrapper = json.loads(setting) if inline else read(map_file)
    mapping = wrapper.get('mapping', wrapper.get('sources', wrapper))
    if not isinstance(mapping, dict):
        raise ValueError('Frozen source map must contain a mapping object')
    normalized = {str(k).replace('\\', '/').lower(): v for k, v in mapping.items()}
    if original.lower() not in normalized:
        raise ValueError('Source absent from frozen integration map: ' + original)
    value = normalized[original.lower()]
    if not isinstance(value, dict) or not isinstance(value.get('sha256'), str):
        raise ValueError('Frozen source map requires each copied file hash')
    target = Path(value['path'])
    if not target.is_absolute() and map_file is not None:
        target = map_file.parent / target
    target = target.resolve()
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != value['sha256']:
        raise ValueError('Frozen mapped source missing or changed: ' + original)
    return target


def read(path):
    return json.loads(Path(path).read_bytes(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def serial(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def stamp(value):
    if type(value) is int:
        return value
    match = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)', value)
    if not match:
        raise ValueError('Aware exact timestamp required')
    parsed = datetime.fromisoformat(match[1] + match[3].replace('Z', '+00:00')).astimezone(UTC)
    delta = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * NS + int((match[2] or '').ljust(9, '0') or '0')


def iso(value):
    whole, fraction = divmod(int(value), NS)
    dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=whole)
    return dt.strftime('%Y-%m-%dT%H:%M:%S') + (f'.{fraction:09d}' if fraction else '') + 'Z'


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('Boolean amount refused')
    answer = Decimal(str(value))
    if not answer.is_finite():
        raise ValueError('Finite amount required')
    return answer


def profile(request):
    name = request.get('profile_id', 'SELECTED_CHAMPION')
    if name not in PROFILES:
        raise ValueError('Only SELECTED_CHAMPION and RAW_ORIGINAL are registered')
    return name


def day_check(day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Canonical climate date required')
    if day[:7] != '2026-10':
        raise ValueError('CHECKPOINT_MONTH_UNSUPPORTED: October only; no November fallback/refit')


def decision_ns(day):
    return stamp(datetime.combine(date.fromisoformat(day), datetime.min.time(), ZoneInfo('America/New_York')).replace(hour=10).isoformat())


def dependency_paths():
    return [BUNDLE / 'saved-calibration-bundle.json', BUNDLE / 'profile.json',
        *[BUNDLE / 'states' / ('2026-10-' + suffix + '.json') for suffix in ('original', 'calibration', 'selection')],
        FROZEN / 'model/fit-run-v1/2026-10/selection.json',
        *[BUNDLE / 'operators' / (name + '.py') for name in
          ('v10_contract_reference', 'city_profile', 'city_v10_simplex_blend_v1', 'v10_math_reference')],
        REFERENCE / 'definitions/canonical24-definition-only-registry-v1.json',
        *[REFERENCE / 'kernels' / name for name in ('probability_transform.py', 'p09_parameter_math.py',
          'p09_decimal_math.py', 'p09_order_math.py', 'p09_policy.py')], COLLECTOR]


def dependencies():
    rows = []
    for original in dependency_paths():
        path = source(original)
        raw = path.read_bytes()
        rows.append({'path': str(original), 'resolved_path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return {'status': 'AVAILABLE', 'paths': [r['path'] for r in rows], 'files': rows, 'sources': rows,
            'checkpoint_month': '2026-10', 'new_fits': 0}


def _bound(binding):
    p = source(BUNDLE / binding['path'])
    raw = p.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256'] or len(raw) != binding['bytes']:
        raise ValueError('Pinned model/operator bytes differ')
    return p


def operators():
    global _OPS
    if _OPS is not None:
        return _OPS
    descriptor = read(source(BUNDLE / 'saved-calibration-bundle.json'))
    names = ('v10_contract_reference', 'city_profile', 'city_v10_simplex_blend_v1', 'v10_math_reference')
    modules = {}
    prior = {n: sys.modules.get(n) for n in names}
    try:
        for name in names:
            path = _bound(descriptor['operators'][name])
            spec = importlib.util.spec_from_file_location('_tempdesk_miami_' + name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            modules[name] = module
    finally:
        for name, previous in prior.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
    _OPS = modules
    return modules


def states():
    global _STATES
    if _STATES is not None:
        return _STATES
    c = operators()['v10_contract_reference']
    descriptor = c.verify_seal(read(source(BUNDLE / 'saved-calibration-bundle.json')))
    m = descriptor['months']['2026-10']
    original = c.verify_seal(read(_bound(m['original_state'])))
    calibration = c.verify_seal(read(_bound(m['calibration_state'])))
    selection = c.verify_seal(read(_bound(m['selection'])))
    city = read(_bound(descriptor['profile']))
    raw_selection = read(source(FROZEN / 'model/fit-run-v1/2026-10/selection.json'))
    if (m['selected_candidate'] != 'SIMPLEX_MIX' or selection['selected_candidate'] != 'SIMPLEX_MIX'
        or raw_selection['branch'] != 'RAW' or calibration['original_fitted_state_self_sha256'] != original['self_sha256']
        or original['origin_ns'] != m['training_cutoff_ns'] or calibration['origin_ns'] != original['origin_ns']
        or original['city_profile'] != city or calibration['city_profile'] != city):
        raise ValueError('Saved own-city parent/selection identity differs')
    if any(r['climate_date'] >= '2026-10-01' or r['physical_label_available_ns'] >= original['origin_ns']
           for r in original['raw_training_labels']):
        raise ValueError('Future training label refused')
    _STATES = (original, calibration, city, m)
    return _STATES


def contracts(rows, day):
    if len(rows) != 6:
        raise ValueError('SIX_NATIVE_CONTRACTS_REQUIRED')
    event = 'KXHIGHMIA-' + date.fromisoformat(day).strftime('%y%b%d').upper() + '-'
    result = []
    for r in sorted(rows, key=lambda x: float('-inf') if x['lower'] is None else decimal(x['lower'])):
        if not r['ticker'].startswith(event):
            raise ValueError('Foreign dated Miami ticker')
        lo, hi = r['lower'], r['upper']
        if any(x is not None and decimal(x) != decimal(x).to_integral_value() for x in (lo, hi)):
            raise ValueError('Saved integer-F class semantics required')
        kind = 'less' if lo is None else 'greater' if hi is None else 'between'
        result.append({'ticker': r['ticker'], 'market_ticker': r['ticker'], 'strike_type': kind,
            'lower': lo, 'upper': hi, 'lower_inclusive': lo is not None,
            'upper_inclusive': False, 'ordinal_index': len(result)})
    operators()['v10_contract_reference'].integer_bounds(result)
    return result


def _calculate(frame, native_contracts, name):
    """Exact saved numerical chain; late-clock monitoring never changes input clocks."""
    import numpy as np
    op = operators()
    c, o = op['v10_contract_reference'], op['v10_math_reference']
    original, calibration, _, _ = states()
    c.integer_bounds(native_contracts)
    # Same eleven-feature map and ridge arithmetic as the saved operator. The
    # caller validates real clocks separately so late display cannot masquerade
    # as the original predecision forecast. No timestamp is replaced here.
    day = date.fromisoformat(frame['climate_date'])
    angle = 2 * math.pi * (day.timetuple().tm_yday - 1) / (366 if day.year % 4 == 0 else 365)
    features = {'calendar.cos_day': math.cos(angle), 'calendar.sin_day': math.sin(angle)}
    for prefix, model, member in (('hrrr', 'hrrr', None), ('gefs.mean', 'gefs', 'avg'), ('gefs.spread', 'gefs', 'spr')):
        values = [r['value'] for r in frame['fields'] if r['model'] == model and r['member_id'] == member
                  and r['field_id'] == 'temperature_2m' and frame['climate_start_ns'] <= r['valid_ns'] < frame['climate_end_ns']]
        if len(values) != (24 if model == 'hrrr' else 8) or any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
            raise ValueError('DENSE_NATIVE_TEMPERATURE_FOOTPRINT_REQUIRED')
        features.update({prefix + '.max': max(values), prefix + '.mean': sum(values) / len(values), prefix + '.min': min(values)})
    raw_state = original['raw']
    c.verify_seal(raw_state)
    x = np.asarray([features[k] for k in raw_state['feature_names']])[raw_state['retained_columns']]
    mu = c.finite(float(raw_state['intercept'] + ((x - np.asarray(raw_state['means'])) / np.asarray(raw_state['scales'])) @ np.asarray(raw_state['coefficients'])))
    raw = o.raw_vector(mu, original['residual'], native_contracts)
    ticks = [r['ticker'] for r in native_contracts]
    if name == 'RAW_ORIGINAL':
        q = raw
    else:
        pava, _ = o.apply_isotonic(raw, original['isotonic'])
        pooled = o.repair(pava, ticks, 'MISSING', original['tables'], local=False)['probabilities']
        q = op['city_v10_simplex_blend_v1'].mix([raw, pava, pooled], calibration['blend'])
    q = c.probability_vector(q)
    ranked = sorted(q, reverse=True)
    return {'profile_id': name, 'model_id': PROFILES[name]['model_id'], 'weather_choice': PROFILES[name]['weather_choice'],
        'climate_date': frame['climate_date'], 'day': frame['climate_date'], 'location_f': mu,
        'probabilities_yes': dict(zip(ticks, q, strict=True)),
        'entropy': -math.fsum(p * math.log(p) for p in q if p > 0) / math.log(6),
        'gap': ranked[0] - ranked[1], 'training_cutoff_ns': original['origin_ns'],
        'asof_ns': frame['decision_ns'], 'pressure_used': False, 'new_fits': 0}


def _weather_functions():
    global _WEATHER
    if _WEATHER is not None:
        return _WEATHER
    import requests
    from urllib.parse import urlsplit
    raw = source(COLLECTOR).read_bytes()
    if hashlib.sha256(raw).hexdigest() != '95b581f840e433590928a6c3495888fcca535946d20b3f231a2510769dd2f350':
        raise ValueError('Accepted native collector changed')
    env = {'date': date, 'datetime': datetime, 'timedelta': timedelta, 'parsedate_to_datetime': parsedate_to_datetime,
        'Path': Path, 'urlsplit': urlsplit, 'json': json, 'math': math, 're': re, 'uuid': uuid,
        'requests': requests, 'UTC': UTC, 'now': lambda: datetime.now(UTC), 'utc': lambda v: datetime.fromisoformat(v.replace('Z', '+00:00')).astimezone(UTC),
        'ns': stamp, 'digest': lambda b: hashlib.sha256(b).hexdigest(),
        'save': lambda p, value: Path(p).write_text(json.dumps(serial(value), indent=2, allow_nan=False), encoding='utf8')}
    tree = ast.parse(raw)
    names = {'fetch', 'span', 'plan', 'mirrors', 'capture', 'decode'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    nodes += [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {'HOSTS', 'GRIDS'} for t in n.targets)]
    if {n.name for n in nodes if isinstance(n, ast.FunctionDef)} != names:
        raise ValueError('Native collector surface differs')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source(COLLECTOR)), 'exec'), env)
    # The installed Windows ecCodes wheel embeds both resources in MEMFS.
    # Its native lexer/parser crashes on concurrent first-use AND concurrent
    # warm parses. Initialize in this calling thread, then serialize all native
    # decode operations; public HTTP downloads remain parallel. The accepted
    # native field/grid/point decoder itself is unchanged.
    for name, expected in {'ECCODES_DEFINITION_PATH': '/MEMFS/definitions',
                           'ECCODES_SAMPLES_PATH': '/MEMFS/samples',
                           'ECCODES_PYTHON_USE_FINDLIBS': '0'}.items():
        if os.environ.get(name, expected) != expected:
            raise ValueError('Unsupported decoder environment: ' + name)
        os.environ[name] = expected
    import eccodes as ec
    if ec.codes_get_api_version() != '2.48.0':
        raise ValueError('Reviewed ecCodes 2.48.0 runtime required')
    if ec.codes_definition_path() != '/MEMFS/definitions' or ec.codes_samples_path() != '/MEMFS/samples':
        raise ValueError('Embedded ecCodes resources differ')
    sample = ec.codes_grib_new_from_samples('GRIB2')
    ec.codes_release(sample)  # Codec health fixture only; no model input.
    native_decode = env['decode']
    native_lock = threading.RLock()
    def serialized_decode(*args):
        with native_lock:
            return native_decode(*args)
    env['decode'] = serialized_decode
    env['codec_runtime'] = {'api_version': ec.codes_get_api_version(),
        'definitions': ec.codes_definition_path(), 'samples': ec.codes_samples_path(),
        'native_decode_serialized': True, 'http_workers': 4,
        'sample_health_check_not_a_weather_input': True}
    _WEATHER = env
    return env


def _receipt_bytes(receipt, at):
    if receipt.get('source_kind') != 'ACTUAL_LOCAL_HTTP_RECEIPT' or not 200 <= receipt['http_status'] < 300:
        raise ValueError('GENUINE_WEATHER_RECEIPT_REQUIRED')
    if not stamp(receipt['request_started_utc']) <= stamp(receipt['received_utc']) <= at:
        raise ValueError('WEATHER_RECEIPT_AFTER_DISPLAY_CLOCK')
    runtime = os.environ.get('TEMP_DESK_RUNTIME_ROOT')
    path = Path(receipt['raw_path']).resolve()
    if runtime and not path.is_relative_to(Path(runtime).resolve()):
        raise ValueError('WEATHER_RECEIPT_ESCAPES_RUNTIME_ROOT')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != receipt['response_sha256']:
        raise ValueError('WEATHER_RECEIPT_BYTES_DIFFER')
    return body


def _verified_fields(fields, day, at):
    w = _weather_functions()
    expected = {r['object_id']: r for r in w['plan'](day)}
    if len(fields) != 44 or len({r['object_id'] for r in fields}) != 44 or set(expected) != {r['object_id'] for r in fields}:
        raise ValueError('ALL_44_NATIVE_MIAMI_FIELDS_REQUIRED')
    for row in fields:
        planned = expected[row['object_id']]
        r, ir = row['receipt'], row['index_receipt']
        if r['url'] not in w['mirrors'](planned) or ir['url'] != r['url'] + '.idx':
            raise ValueError('Foreign native source URL')
        body = _receipt_bytes(r, at)
        _receipt_bytes(ir, at)
        if w['decode'](planned, body, r, ir) != row or row['available_ns'] > at:
            raise ValueError('WEATHER_VALUE_OR_RELEASE_IDENTITY_DIFFERS')
    return fields


def _frame(fields, day):
    op = operators()
    city = op['city_profile'].CityProfile.from_dict(states()[2])
    start, end = city.climate_bounds(day)
    anchor = decision_ns(day)
    return {'climate_date': day, 'city_id': 'KXHIGHMIA', 'station_id': 'KMIA', 'city_context': city.context(day),
        'climate_start_ns': stamp(start.isoformat()), 'climate_end_ns': stamp(end.isoformat()),
        'decision_ns': anchor, 'feature_cutoff_ns': anchor, 'fields': fields, 'observations': [],
        'required_field_count': 44, 'expected_temperature_valid_ns': {
          'hrrr': sorted(r['valid_ns'] for r in fields if r['model'] == 'hrrr'),
          'gefs_avg': sorted(r['valid_ns'] for r in fields if r['member_id'] == 'avg'),
          'gefs_spr': sorted(r['valid_ns'] for r in fields if r['member_id'] == 'spr')}}


def forecast(request):
    day, name = request['day'], profile(request)
    day_check(day)
    at = int(request.get('at_ns', stamp(datetime.now(UTC).isoformat())))
    packet = request.get('packet', request.get('weather_packet', request.get('receiptpacket', request.get('weather', {}))))
    given_frame = request.get('frame', request.get('weather_frame', packet.get('frame') if isinstance(packet, dict) else None))
    if given_frame:
        if given_frame.get('climate_date') != day or given_frame.get('station_id') != 'KMIA' or given_frame.get('city_id') != 'KXHIGHMIA':
            raise ValueError('Foreign weather frame')
        fields = given_frame.get('fields', [])
    else:
        if packet.get('climate_date', packet.get('day')) != day:
            raise ValueError('CURRENT_NATIVE_WEATHER_PACKET_REQUIRED')
        fields = packet.get('fields', [])
    _verified_fields(fields, day, at)
    f = _frame(fields, day)
    native = contracts(request['contracts'], day)
    f['contracts'] = native
    timely = all(r['available_ns'] < f['decision_ns'] for r in fields)
    if timely:
        operators()['v10_contract_reference'].validate_frame(f)
    prediction = _calculate(f, native, name)
    reasons = ['DATED_TWC_TO_NWS_PHYSICAL_TARGET_EQUIVALENCE_UNPROVED']
    if not timely:
        reasons.append('WEATHER_RECEIVED_AFTER_ORIGINAL_10AM_CUTOFF')
    prediction.update(status='NWS_PHYSICAL_FORECAST_ONLY', blockers=reasons,
        weather_trade_eligible=timely, trade_eligible=False,
        latest_weather_available_ns=max(r['available_ns'] for r in fields), actual_receipt_verified=True,
        display_at_ns=at, physical_training_target='NWS_CLIMIA_KMIA_FIXED05Z_INTEGER_F',
        economic_target_equivalence_proven=False, paper_only=True, actual_orders=0,
        qualification='Monitoring uses actual values and receipt clocks. Late data cannot reconstruct a timely 10am signal; dated TWC/NWS target equivalence remains conditional.')
    if request.get('target_binding'):
        from tempdesk.policy_receipts import verify
        proof=verify(request['target_binding'],'miami',day,request.get('mapping_contracts',request['contracts']),at)
        prediction.update(prepared=request.get('purpose')=='prepare_publication',paper_target_mapping_admitted=True,trade_eligible=timely and f['decision_ns']<=at<f['decision_ns']+10**9,status='PAPER_DAILY_CLI_PROXY_FORECAST',blockers=[] if timely else ['WEATHER_RECEIVED_AFTER_ORIGINAL_10AM_CUTOFF'],target_equivalence_guaranteed=False,paper_target_mapping=request['target_binding'],forecast_scope=proof['hypothesis'])
    return prediction


def collect_weather(request):
    from tempdesk.weather_retry import install as install_weather_retry
    install_weather_retry()
    day = request['day']
    day_check(day)
    if day != datetime.now(UTC).astimezone(ZoneInfo('America/New_York')).date().isoformat():
        raise ValueError('COLLECT_CURRENT_MIAMI_DATE_ONLY')
    w = _weather_functions()
    runtime = Path(os.environ.get('TEMP_DESK_RUNTIME_ROOT', Path(__file__).resolve().parents[2] / 'runtime')).resolve()
    if os.environ.get('TEMP_DESK_SOURCE_MAP') and not os.environ.get('TEMP_DESK_RUNTIME_ROOT'):
        raise ValueError('FROZEN_WORKER_REQUIRES_SEPARATE_RUNTIME_ROOT')
    capture = Path(request.get('capture_dir', runtime / 'miami/weather')).resolve()
    if not capture.is_relative_to(runtime):
        raise ValueError('CAPTURE_DIRECTORY_ESCAPES_RUNTIME_ROOT')
    directory = capture / day / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    previous = request.get('cached_packet', request.get('previous_packet', {}))
    cached = {}
    at = stamp(datetime.now(UTC).isoformat())
    if previous.get('climate_date') == day:
        plan_by_id = {r['object_id']: r for r in w['plan'](day)}
        for row in previous.get('fields', []):
            try:
                planned = plan_by_id[row['object_id']]
                r, ir = row['receipt'], row['index_receipt']
                if r['url'] not in w['mirrors'](planned) or ir['url'] != r['url'] + '.idx':
                    continue
                body = _receipt_bytes(r, at)
                _receipt_bytes(ir, at)
                if w['decode'](planned, body, r, ir) == row:
                    cached[row['object_id']] = row
            except (ValueError, KeyError, OSError):
                pass  # Only independently admitted exact bytes are reused.
    fields, errors = list(cached.values()), []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(w['capture'], r, directory): r for r in w['plan'](day) if r['object_id'] not in cached}
        for job in as_completed(pending):
            try:
                fields.append(w['decode'](*job.result()))
            except Exception as exc:
                errors.append({'object_id': pending[job]['object_id'], 'reason': type(exc).__name__ + ': ' + str(exc)})
    fields.sort(key=lambda r: r['object_id'])
    packet = {'climate_date': day, 'fields': fields, 'observations': [], 'required_fields': 44,
        'complete': len(fields) == 44, 'errors': errors, 'source_mode': 'ACTUAL_CURRENT_LOCAL_RECEIPTS',
        'completed_utc': datetime.now(UTC).isoformat(), 'new_fits': 0, 'codec_runtime': w['codec_runtime']}
    (directory / 'packet.json').write_text(json.dumps(serial(packet), indent=2, allow_nan=False), encoding='utf8')
    return {'status': 'AVAILABLE' if packet['complete'] else 'INCOMPLETE', 'packet': packet,
            'weather_packet': packet, 'packet_path': str(directory / 'packet.json'), 'blockers': errors, 'actual_orders': 0}


def _economic_functions():
    global _ECON
    if _ECON is not None:
        return _ECON
    env = {'math': math, 'Decimal': Decimal, 'ROUND_CEILING': ROUND_CEILING, 'json': json,
        'Mapping': __import__('typing').Mapping, 'Any': __import__('typing').Any,
        'ReplayError': ValueError, 'DevelopmentSearchError': ValueError}
    groups = [('probability_transform.py', {'_fee', '_transform'}),
        ('p09_parameter_math.py', {'validate_spec', '_adjust'}),
        ('p09_decimal_math.py', {'_decimal_text', '_probability_decimal'}),
        ('p09_order_math.py', {'_economics', '_limit_for_return', '_order_record'}),
        ('p09_policy.py', {'fee', 'cost', 'ranking', 'candidates', 'allocate', 'arrive'})]
    for filename, names in groups:
        path = source(REFERENCE / 'kernels' / filename)
        tree = ast.parse(path.read_text())
        if filename == 'p09_parameter_math.py':
            defaults = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'DEFAULTS' for t in n.targets)]
            exec(compile(ast.Module(body=defaults, type_ignores=[]), str(path), 'exec'), env)
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        if {n.name for n in nodes} != names:
            raise ValueError('Saved economic operator surface differs')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), env)
    _ECON = env
    return env


def _parameters(name):
    p = deepcopy(read(source(REFERENCE / 'definitions/canonical24-definition-only-registry-v1.json'))['P09_selector_parameters'])
    p.update({k: v for k, v in PROFILES[name].items() if k in ('minimum_price_cents', 'maximum_price_cents', 'minimum_probability_gap')})
    p.update(allowed_sides=['NO'], probability_haircut=0., minimum_expected_net_return=.10)
    return p


def _quote(raw, day, ticker, at):
    result = {'strict_usable': False, 'strict_failure_reasons': [], 'climate_date': day,
        'market_ticker': ticker, 'contract_side': 'NO', 'quote_at_utc': None, 'quote_age_ms': None,
        'best_bid_cents': None, 'best_ask_cents': None, 'displayed_ask_quantity_contracts': None}
    try:
        if not raw:
            raise ValueError('MISSING_BOOK')
        received = raw['received_ns']
        if type(received) is not int or received > at:
            raise ValueError('BOOK_NOT_RECEIVED_BY_CUTOFF')
        clock_kind = raw.get('source_age_clock', 'RECEIPT_CLOCK')
        if clock_kind == 'RECEIPT_CLOCK':
            clock = received
        elif clock_kind in ('SERVER_TIME', 'SERVER_DATE', 'SERVER_CLOCK', 'EXCHANGE_EVENT_CLOCK', 'NATIVE_EVENT_CLOCK'):
            clock = raw.get('server_time_ns')
            if type(clock) is not int:
                raise ValueError('DECLARED_SOURCE_CLOCK_MISSING')
        else:
            raise ValueError('UNSUPPORTED_BOOK_AGE_CLOCK')
        if not 0 <= at - clock <= 5 * NS:
            raise ValueError('BOOK_OLDER_THAN_NATIVE_FIVE_SECONDS')
        if clock > received:
            raise ValueError('SOURCE_CLOCK_AFTER_RECEIPT')
        def levels(items):
            if not isinstance(items, list):
                raise ValueError('BOOK_SIDE_MISSING')
            parsed = [(decimal(r[0]), decimal(r[1])) for r in items]
            if any(not 0 < price < 1 or price * 100 != (price * 100).to_integral_value() or qty <= 0 for price, qty in parsed):
                raise ValueError('NATIVE_CENT_PRICE_OR_POSITIVE_DEPTH_REQUIRED')
            if len({price for price, qty in parsed}) != len(parsed):
                raise ValueError('DUPLICATE_PRICE_LEVEL')
            return parsed
        bids, asks = levels(raw.get('bids')), levels(raw.get('asks'))
        if not bids or not asks:
            raise ValueError('EMPTY_EXECUTABLE_OR_SPREAD_SIDE')
        bid, _ = max(bids)
        ask, quantity = min(asks)
        if bid >= ask or quantity < 1:
            raise ValueError('LOCKED_CROSSED_OR_NO_WHOLE_TOP_QUANTITY')
        if raw.get('bid') is not None and decimal(raw['bid']) != bid or raw.get('ask') is not None and decimal(raw['ask']) != ask:
            raise ValueError('TOP_LEVEL_SUMMARY_DIFFERS')
        result.update(strict_usable=True, best_bid_cents=int(bid * 100), best_ask_cents=int(ask * 100),
            displayed_ask_quantity_contracts=str(quantity), quote_at_utc=iso(clock), quote_age_ms=(at - clock) / 10**6,
            source_age_clock=clock_kind, source_event_time_proven=clock_kind != 'RECEIPT_CLOCK')
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        result['strict_failure_reasons'].append(str(exc))
    return result


def _plan(day, name, probabilities, native_contracts, quote_rows, opening_cash, available_cash):
    cash, available = decimal(opening_cash), decimal(available_cash)
    if cash < 0 or available < 0 or available > cash:
        raise ValueError('Available/opening cash bounds differ')
    keys = [r['ticker'] for r in native_contracts]
    if set(probabilities) != set(keys):
        raise ValueError('Exact six-class probability join required')
    operators()['v10_contract_reference'].probability_vector([probabilities[k] for k in keys])
    k = _economic_functions()
    result = k['candidates'](day, probabilities, native_contracts, quote_rows, _parameters(name))
    budget = min(cash * Decimal('.10'), available)
    return {**result, **k['allocate'](day, result['candidates'], budget), 'opening_cash_dollars': str(cash)}


def decide(request):
    if request.get('paper_mapping_mode')=='VERIFIED_DAILY_CLI_PROXY_v1':
        from tempdesk.policy_receipts import verify
        verify(request.get('target_binding'),'miami',request['day'],request.get('mapping_contracts',request['contracts']),int(request['at_ns']))
    day, name = request['day'], profile(request)
    day_check(day)
    at, anchor = int(request['at_ns']), decision_ns(day)
    state = deepcopy(request.get('state', {}))
    blocked = []
    if not anchor <= at < anchor + NS:
        blocked.append('OUTSIDE_ORIGINAL_10AM_DECISION_CLOCK')
    if request.get('submitted') or day in state.get('submitted_days', []) or state.get('day') == day and state.get('submitted'):
        blocked.append('DAILY_INITIAL_BASKET_ALREADY_SUBMITTED')
    if state.get('unknown_reservation') or state.get('pending_reservation') or state.get('unknown_execution'):
        blocked.append('UNRESOLVED_BUY_RESERVATION')
    prediction = request.get('forecast')
    if prediction is None and not blocked:
        prediction = forecast(request)
    if prediction is None or prediction.get('profile_id') != name or prediction.get('model_id') != PROFILES[name]['model_id']:
        blocked.append('OWN_SAVED_FORECAST_REQUIRED')
    elif (prediction.get('day', prediction.get('climate_date')) != day or prediction.get('asof_ns') != anchor
          or prediction.get('weather_trade_eligible') is not True or prediction.get('actual_receipt_verified') is not True):
        blocked.append('TIMELY_ACTUAL_RECEIPT_FORECAST_REQUIRED')
    if prediction is not None and prediction.get('economic_target_equivalence_proven') is not True and prediction.get('paper_target_mapping_admitted') is not True:
        blocked.append('DATED_TWC_TO_NWS_PHYSICAL_TARGET_EQUIVALENCE_UNPROVED')
    if blocked:
        return {'status': 'BLOCKED', 'profile_id': name, 'orders': [], 'blockers': blocked, 'policy_state': state}
    native = contracts(request['contracts'], day)
    quote_rows = {(day, r['ticker'], 'NO'): _quote(request.get('quotes', {}).get(r['ticker']), day, r['ticker'], at) for r in native}
    allocation = _plan(day, name, prediction['probabilities_yes'], native, quote_rows,
        request['opening_cash'], request.get('available_cash', request['opening_cash']))
    orders = []
    for order in allocation['orders']:
        original = deepcopy(order)
        original.update(decision_ns=at, original_decision_anchor_ns=anchor, arrival_ns=at + 5 * NS,
                        profile_id=name, clock_policy='PUBLIC_REST_CLOCK_V1')
        orders.append({'ticker': order['market_ticker'], 'side': 'NO', 'quantity': order['quantity'],
            'limit_price': str(Decimal(order['limit_price_cents']) / 100),
            'reserved_outlay': order['maximum_reserved_outlay_dollars'], 'fee_rate': '.07', 'fee_quantum': '.0001',
            'arrival_delay_seconds': 5, 'decision_ns': at, 'original_decision_anchor_ns': anchor,
            'arrival_ns': at + 5 * NS, 'clock_policy': 'PUBLIC_REST_CLOCK_V1',
            'native_order': original})
    policy_state = {**state, 'day': day, 'decision_evaluated_ns': at, 'submitted': bool(orders)}
    return {'status': 'ORDER_FROZEN' if orders else 'ABSTAINED', 'profile_id': name, 'orders': orders,
        'reserved_outlay': allocation['reserved_outlay_dollars'], 'budget': allocation['budget_dollars'],
        'blockers': [] if orders else [allocation['reason'] or 'NO_FUNDED_WHOLE_CONTRACT'],
        'unavailable_market_sides': allocation['unavailable_market_sides'], 'checks': allocation['checks'],
        'policy_state': policy_state, 'native_allocation': allocation,
        'paper_only': True, 'actual_orders': 0, 'clock_policy': 'PUBLIC_REST_CLOCK_V1',
        'original_decision_anchor_ns': anchor, 'actual_decision_ns': at,
        'source_qualification': 'Explicit REST decision slot [10am,10am+1s), actual arrival slot [actual decision+5s,+6s). This is a prospective clock adaptation, not exact historical timing equivalence. Native exchange event time may be absent.'}


def arrival(request):
    day, name = request['day'], profile(request)
    day_check(day)
    order = deepcopy(request['frozen_order'])
    original = order['native_order']
    at = int(request['at_ns'])
    anchor = decision_ns(day)
    if (original.get('profile_id') != name or original.get('original_decision_anchor_ns') != anchor
        or not anchor <= original.get('decision_ns', -1) < anchor + NS
        or original.get('clock_policy') != 'PUBLIC_REST_CLOCK_V1'):
        raise ValueError('Frozen order profile/day differs')
    if (order['ticker'] != original['market_ticker'] or order['side'] != 'NO'
        or order['quantity'] != original['quantity'] or decimal(order['limit_price']) * 100 != original['limit_price_cents']
        or decimal(order['reserved_outlay']) != decimal(original['maximum_reserved_outlay_dollars'])):
        raise ValueError('Frozen fixed order changed')
    if not original['arrival_ns'] <= at < original['arrival_ns'] + NS or original['arrival_ns'] != original['decision_ns'] + 5 * NS:
        return {'status': 'UNKNOWN', 'reason': 'FIXED_FIVE_SECOND_ARRIVAL_EVIDENCE_MISSING',
            'reserved_outlay': order['reserved_outlay'], 'release_reservation': False, 'quantity': None}
    quote = _quote(request.get('quotes', {}).get(order['ticker']), day, order['ticker'], at)
    result = _economic_functions()['arrive'](day, original, {(day, order['ticker'], 'NO'): quote}, Decimal('.10'))
    if result['execution_status'] == 'UNKNOWN_EXECUTION':
        return {'status': 'UNKNOWN', 'reason': result['execution_reason'], 'native_result': result,
            'reserved_outlay': order['reserved_outlay'], 'release_reservation': False, 'quantity': None}
    if result['execution_status'] == 'NOT_FILLED':
        return {'status': 'CANCELLED', 'reason': result['execution_reason'], 'native_result': result,
            'principal': '0', 'fee': '0', 'quantity': 0, 'release_reservation': True}
    quantity = original['quantity']
    price = Decimal(result['fill_price_cents']) / 100
    return {'status': 'FILLED', 'quantity': quantity, 'principal': str(price * quantity),
        'fee': result['fill_fee_dollars'], 'outlay': result['fill_entry_outlay_dollars'],
        'price': str(price), 'fills': [[quantity, str(price)]], 'native_result': result,
        'release_reservation': True, 'conditional_paper_fill': True, 'actual_orders': 0,
        'source_age_clock': quote.get('source_age_clock'), 'exchange_event_time_proven': quote.get('source_event_time_proven', False),
        'clock_policy': 'PUBLIC_REST_CLOCK_V1', 'original_decision_anchor_ns': anchor,
        'actual_decision_ns': original['decision_ns'], 'arrival_slot_start_ns': original['arrival_ns'], 'actual_arrival_ns': at}


def describe():
    common = {'entry_local': '10:00 America/New_York', 'exit': 'OFFICIAL_SETTLEMENT',
        'daily_cash_fraction': '.10', 'arrival_delay_seconds': 5, 'maximum_quote_age_seconds': 5,
        'maximum_spread_cents': 5, 'minimum_expected_net_return': '.10', 'side': 'NO',
        'selection_mode': 'expected_profit', 'allocation': 'ranked whole-contract round robin',
        'fill_policy': 'full frozen quantity at native top or cancel', 'probability_haircut': 0,
        'fee_rate': '.07', 'fee_quantum': '.0001', 'maximum_entropy': 1,
        'climate_start_utc_hour': 5, 'checkpoint_month': '2026-10', 'clock_policy': 'PUBLIC_REST_CLOCK_V1',
        'decision_slot': '[native 10am anchor, anchor+1 second)',
        'arrival_slot': '[actual decision+5 seconds, actual decision+6 seconds)',
        'rest_timing_is_prospective_adaptation_not_archival_equivalence': True}
    return {'status': 'AVAILABLE', 'city': 'Miami', 'city_id': 'miami', 'station': 'KMIA', 'series': 'KXHIGHMIA',
        'timezone': 'America/New_York', 'profiles': [{'profile_id': k, **v, **common} for k, v in PROFILES.items()],
        'weather': {'native_fields': 44, 'hrrr': '00 f005 + 06 f000..022', 'gefs': '00 avg/spr f003..030 every 3h',
                    'availability_minimum_hours_after_issue': 6, 'actual_local_receipts_required': True},
        'paper_only': True, 'order_api': False, 'new_fits': 0,
        'blockers': ['No November checkpoint fallback', 'Late data is monitoring only',
                     'NWS physical training versus current TWC payout equivalence remains conditional']}


def dispatch(request):
    actions = {'describe': lambda r: describe(), 'dependencies': lambda r: dependencies(),
        'collect_weather': collect_weather, 'forecast': forecast, 'decide': decide, 'arrival': arrival}
    if request.get('action') not in actions:
        raise ValueError('Unknown Miami adapter action')
    try:
        return serial(actions[request['action']](request))
    except (ValueError, KeyError, OSError, TypeError, ImportError) as exc:
        return {'status': 'BLOCKED' if request['action'] != 'arrival' else 'UNKNOWN',
            'blockers': [type(exc).__name__ + ': ' + str(exc)], 'orders': [], 'paper_only': True,
            'release_reservation': False, 'actual_orders': 0}


if __name__ == '__main__':
    request = json.loads(sys.stdin.read(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    print(json.dumps(dispatch(request), allow_nan=False, separators=(',', ':')))
