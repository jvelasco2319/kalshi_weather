"""October Miami history: saved weights, native archival admission and cash clocks.

No exchange transport, fitting, live-host wallet or fabricated receipt exists here.
Each profile has an independent $100 conditional wallet. An unknown opportunity
nulls the full-history answer; supported cash is a separately labelled subset.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
BASE = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot')
FROZEN = BASE / 'Miami/Frozen/MIAMI-V10-P09-v1'
CAMPAIGN = BASE / 'Development/Miami/MIAMI-REALITY-SIM-v1-20261012'
PYTHON = BASE.parent / 'Kalshi/_weather/_llm/.venv/Scripts/python.exe'
NS = 10**9
D = Decimal
PROFILES = ('SELECTED_CHAMPION', 'RAW_ORIGINAL')
ENGINE_SHA = 'a3e80d08332d40c601fd169dd22f5e915ffb951e9968108d2b3359ea6a184494'
PACKET_SHA = 'ddeb30516fd42bd9b6ee6ee70592e1b9c8ed3c9459c07f39cdc7df69092fe017'
TAIL_SHA = 'c09c4ced67aff437fb8fcd686df2f479aa298f846492c10dec19b67a3fc58288'
QINDEX_SHA = '28361d795eec2b35915edcca275e6b08d375887415208620f27372c19486f68e'
ROWS_SHA = '441b57813355897935f194b711c4b33f01b463a396335a1c04f96f122d55e673'
ASSUMPTIONS = [
    'HISTORICAL_CONDITIONAL_REPLAY_NOT_ACTUAL_FILLS',
    'NATIVE_EVENT_AGE_MAXIMUM_5_SECONDS_EXACT_10AM_AND_PLUS5_SECONDS',
    'LATEST_CAPTURED_EOF_INDEX_REVISION_IS_NOT_FIRST_HISTORICAL_RECEIPT',
    'FULL_FROZEN_TOP_QUANTITY_FOK_NO_RESIZE_NO_RETRY',
    'OFFICIAL_BINARY_RESULT_WITH_OFFICIAL_SETTLEMENT_CASH_RELEASE_PROXY',
    'NWS_CLIMIA_PHYSICAL_FORECAST_VERSUS_DATED_TWC_PAYOUT_TARGET_UNPROVED',
    'KNOWN_CASH_IS_SUPPORTED_SUBSET_CONDITIONAL_ON_ABSTENTION_AT_UNKNOWN_OPPORTUNITIES',
    'INDEPENDENT_WALLETS_NOT_SHARED_PHYSICAL_CAPACITY',
]


def read(path):
    return json.loads(Path(path).read_bytes())


def pin(path, expected=None):
    path = Path(path).resolve()
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected is not None and sha != expected:
        raise ValueError('SOURCE_HASH_CHANGED: ' + str(path))
    return {'path': str(path), 'sha256': sha, 'bytes': len(raw)}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False, default=str), encoding='utf8')
    os.replace(temporary, path)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def native_time(value):
    if type(value) is int:
        return value
    text = value.replace(' ', 'T')
    if not re.search(r'(Z|[+-]\d{2}:\d{2})$', text):
        text += 'Z'
    m = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)', text)
    if not m:
        raise ValueError('Exact aware source timestamp required')
    dt = datetime.fromisoformat(m[1] + m[3].replace('Z', '+00:00')).astimezone(timezone.utc)
    delta = dt - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * NS + int((m[2] or '').ljust(9, '0') or 0)


def _adapter(release_path, runtime_root):
    release = Path(release_path).resolve()
    manifest = read(release / 'manifest.json')
    worker = manifest['workers']['miami']
    row = next(r for r in manifest['files'] if r['path'] == worker)
    pin(release / worker, row['sha256'])
    os.environ['TEMP_DESK_SOURCE_MAP'] = str(release / 'source-map.json')
    os.environ['TEMP_DESK_RUNTIME_ROOT'] = str(Path(runtime_root).resolve())
    return load('_miami_offline_frozen_' + uuid.uuid4().hex, release / worker)


def _engine(runtime_root):
    """Extract only original admission definitions, never top-level drivers."""
    original = FROZEN / 'runtime/reference_trader/kernels/engine.py'
    source_pin = pin(original, ENGINE_SHA)
    target = Path(runtime_root) / 'sourcepack/miami-native-admission.py'
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    pin(target, ENGINE_SHA)
    names = {'decimal', 'clock', 'token', 'Level', 'BookSnapshot', 'Admission', 'validate_snapshot'}
    tree = ast.parse(target.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    env = {'__name__': '_miami_offline_native_admission', 'dataclass': dataclass,
           'Decimal': D, 'D': D, 'NS': NS, 'ZERO': D(0), 'ONE': D(1)}
    sys.modules[env['__name__']] = SimpleNamespace(**env)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(target), 'exec'), env)
    parent = env['BookSnapshot']
    class ArchivalPhysicalSnapshot(parent):
        @property
        def available_ns(self):
            # Explicit pre-existing EOF-revision conditional lane, not a
            # falsified indexed clock / first receipt.
            return self.event_ns
    env['physical_book_class'] = ArchivalPhysicalSnapshot
    return env, [source_pin, pin(target)]


def _row(raw):
    raw = deepcopy(raw)
    for key in ('outcome', 'bids', 'asks'):
        if isinstance(raw[key], str):
            raw[key] = json.loads(raw[key])
    if isinstance(raw['outcome'], list):
        raw['outcome'] = dict(zip(('id', 'platform_id', 'name', 'index'), raw['outcome']))
    for key in ('bids', 'asks'):
        raw[key] = [dict(zip(('price', 'size'), v)) if isinstance(v, list) else v for v in raw[key]]
    raw['path_index'] = int(raw['path_index'])
    raw['hash'] = str(raw['hash'])
    return raw


def select_pair(rows, ticker, at, engine):
    """Latest event/path before quality, all co-ties; no older-good fallback."""
    try:
        values = [_row(r) for r in rows if r['market_platform_id'] == ticker and r['platform'] == 'KALSHI'
                  and native_time(r['timestamp']) <= at]
        if not values:
            raise ValueError('MISSING_SELECTED_PAIR')
        rank = max((native_time(r['timestamp']), r['path_index']) for r in values)
        selected = [r for r in values if (native_time(r['timestamp']), r['path_index']) == rank]
        if len({r['market_id'] for r in selected}) != 1:
            raise ValueError('NATIVE_MARKET_IDENTITY_CONFLICT')
        sides = {}
        for side in ('YES', 'NO'):
            items = [r for r in selected if str(r['outcome']['platform_id']).upper() == side]
            if not items:
                raise ValueError('MISSING_LATEST_ATOMIC_' + side)
            canonical = [json.dumps({k: v for k, v in r.items() if k not in ('indexed_at', '_source_locator')}, sort_keys=True) for r in items]
            if len(set(canonical)) != 1:
                raise ValueError('CONFLICTING_COEVENT_COPATH_NATIVE_PAYLOADS_' + side)
            sides[side] = max(items, key=lambda r: native_time(r['indexed_at']))
        y, n = sides['YES'], sides['NO']
        if y['outcome']['id'] == n['outcome']['id'] or int(y['outcome']['index']) == int(n['outcome']['index']):
            raise ValueError('NATIVE_BINARY_OUTCOME_IDENTITY_CONFLICT')
        for raw in (y, n):
            text = str(raw['hash'])
            if not text.isascii() or not text.isdigit() or not 0 < int(text) <= 2**64 - 1:
                raise ValueError('NATIVE_COMPONENT_FLAG_OR_UINT64_MISMATCH')
        # The accepted provider applies Decimal(value), including JSON float
        # binary values. Decimal(str(float)) would silently repair Oct9/10
        # non-mirrored archives and manufacture admissible trades.
        levels = lambda r, key: tuple(engine['Level'](D(x['price']), D(x['size'])) for x in r[key])
        book = engine['physical_book_class'](ticker=ticker, market_id=y['market_id'], event_ns=rank[0],
            path_index=rank[1], yes_indexed_ns=native_time(y['indexed_at']), no_indexed_ns=native_time(n['indexed_at']),
            yes_state=y['state'], no_state=n['state'], yes_continuity=y['continuity'], no_continuity=n['continuity'],
            yes_hash=y['hash'], no_hash=n['hash'], yes_bids=levels(y, 'bids'), yes_asks=levels(y, 'asks'),
            no_bids=levels(n, 'bids'), no_asks=levels(n, 'asks'), source_ref='FULL_NATIVE_ORIGINAL_LATEST_PAIR')
        admission = engine['validate_snapshot'](book, at, 5)
        if not admission.valid:
            raise ValueError(admission.reason)
        if not book.no_asks:
            raise ValueError('ABSENT_OR_NONCENT_TOP_PRICE')
        bid, ask = book.no_bids[0].price if book.no_bids else None, book.no_asks[0].price
        if any(p * 100 != (p * 100).to_integral_value() for p in (bid, ask) if p is not None):
            raise ValueError('NATIVE_CENT_PRICE_REQUIRED')
        return {'strict_usable': True, 'strict_failure_reasons': [],
            'market_ticker': ticker, 'contract_side': 'NO', 'quote_at_utc': datetime.fromtimestamp(rank[0] // NS, timezone.utc).isoformat(),
            'quote_age_ms': str(D(at - rank[0]) / D(10**6)), 'best_bid_cents': None if bid is None else int(bid * 100),
            'best_ask_cents': int(ask * 100), 'displayed_ask_quantity_contracts': str(book.no_asks[0].size),
            'native_event_ns': rank[0], 'native_yes_indexed_ns': book.yes_indexed_ns,
            'native_no_indexed_ns': book.no_indexed_ns, 'path_index': rank[1],
            'first_historical_delivery_proven': False}
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return {'strict_usable': False, 'strict_failure_reasons': [str(exc)], 'market_ticker': ticker,
            'contract_side': 'NO', 'quote_at_utc': None, 'quote_age_ms': None, 'best_bid_cents': None,
            'best_ask_cents': None, 'displayed_ask_quantity_contracts': None}


def _legacy_rows(day, at, pins):
    if day <= '2026-10-08':
        folder = FROZEN / 'sources/month-10/all-six-native-v1'
        index, payload = folder / 'quote-index.sqlite3', folder / 'selected-native-rows.sqlite3'
        for path, sha in ((index, QINDEX_SHA), (payload, ROWS_SHA)):
            pins.append(pin(path, sha))
        output = []
        with sqlite3.connect(index.as_uri() + '?mode=ro', uri=True) as db, sqlite3.connect(payload.as_uri() + '?mode=ro', uri=True) as cache:
            pairs = db.execute('SELECT pair_json FROM pairs WHERE climate_date=? AND asof_ns=? AND lane=?', (day, at, 'PHYSICAL_1800')).fetchall()
            for (text,) in pairs:
                pair = json.loads(text)
                if pair['source_state'] != 'PENDING_NESTED_VALIDATION':
                    continue
                for locs in pair['locators'].values():
                    for loc in locs:
                        key = f"{loc['query_id']}:{loc['row_group']}:{loc['row_in_group']}"
                        item = cache.execute('SELECT payload_json FROM rows WHERE source_key=?', (key,)).fetchone()
                        if item is None:
                            raise ValueError('Exact native cache locator absent')
                        output.append(json.loads(item[0]))
        return output
    if day <= '2026-10-11':
        path = CAMPAIGN / 'sources/october-tail-books-v1.json'
        pins.append(pin(path, TAIL_SHA))
        return [r for group in read(path)['rows'] if group['climate_date'] == day and group['asof_ns'] == at
                for r in group['all_native_coevent_payloads']]
    return []


def official_contracts(markets, day, mia):
    if len(markets) != 6:
        raise ValueError('SIX_OFFICIAL_MARKETS_REQUIRED')
    layout = []
    for m in markets:
        kind = m['strike_type']
        if kind == 'less':
            lo, hi = None, m['cap_strike']
        elif kind == 'greater':
            lo, hi = D(str(m['floor_strike'])) + 1, None
        elif kind == 'between':
            lo, hi = m['floor_strike'], D(str(m['cap_strike'])) + 1
        else:
            raise ValueError('UNSUPPORTED_NATIVE_STRIKE_SEMANTICS')
        layout.append({'ticker': m['ticker'], 'lower': None if lo is None else int(lo), 'upper': None if hi is None else int(hi)})
        if lo is not None and D(str(lo)) != D(int(lo)) or hi is not None and D(str(hi)) != D(int(hi)):
            raise ValueError('INTEGER_FAHRENHEIT_NATIVE_STRIKES_REQUIRED')
    return mia.contracts(layout, day)


def _legacy_day(day, mia, pins):
    path = CAMPAIGN / 'sources/october-packet-v1.json'
    pins.append(pin(path, PACKET_SHA))
    lane = read(path)['lanes']['PHYSICAL_1800']
    windows = [r for r in lane['windows'] if r['climate_date'] == day]
    payouts = {r['ticker']: r for r in lane['payouts'] if r['climate_date'] == day}
    frame_path = FROZEN / ('weather/daily/' + day + '.json') if day <= '2026-10-08' else CAMPAIGN / ('sources/oct-tail/joined-weather/' + day + '.json')
    if day <= '2026-10-08':
        manifest = read(FROZEN / 'FROZEN-MANIFEST.json')
        relative = frame_path.relative_to(FROZEN).as_posix()
        pins.append(pin(frame_path, next(r['sha256'] for r in manifest['files'] if r['path'] == relative)))
        layout_path = mia.BUNDLE / 'dated-layouts.json'
        binding = next(r for r in manifest['files'] if r['path'] == layout_path.relative_to(FROZEN).as_posix())
        pins.append(pin(layout_path, binding['sha256']))
        layouts = read(layout_path)['dates'][day]['contracts']
        bounds = mia.operators()['v10_contract_reference'].integer_bounds(layouts)
        native = mia.contracts([{'ticker': r['ticker'], 'lower': lo, 'upper': None if hi is None else hi + 1} for r, (lo, hi) in zip(layouts, bounds)], day)
    else:
        manifest = read(CAMPAIGN / 'sources/october-source-pins-v1.json')
        binding = next(r for r in manifest['pins'] if str(Path(r['path']).resolve()).lower() == str(frame_path.resolve()).lower())
        pins.append(pin(frame_path, binding['sha256']))
        layouts = read(frame_path)['contracts']
        bounds = mia.operators()['v10_contract_reference'].integer_bounds(layouts)
        native = mia.contracts([{'ticker': r['ticker'], 'lower': lo, 'upper': None if hi is None else hi + 1} for r, (lo, hi) in zip(layouts, bounds)], day)
    frame = read(frame_path)
    mia.operators()['v10_contract_reference'].verify_seal(frame)
    if frame['climate_date'] != day or frame['station_id'] != 'KMIA' or frame['decision_ns'] != mia.decision_ns(day):
        raise ValueError('OWN_NATIVE_WEATHER_CLOCK_REQUIRED')
    return {'contracts': native, 'frame': frame, 'windows': windows, 'payouts': payouts}


def _weather_proxy(mia, packet, day):
    """Real values and receipts preserved; archive availability is a named proxy."""
    at = native_time(datetime.now(timezone.utc).isoformat())
    mia._verified_fields(packet['fields'], day, at)
    fields = deepcopy(packet['fields'])
    for row in fields:
        row['actual_local_available_ns'] = row['available_ns']
        proxy = row['issue_ns'] + 6 * 3600 * NS
        if row['receipt'].get('last_modified'):
            proxy = max(proxy, native_time(parsedate_to_datetime(row['receipt']['last_modified']).isoformat()))
        row['available_ns'] = proxy
        row['source_ref'].update(historical_availability_basis='MAX_ISSUE_PLUS6H_AND_NOAA_LAST_MODIFIED_PROXY', historical_first_delivery_proven=False)
    frame = mia._frame(fields, day)
    frame['asof_ns'] = frame['decision_ns']
    frame['available_ns'] = max(r['available_ns'] for r in fields)
    if any(r['available_ns'] >= frame['decision_ns'] for r in fields):
        raise ValueError('ARCHIVE_PROXY_NOT_BEFORE_NATIVE_DECISION')
    return frame


def _weather_paths(packet, packet_path, extra=()):
    paths = {str(Path(packet_path).resolve()), *[str(Path(p).resolve()) for p in extra]}
    for field in packet.get('fields', []):
        for key in ('receipt', 'index_receipt'):
            paths.add(str(Path(field[key]['raw_path']).resolve()))
    for row in packet.get('source_parent_pins', []):
        pin(row['path'], row['sha256'])
        paths.add(row['path'])
    return sorted(paths)


def _acquire_weather(day, release_path, runtime_root, progress=None):
    mia = _adapter(release_path, runtime_root)
    mia.day_check(day)
    directory = Path(runtime_root) / 'sources/miami' / day / 'weather'
    directory.mkdir(parents=True, exist_ok=True)
    packet_path = directory / 'packet.json'
    if packet_path.is_file():
        packet = read(packet_path)
        try:
            frame = _weather_proxy(mia, packet, day)
            return {'status': 'AVAILABLE', 'frame': frame, 'packet_path': str(packet_path), 'source_paths': _weather_paths(packet, packet_path)}
        except (ValueError, KeyError, OSError):
            pass
    # Reuse real live captures where their exact byte/field identity passes.
    # Copy response bytes into the isolated history runtime because the frozen
    # decoder deliberately refuses receipt paths escaping its configured root.
    runtime = Path(runtime_root).resolve()
    live_root = runtime.parent if runtime.name == 'offline-history' else runtime
    candidates = sorted((live_root / 'miami/weather' / day).glob('*/packet.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for previous in candidates[:8]:
        packet = read(previous)
        if packet.get('climate_date') != day or not packet.get('complete') or len(packet.get('fields', [])) != 44:
            continue
        relocated = deepcopy(packet)
        folder = directory / ('reused-' + uuid.uuid4().hex)
        folder.mkdir()
        try:
            source_parents = [pin(previous)]
            for field in relocated['fields']:
                for key in ('receipt', 'index_receipt'):
                    receipt = field[key]
                    old = Path(receipt['raw_path']).resolve()
                    if not old.is_relative_to(live_root):
                        raise ValueError('LIVE_CAPTURE_ESCAPES_RUNTIME')
                    source_parents.append(pin(old, receipt['response_sha256']))
                    target = folder / (receipt['response_sha256'] + '.raw')
                    target.write_bytes(old.read_bytes())
                    receipt['raw_path'] = str(target)
            relocated['source_parent_pins'] = source_parents
            frame = _weather_proxy(mia, relocated, day)
            save(packet_path, relocated)
            return {'status': 'AVAILABLE', 'frame': frame, 'packet_path': str(packet_path),
                    'source_paths': _weather_paths(relocated, packet_path, [previous]), 'reused_actual_capture': True}
        except (ValueError, KeyError, OSError):
            continue
    w = mia._weather_functions()
    folder = directory / uuid.uuid4().hex
    folder.mkdir()
    fields, errors = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(w['capture'], row, folder): row for row in w['plan'](day)}
        for job in as_completed(jobs):
            try:
                fields.append(w['decode'](*job.result()))
            except Exception as exc:
                errors.append({'object_id': jobs[job]['object_id'], 'reason': type(exc).__name__})
            if progress:
                progress({'city': 'miami', 'phase': 'WEATHER_ARCHIVE', 'day': day, 'completed_fields': len(fields), 'required_fields': 44, 'errors': len(errors)})
    packet = {'climate_date': day, 'fields': sorted(fields, key=lambda r: r['object_id']), 'complete': len(fields) == 44,
              'errors': errors, 'actual_receipts_retained': True, 'new_fits': 0, 'source_mode': 'LATE_ARCHIVAL_HTTP_RECEIPTS'}
    save(packet_path, packet)
    if not packet['complete']:
        return {'status': 'PENDING', 'reason': 'NATIVE_44_FIELD_ARCHIVE_INCOMPLETE', 'source_paths': [str(packet_path)], 'errors': errors}
    try:
        return {'status': 'AVAILABLE', 'frame': _weather_proxy(mia, packet, day), 'packet_path': str(packet_path), 'source_paths': _weather_paths(packet, packet_path)}
    except ValueError as exc:
        return {'status': 'UNKNOWN', 'reason': str(exc), 'source_paths': [str(packet_path)]}


def _new_day(day, acquired, mia, release_path, runtime_root, pins, progress):
    market_path = acquired.get('market_path')
    if not market_path:
        raise ValueError('OFFICIAL_SOURCE_PENDING')
    pins.append(pin(market_path))
    markets = read(market_path)['markets']
    native = official_contracts(markets, day, mia)
    windows, payouts = [], {}
    for m in markets:
        windows.append({'ticker': m['ticker'], 'open_ns': native_time(m['open_time']), 'close_ns': native_time(m['close_time']),
                        'available_ns': native_time(m['created_time']), 'historical_first_delivery_proven': False})
        if m.get('status') in ('settled', 'finalized') and m.get('result') in ('yes', 'no') and m.get('settlement_ts'):
            resolved = native_time(m['settlement_ts'])
            if resolved < native_time(m['close_time']):
                raise ValueError('OFFICIAL_PAYOUT_BEFORE_CLOSE')
            payouts[m['ticker']] = {'ticker': m['ticker'], 'no_payout': str(int(m['result'] == 'no')),
                'yes_payout': str(int(m['result'] == 'yes')), 'resolution_ns': resolved, 'official_available_ns': resolved,
                'actual_cash_receipt_proven': False, 'source_ref': pin(market_path)}
    if payouts and len(payouts) == 6 and sum(int(p['yes_payout']) for p in payouts.values()) != 1:
        raise ValueError('OFFICIAL_SINGLE_BINARY_WINNER_REQUIRED')
    weather = _acquire_weather(day, release_path, runtime_root, progress)
    for path in weather.get('source_paths', []):
        pins.append(pin(path))
    if weather['status'] != 'AVAILABLE':
        raise ValueError(weather.get('reason', 'WEATHER_ARCHIVE_PENDING'))
    return {'contracts': native, 'frame': weather['frame'], 'windows': windows, 'payouts': payouts}


def _source_days(days, mia, engine, release_path, runtime_root, progress):
    from tempdesk.offline_sources import acquire_day
    contexts, pins, requests = {}, [], []
    for day in days:
        context = {'error': None, 'source_paths': [], 'quote_rows': {}, 'arrival_rows': {}}
        local_pins = []
        try:
            mia.day_check(day)
            if native_time(day + 'T14:00:05Z') > native_time(datetime.now(timezone.utc).isoformat()):
                raise ValueError('DECISION_OR_ARRIVAL_NOT_YET_REACHED')
            if day <= '2026-10-11':
                context.update(_legacy_day(day, mia, local_pins))
            at = mia.decision_ns(day)
            captured = {at: _legacy_rows(day, at, local_pins), at + 5 * NS: _legacy_rows(day, at + 5 * NS, local_pins)}
            need_acquire = day > '2026-10-11'
            if not need_acquire:
                need_acquire = any(not select_pair(captured[clock], contract['ticker'], clock, engine)['strict_usable']
                    for clock in captured for contract in context['contracts'])
            acquired = None
            if need_acquire:
                if progress:
                    progress({'city': 'miami', 'phase': 'ARCHIVE_SOURCES', 'day': day})
                acquired = acquire_day('miami', day, runtime_root)
                if acquired.get('errors'):
                    requests.append({'city': 'miami', 'day': day, 'kind': 'NATIVE_ARCHIVE', 'reason': acquired['errors'], 'source_status': acquired['status']})
                if day > '2026-10-11':
                    context.update(_new_day(day, acquired, mia, release_path, runtime_root, local_pins, progress))
                if acquired.get('books_path'):
                    local_pins.append(pin(acquired['books_path']))
                    rows = [json.loads(line) for line in Path(acquired['books_path']).read_text().splitlines() if line.strip()]
                    # A new, complete full-day source owns latest selection.
                    # Incomplete capture never overwrites an old admitted pair.
                    current_prefix = (day == datetime.now(timezone.utc).date().isoformat() and not acquired.get('errors'))
                    if acquired.get('books_complete') or current_prefix:
                        captured = {clock: rows for clock in captured}
                for key in ('market_receipt_path', 'books_receipt_path'):
                    if acquired and acquired.get(key):
                        local_pins.append(pin(acquired[key]))
            frame = deepcopy(context['frame'])
            frame['contracts'] = context['contracts']
            frame.pop('self_sha256', None)
            frame = mia.operators()['v10_contract_reference'].seal(frame)
            mia.operators()['v10_contract_reference'].validate_frame(frame)
            context['frame'] = frame
            context['forecasts'] = {name: mia._calculate(frame, context['contracts'], name) for name in PROFILES}
            for name, clock in (('quote_rows', at), ('arrival_rows', at + 5 * NS)):
                context[name] = {(day, c['ticker'], 'NO'): select_pair(captured[clock], c['ticker'], clock, engine) for c in context['contracts']}
        except (ValueError, KeyError, OSError, TypeError) as exc:
            context['error'] = str(exc)
            requests.append({'city': 'miami', 'day': day, 'kind': 'DAY_INPUTS', 'reason': str(exc), 'status': 'PENDING'})
        context['source_paths'] = sorted({r['path'] for r in local_pins})
        pins.extend(local_pins)
        contexts[day] = context
    return contexts, pins, requests


def _wallet(days, contexts, name, mia):
    """Original P09 policy, separate known-subset and unidentified full path."""
    cash, reserved, origin_total = D(100), D(0), D(0)
    total_fee, contracts, wins, losses = D(0), 0, 0, 0
    holdings, records, ledger = [], [], []
    tainted, execution_lock = False, False

    def release(at):
        nonlocal cash
        for hold in holdings:
            if not hold.get('paid') and hold.get('resolution_ns') is not None and hold['resolution_ns'] <= at:
                cash += hold['payout']
                hold['paid'] = True
                ledger.append({'kind': 'OFFICIAL_BINARY_PAYOUT', 'origin_day': hold['day'], 'ticker': hold['ticker'], 'at_ns': hold['resolution_ns'], 'amount': str(hold['payout']), 'cash': str(cash)})

    for day in days:
        at = native_time(day + 'T14:00:00Z')
        local_end = datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), ZoneInfo('America/New_York'))
        cutoff = min(native_time(local_end.isoformat()), native_time(datetime.now(timezone.utc).isoformat()))
        calendar_before = cash
        release(at)
        before = cash
        context = contexts[day]
        reasons, status, origin_net = [], 'COMPLETE', D(0)
        fees_day, count_day, wins_day, losses_day = D(0), 0, 0, 0
        winning_orders, losing_orders, unsettled_orders = 0, 0, 0
        unknown_opportunity = False
        if context.get('error'):
            status, unknown_opportunity = 'PENDING', True
            reasons.append(context['error'])
        elif execution_lock:
            status, unknown_opportunity = 'UNKNOWN', True
            reasons.append('NATIVE_UNKNOWN_EXECUTION_RESERVATION_RISK_LOCK')
        else:
            native, quotes = context['contracts'], context['quote_rows']
            # Explicit creation-time proxy only; retain physical window/close.
            valid_windows = all(w['available_ns'] <= at and w['open_ns'] <= at < w['close_ns'] for w in context['windows']) and len(context['windows']) == 6
            if not valid_windows:
                status, unknown_opportunity = 'UNKNOWN', True
                reasons.append('NATIVE_MARKET_WINDOW_UNAVAILABLE')
            else:
                forecast = context['forecasts'][name]
                allocation = mia._plan(day, name, forecast['probabilities_yes'], native, quotes, str(cash), str(max(D(0), cash - reserved)))
                ledger.append({'kind': 'NATIVE_ALLOCATION', 'day': day, 'at_ns': at,
                    'opening_cash': str(cash), 'budget': allocation['budget_dollars'],
                    'reserved_outlay': allocation['reserved_outlay_dollars'],
                    'reason': allocation['reason'], 'unavailable_market_sides': allocation['unavailable_market_sides']})
                if allocation['unavailable_market_sides']:
                    status, unknown_opportunity = 'UNKNOWN', True
                    reasons.append('UNRESOLVED_DECISION_MARKET_OPPORTUNITIES')
                if not allocation['orders'] and not unknown_opportunity:
                    reasons.append(allocation['reason'] or 'NO_FUNDED_WHOLE_CONTRACT')
                reserved += D(allocation['reserved_outlay_dollars'])
                if reserved > cash or D(allocation['reserved_outlay_dollars']) > before * D('.10'):
                    raise ValueError('NATIVE_FEE_INCLUSIVE_CAP_OR_RESERVATION_FAILED')
                for order in allocation['orders']:
                    result = mia._economic_functions()['arrive'](day, order, context['arrival_rows'], D('.10'))
                    outlay = D(order['maximum_reserved_outlay_dollars'])
                    ledger.append({'kind': 'NATIVE_ORDER', 'day': day, 'at_ns': at + 5 * NS, 'order': order, 'result': result})
                    if result['execution_status'] == 'UNKNOWN_EXECUTION':
                        status, execution_lock, unknown_opportunity = 'UNKNOWN', True, True
                        reasons.append('UNKNOWN_EXECUTION_RESERVED')
                        continue
                    reserved -= outlay
                    if result['execution_status'] == 'NOT_FILLED':
                        continue
                    spent, fee = D(result['fill_entry_outlay_dollars']), D(result['fill_fee_dollars'])
                    cash -= spent
                    if cash < reserved:
                        raise ValueError('RESERVATION_OR_NEGATIVE_CASH_FAILED')
                    count_day += order['quantity']
                    fees_day += fee
                    payout = context['payouts'].get(order['market_ticker'])
                    if payout:
                        proceeds = D(payout['no_payout']) * order['quantity']
                        origin_net += proceeds - spent
                        winning = proceeds - spent > 0
                        winning_orders += int(winning)
                        losing_orders += int(not winning)
                        holdings.append({'day': day, 'ticker': order['market_ticker'], 'payout': proceeds,
                            'resolution_ns': max(payout['resolution_ns'], payout['official_available_ns']), 'paid': False})
                    else:
                        status, unknown_opportunity = 'PENDING', True
                        reasons.append('OFFICIAL_BINARY_SETTLEMENT_PENDING')
                        unsettled_orders += 1
                        holdings.append({'day': day, 'ticker': order['market_ticker'], 'payout': D(0), 'resolution_ns': None, 'paid': False})
        release(cutoff)
        if count_day and not unsettled_orders:
            wins_day, losses_day = int(origin_net > 0), int(origin_net <= 0)
        tainted = tainted or unknown_opportunity
        origin_total += origin_net
        total_fee += fees_day
        contracts += count_day
        wins += wins_day
        losses += losses_day
        records.append({'day': day, 'status': status, 'reason': '; '.join(sorted(set(reasons))) or 'NATIVE_POLICY_EVALUATED',
            'ending_cash': None if tainted or status != 'COMPLETE' else str(cash), 'known_cash': str(cash),
            'net_pnl': None if tainted or status != 'COMPLETE' else str(cash - calendar_before),
            'known_calendar_cash_change': str(cash - calendar_before), 'calendar_cutoff_ns': cutoff,
            'eventual_origin_net': None if unknown_opportunity else str(origin_net),
            'known_eventual_origin_net': str(origin_net), 'eventual_origin_settled_cash': None if tainted else str(D(100) + origin_total),
            'known_eventual_origin_settled_cash': str(D(100) + origin_total),
            'wins': wins_day, 'losses': losses_day, 'win_unit': 'SUPPORTED_FEE_POSITIVE_ORIGIN_TRADE_DAY',
            'winning_orders': winning_orders, 'losing_orders': losing_orders,
            'contracts': count_day, 'fees': str(fees_day), 'reserved': str(reserved),
            'full_history_unidentified': tainted, 'source_paths': context['source_paths'], 'assumptions': list(ASSUMPTIONS)})
    return {'profile_id': name, 'wallet_id': 'miami:' + name, 'initial_cash': '100', 'days': records,
        'ending_cash': records[-1]['ending_cash'] if records else '100', 'known_cash': str(cash),
        'eventual_origin_settled_cash': None if tainted else str(D(100) + origin_total),
        'known_eventual_origin_settled_cash': str(D(100) + origin_total), 'wins': wins, 'losses': losses,
        'contracts': contracts, 'fees': str(total_fee), 'reserved': str(reserved),
        'unpaid_known_positions': sum(not h['paid'] for h in holdings), 'ledger': ledger,
        'full_history_unidentified': tainted, 'assumptions': list(ASSUMPTIONS)}


def _replay(start_day, end_day, release_path, runtime_root, progress=None):
    if start_day != '2026-10-01' or date.fromisoformat(end_day) < date.fromisoformat(start_day):
        raise ValueError('MIAMI_HISTORY_START_MUST_BE_2026_10_01')
    runtime = Path(runtime_root).resolve()
    mia = _adapter(release_path, runtime)
    engine, pins = _engine(runtime)
    days = [(date.fromisoformat(start_day) + timedelta(days=i)).isoformat() for i in range((date.fromisoformat(end_day) - date.fromisoformat(start_day)).days + 1)]
    contexts, source_pins, requests = _source_days(days, mia, engine, release_path, runtime, progress)
    pins += source_pins
    pins += [pin(Path(release_path) / 'manifest.json'), pin(Path(release_path) / 'source-map.json')]
    for binding in mia.dependencies()['files']:
        pins.append(pin(binding['resolved_path'], binding['sha256']))
    output = {'city': 'miami', 'start_day': start_day, 'end_day': end_day,
        'profiles': [_wallet(days, contexts, name, mia) for name in PROFILES], 'requests': requests,
        'source_pins': list({r['path']: r for r in pins}.values()), 'assumptions': ASSUMPTIONS,
        'new_fits': 0, 'actual_orders': 0, 'frozen_profiles_unchanged': True}
    if progress:
        progress({'city': 'miami', 'phase': 'REPLAY_COMPLETE', 'days': len(days), 'profiles': len(PROFILES)})
    return output


def _child(action, args, progress=None):
    """The frozen numerical/source-map environment is isolated from UI threads."""
    env = dict(os.environ)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    deps = 'C:/Users/jarve/Documents/Codex/2026-10-08/cos/work/python-deps'
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + deps
    logs = Path(args['runtime_root']).resolve() / 'worker-logs'
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / ('miami-' + uuid.uuid4().hex + '.stderr.log')
    # Native decoder/traceback messages are drained to a runtime file so an
    # unread PIPE cannot deadlock the source worker. Error text is not sent to
    # the browser; acquisition exceptions omit connection material.
    with log.open('w', encoding='utf8') as stderr:
        run = subprocess.Popen([str(PYTHON), '-B', str(Path(__file__).resolve()), '--child'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=stderr, text=True, env=env)
        run.stdin.write(json.dumps({'action': action, **args}))
        run.stdin.close()
        result = None
        for line in run.stdout:
            item = json.loads(line)
            if item.get('kind') == 'progress' and progress:
                progress(item['value'])
            elif item.get('kind') == 'result':
                result = item['value']
        code = run.wait()
    if code or result is None:
        # No raw credentials, SQL connection material or exception bodies.
        raise RuntimeError('MIAMI_ISOLATED_HISTORY_WORKER_FAILED_' + str(code))
    return result


def replay(start_day, end_day, release_path, runtime_root, progress=None):
    return _child('replay', {'start_day': start_day, 'end_day': end_day,
        'release_path': str(release_path), 'runtime_root': str(runtime_root)}, progress)


def acquire_weather(day, release_path, runtime_root, progress=None):
    return _child('weather', {'day': day, 'release_path': str(release_path), 'runtime_root': str(runtime_root)}, progress)


if __name__ == '__main__':
    request = json.load(sys.stdin)
    action = request.pop('action')
    emit = lambda value: print(json.dumps({'kind': 'progress', 'value': value}, default=str), flush=True)
    result = (_replay(**request, progress=emit) if action == 'replay' else _acquire_weather(**request, progress=emit))
    print(json.dumps({'kind': 'result', 'value': result}, default=str, allow_nan=False), flush=True)
