"""Chronological LA archive replay, isolated from the online paper wallets.

The saved native traders own all decisions and money. This facade only binds
the reviewed sources, records evidence gaps and exposes their cash ledgers.
It neither calls a trading exchange nor fits or changes a weather model.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import os
import time
from zoneinfo import ZoneInfo

LA_ROOT = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Los Angeles')
APP_ROOT = Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard')
REVIEW_PATH = APP_ROOT / 'runtime/offline-preparation-20261012/la-offline-independent-review-v1.json'
PROFILES = ('F3_V10_PHYSICAL_FORECAST_EXIT', 'F6_HYBRID_STAGED5', 'V8D_NO18_SETTLEMENT')
NS = 10**9
MODE = 'CONDITIONAL_ARCHIVE_REPLAY'
STRICT_STATUS = 'UNPROVED_HISTORICAL_FIRST_DELIVERY_FILL_AND_TARGET_EQUIVALENCE'
ASSUMPTIONS = [
    'CONDITIONAL_ARCHIVE_REPLAY: original archived source clocks and native admission rules apply; missing book states are never interpolated.',
    'Historical first delivery and exchange fills are unproved. These are hypothetical separate wallets, not nine simultaneously proven fills.',
    'The frozen physical NWS KLAX forecasts are compared with official binary market payouts. Equivalence with The Weather Company settlement target is unproved.',
    'Each saved LA trader starts with $100 on October 1. Native sizing, fees, whole contracts, partial fills, exits, risk latches and official payout clocks remain unchanged.',
    'F3 and F6 retain their original 17/18 UTC forecast packets, 60-second source age, half displayed depth and retail-cent fees; V8D retains NO at18 UTC, 1800-second source age, full depth and raw4dp fees.',
    'Current October model states were created after some replay dates. Applying them to earlier October is a retrospective fixed-state counterfactual, not a historical deployment or unseen holdout.',
    'Known calendar cash is separate from complete ending return. A missing economic day or unresolved execution prevents a claimed compounded balance on later days.',
]


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _pin(path):
    path = Path(path).resolve()
    before = path.stat()
    digest = sha256(path.read_bytes()).hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed while being pinned: ' + str(path))
    return {'path': str(path), 'sha256': digest, 'bytes': after.st_size}


def days_between(start_day, end_day):
    start, end = date.fromisoformat(start_day), date.fromisoformat(end_day)
    today = datetime.now(ZoneInfo('America/Los_Angeles')).date()
    if start_day != start.isoformat() or end_day != end.isoformat() or start != date(2026, 10, 1) or end < start or end > today:
        raise ValueError('LA offline wallets must start October 1, 2026 and end no later than today')
    return [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)]


def _review_gate(release_path):
    """A reviewed new facade never bypasses the existing accepted source seal."""
    from .bridge import Release
    frozen = Release(release_path)
    profiles = {p['profile_id']: p for p in frozen.manifest['profiles'] if p['city'] == 'la'}
    if set(profiles) != set(PROFILES) or any(str(p.get('initial_cash')) != '100' for p in profiles.values()):
        raise ValueError('Offline profiles differ from the frozen LA trio')
    review_path = Path(os.environ.get('OFFLINE_LA_REVIEW_PATH', str(REVIEW_PATH))).resolve()
    if os.environ.get('OFFLINE_LA_REVIEW_PATH'):
        copied_root = Path(__file__).resolve().parents[1]
        manifest_path = copied_root/'offline-manifest.json'
        manifest = _read(manifest_path)
        pinned_manifest_sha = os.environ.get('OFFLINE_RELEASE_MANIFEST_SHA256')
        if not pinned_manifest_sha or _pin(manifest_path)['sha256'] != pinned_manifest_sha:
            raise ValueError('Copied offline release manifest is not bound to this worker')
        if not review_path.is_relative_to(copied_root/'evidence'):
            raise ValueError('Copied LA review must be inside the sealed offline evidence folder')
        listed = {r['path']: r['sha256'] for r in manifest['files']}
        relative = review_path.relative_to(copied_root).as_posix()
        if listed.get(relative) != _pin(review_path)['sha256']:
            raise ValueError('Copied LA review is missing from the sealed offline manifest')
        fee_review_setting = os.environ.get('OFFLINE_LA_FEE_REVIEW_PATH')
        if fee_review_setting:
            fee_review_path = Path(fee_review_setting).resolve()
            if not fee_review_path.is_relative_to(copied_root/'evidence'):
                raise ValueError('Copied LA fee producer review must stay inside sealed offline evidence')
            fee_relative = fee_review_path.relative_to(copied_root).as_posix()
            if listed.get(fee_relative) != _pin(fee_review_path)['sha256']:
                raise ValueError('Copied LA fee producer review is not bound to the sealed manifest')
        if Path(manifest['model_release_path']).resolve() != frozen.root or manifest['model_manifest_sha256'] != frozen.manifest_sha256:
            raise ValueError('Offline review is bound to a different saved model release')
    review = _read(review_path)
    if review.get('passes') is not True or review.get('scope') != 'FROZEN_LA_OFFLINE_OCTOBER_NATIVE_TRIO':
        raise ValueError('LA offline independent review is missing or has not passed')
    expected = {str(Path(p['path']).resolve()).casefold(): p['sha256'] for p in review.get('code_pins', [])}
    refs = []
    for name in ('offline_la.py', 'offline_la_native.py', 'offline_la_weather.py', 'offline_la_acquisition.py', 'offline_la_fee.py', 'offline_sources.py'):
        ref = _pin(Path(__file__).parent/name)
        original = str((APP_ROOT/'tempdesk'/name).resolve()).casefold()
        if expected.get(original) != ref['sha256']:
            raise ValueError('LA offline reviewed source changed: ' + name)
        refs.append(ref)
    for expected_ref in review.get('dependency_pins', []):
        ref = _pin(expected_ref['path'])
        if ref['sha256'] != expected_ref['sha256']:
            raise ValueError('Reviewed LA native source changed before import: '+ref['path'])
        refs.append(ref)
    refs += [_pin(review_path), _pin(frozen.root/'manifest.json')]
    return frozen, refs


def request_for(day, reason, profile_id):
    # These exact-minute cutoffs describe missing evidence; they do not make a
    # later REST receipt available at an earlier historical decision.
    from datetime import timezone
    base = datetime.fromisoformat(day).replace(hour=17, tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    stamp = base-epoch
    begin = (stamp.days*86400 + stamp.seconds)*NS
    times = [begin+i*60*NS for i in range(181)] if profile_id != PROFILES[2] else [begin+3600*NS]
    return {'city': 'la', 'day': day, 'profile_id': profile_id, 'series': 'KXHIGHLAX',
            'components': ['DATED_SIX_MARKET_DEFINITIONS', 'OFFICIAL_BINARY_PAYOUTS', 'NATIVE_ATOMIC_YES_NO_BOOKS', 'FROZEN_NATIVE_HRRR_GEFS_METAR_FORECASTS'],
            'cutoffs_ns': sorted(times+[t+5*NS for t in times]), 'reason': reason,
            'read_only': True, 'new_fits': 0, 'historical_receipt_backdating_allowed': False,
            'settlement_target_equivalence_required_for_live': True}


def _empty_day(day, *, cash, reserved, status, reason, source_paths=(), assumptions=()):
    return {'day': day, 'status': status, 'reason': reason, 'ending_cash': None,
            'known_cash': str(cash), 'net_pnl': None, 'wins': 0, 'losses': 0,
            'eventual_origin_net': None, 'known_eventual_origin_net': None,
            'contracts': 0, 'fees': '0', 'reserved': str(reserved),
            'source_paths': list(source_paths), 'assumptions': list(assumptions),
            'strict_evidence_status': STRICT_STATUS, 'mode': MODE,
            'target_warning': 'TWC_OFFICIAL_PAYOUT_VS_NWS_PHYSICAL_FORECAST_EQUIVALENCE_UNPROVED',
            'replay_executed': False}


def _money(value, field):
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError('Invalid native ' + field)
    return number


def _missing_status(day, profile_id):
    """An unfinished clock is pending; an elapsed missing clock is unknown."""
    last_hour = 18 if profile_id == PROFILES[2] else 20
    end = datetime.fromisoformat(day).replace(hour=last_hour, second=5, tzinfo=timezone.utc)
    return 'PENDING' if datetime.now(timezone.utc) < end else 'UNKNOWN'


def _run_profiles(days, weather, native, context, runtime_root, progress=None):
    """Thin transport over the exact native traders, also exercised by fixtures."""
    profiles, requests, pins = [], [], {}
    for pid in PROFILES:
        state = None
        cash, reserved = Decimal(100), Decimal(0)
        daily, ledger = [], []
        stop_reason = None
        extras = []
        plans = {}
        # Inventory every requested calendar day even if an earlier money gap
        # prevents running its wallet. The next click can therefore acquire all
        # missing sources instead of discovering only one gap at a time.
        for day in days:
            if day[:7] != '2026-10':
                reason = 'FROZEN_OCTOBER_CHECKPOINT_HAS_NO_AUTHORIZED_NEW_MONTH_FIT'
                plans[day] = {'missing': reason, 'status': 'PENDING'}
                requests.append(request_for(day, reason, pid))
                continue
            if progress:
                progress({'city': 'la', 'profile_id': pid, 'phase': 'source_inventory', 'day': day})
            packet = None
            try:
                packet = weather.load_forecasts(day, pid, LA_ROOT, runtime_root)
                for ref in packet.get('source_pins', []):
                    pins[str(Path(ref['path']).resolve())] = ref
                if not packet.get('rows'):
                    raise native.EvidencePending(packet.get('reason', 'MISSING_FROZEN_NATIVE_FORECAST'))
                prepared = native.prepare(day, pid, packet['rows'], context, runtime_root)
                for ref in packet.get('source_pins', [])+prepared.get('source_pins', []):
                    pins[str(Path(ref['path']).resolve())] = ref
                plans[day] = {'packet': packet, 'prepared': prepared}
            except (native.EvidencePending, weather.EvidencePending, FileNotFoundError) as error:
                reason = str(error)
                status = _missing_status(day, pid)
                plans[day] = {'missing': reason, 'status': status,
                              'source_paths': [ref['path'] for ref in (packet or {}).get('source_pins', [])]}
                requests.append(request_for(day, reason, pid))
                if progress:
                    progress({'city': 'la', 'profile_id': pid, 'phase': 'source_gap', 'day': day, 'status': status, 'reason': reason})
        for day in days:
            plan = plans[day]
            if plan.get('missing'):
                reason = plan['missing']
                daily.append(_empty_day(day, cash=cash, reserved=reserved, status=plan['status'], reason=reason,
                    assumptions=ASSUMPTIONS+extras, source_paths=plan.get('source_paths', [])))
                stop_reason = stop_reason or reason
                continue
            packet, prepared = plan['packet'], plan['prepared']
            extras = list(dict.fromkeys(extras+packet.get('assumptions', [])))
            source_paths = [ref['path'] for ref in packet.get('source_pins', [])+prepared.get('source_pins', [])]
            if stop_reason:
                daily.append(_empty_day(day, cash=cash, reserved=reserved, status='UNKNOWN',
                    reason='EARLIER_UNRESOLVED_ECONOMIC_DAY: '+stop_reason, assumptions=ASSUMPTIONS+extras, source_paths=source_paths))
                continue
            try:
                result = native.run_prepared(prepared, pid, state=state)
            except native.EvidencePending as error:
                reason = str(error)
                details = getattr(error, 'details', {})
                cash = _money(details.get('known_cash', cash), 'known cash')
                reserved = _money(details.get('reserved', reserved), 'reserved')
                daily.append(_empty_day(day, cash=cash, reserved=reserved, status='UNKNOWN', reason=reason,
                    source_paths=source_paths, assumptions=ASSUMPTIONS+extras))
                requests.append(request_for(day, reason, pid))
                stop_reason = reason
                continue
            state = result['state']
            cash = _money(result['cash'], 'cash')
            reserved = _money(result['reserved'], 'reserved')
            for ref in result.get('source_pins', []):
                pins[str(Path(ref['path']).resolve())] = ref
            fee = _money(result['fees'], 'fees')
            count = int(result['contracts'])
            if count != result['contracts'] or count < 0:
                raise ValueError('Native contracts must be a nonnegative whole number')
            unknown = bool(result['unknown_execution'])
            uncertain_selection = bool(result.get('unknown_decisions') or result.get('unsupported_opportunities'))
            resolved = bool(result['financially_resolved']) and not unknown and reserved == 0 and not result['open_contracts']
            # A supported closed trade can have a known conditional cash ledger
            # while alternative opportunities remain unknown. An unsupported
            # no-buy decision cannot be promoted to a confirmed no-trade day.
            selection_unknown_no_buy = uncertain_selection and count == 0
            complete = resolved and not selection_unknown_no_buy
            status = 'COMPLETE' if complete else 'UNKNOWN' if unknown or selection_unknown_no_buy or result.get('unknown_decisions') else 'PENDING'
            reason = None if complete else (
                'UNKNOWN_SUBMITTED_EXECUTION' if unknown else 'UNSUPPORTED_NO_TRADE_EVIDENCE' if selection_unknown_no_buy else
                'MISSING_NATIVE_DECISION_OR_EXIT_EVIDENCE' if result.get('unknown_decisions') else result.get('reason') or 'OFFICIAL_PAYOUT_PENDING')
            row = _empty_day(day, cash=cash, reserved=reserved, status=status, reason=reason,
                source_paths=source_paths, assumptions=ASSUMPTIONS+extras)
            row.update(replay_executed=True, ending_cash=str(cash) if complete else None,
                net_pnl=str(result['net_pnl']) if complete and result.get('net_pnl') is not None else None,
                eventual_origin_net=str(result.get('eventual_origin_net', result.get('net_pnl'))) if complete and result.get('eventual_origin_net', result.get('net_pnl')) is not None else None,
                known_eventual_origin_net=str(result.get('known_eventual_origin_net', result.get('net_pnl'))) if ('known_eventual_origin_net' in result or resolved) and result.get('known_eventual_origin_net', result.get('net_pnl')) is not None else None,
                wins=int(result['wins']), losses=int(result['losses']), contracts=count, fees=str(fee),
                unknown_execution=unknown, unknown_decisions=int(result.get('unknown_decisions', 0)),
                unsupported_opportunities=int(result.get('unsupported_opportunities', 0)),
                open_contracts=int(result['open_contracts']), calendar_cutoff_utc=result.get('calendar_cutoff_utc'),
                known_cash_asof_utc=result.get('calendar_cutoff_utc'),
                cutoff_semantics='Native observation end plus supported official payouts before the next entry; this may differ from calendar midnight',
                calendar_cash=result.get('calendar_cash'),
                calendar_cash_cutoff_utc=result.get('calendar_cash_cutoff_utc'),
                native_financially_resolved=resolved, native_evidence_status=result.get('status'),
                supported_cash_ledger_known=resolved,
                money_known=resolved,
                result_scope='SUPPORTED_EXECUTION_LEDGER_ONLY' if resolved and uncertain_selection else 'CONDITIONAL_NATIVE_REPLAY' if complete else 'INCOMPLETE_EVIDENCE',
                net_label='Conditional supported-trade net after fees' if resolved and uncertain_selection else 'Conditional native net after fees' if complete else 'Unknown complete return',
                opportunity_universe_complete=not uncertain_selection,
                native_continuation_stopped=not complete)
            daily.append(row)
            ledger.append({'day': day, 'events': result.get('ledger', []), 'decisions': result.get('decisions', [])})
            if not complete:
                stop_reason = reason
                requests.append(request_for(day, reason, pid))
            if progress:
                progress({'city': 'la', 'profile_id': pid, 'phase': 'replay', 'day': day,
                          'status': status, 'known_cash': str(cash), 'reason': reason})
        if stop_reason is None:
            expected_cash = Decimal(100)+sum((Decimal(row['net_pnl']) for row in daily), Decimal(0))
            if cash != expected_cash:
                raise ValueError('Offline LA complete-wallet cash and fee-positive episode net do not reconcile')
        profiles.append({'profile_id': pid, 'wallet_id': 'la:'+pid, 'initial_cash': '100',
            'days': daily, 'ending_cash': str(cash) if stop_reason is None else None, 'known_cash': str(cash),
            'wins': sum(row['wins'] for row in daily), 'losses': sum(row['losses'] for row in daily),
            'contracts': sum(row['contracts'] for row in daily),
            'fees': str(sum((Decimal(row['fees']) for row in daily), Decimal(0))), 'reserved': str(reserved),
            'assumptions': ASSUMPTIONS+extras, 'strict_evidence_status': STRICT_STATUS, 'mode': MODE,
            'ledger': ledger, 'history_complete': stop_reason is None, 'first_unresolved_reason': stop_reason,
            'actual_exchange_orders': 0, 'new_fits': 0})
    return {'city': 'la', 'profiles': profiles, 'requests': requests, 'source_pins': list(pins.values()),
            'assumptions': ASSUMPTIONS, 'strict_evidence_status': STRICT_STATUS, 'mode': MODE,
            'paper_only': True, 'new_fits': 0, 'orders_sent': 0}


def replay(start_day, end_day, release_path, runtime_root, progress=None):
    days = days_between(start_day, end_day)
    from .bridge import runtime_guard
    runtime = runtime_guard(release_path, runtime_root)
    frozen, reviewed_pins = _review_gate(release_path)
    from . import offline_la_weather as weather, offline_la_native as native
    # Import and validate originals once per isolated city worker; no online
    # ledger, publication cache or global shared-account state is consulted.
    context = native.load_context(LA_ROOT)
    started = time.time_ns()
    acquisition = []
    # All source collection is read-only and remains outside the accepted
    # release. The backend inventory determines which original captures can
    # be reused and which official results/books need a fresh download.
    from .offline_sources import acquire_day
    for day in days:
        if day[:7] != '2026-10':
            continue
        status = native.source_status(day, context, runtime)
        if status.get('need_acquire'):
            if progress:
                progress({'city': 'la', 'phase': 'read_only_download', 'day': day})
            try:
                receipt = acquire_day('la', day, runtime, force=bool(status.get('force_acquire')), official_only=bool(status.get('official_only')))
                acquisition.append(receipt)
            except (RuntimeError, OSError, ValueError) as error:
                acquisition.append({'city': 'la', 'day': day, 'status': 'PENDING',
                    'error_type': type(error).__name__, 'reason': str(error)})
            native.invalidate_source(day, context)
    fee_admission = None
    fee_days = [day for day in days if '2026-10-12' <= day <= '2026-10-31']
    if fee_days:
        from . import offline_la_fee as fee_helper
        if progress:
            progress({'city': 'la', 'phase': 'conditional_fee_metadata', 'days': fee_days})
        try:
            fee_admission = fee_helper.refresh_temporal_metadata(runtime, climate_days=fee_days)
        except (RuntimeError, OSError, ValueError) as error:
            fee_admission = {'status': 'PENDING', 'error_type': type(error).__name__, 'reason': str(error)}
            if progress:
                progress({'city': 'la', 'phase': 'fee_evidence_gap', 'reason': str(error)})
            # Previously pinned fee periods are still replayable. The native
            # date gate blocks unsupported extensions on the later days.
    from . import offline_la_acquisition as acquisition_helper
    future = {}
    for day in days:
        if not '2026-10-12' <= day <= '2026-10-31':
            continue
        try:
            packet = acquisition_helper.ensure_forecasts(day, LA_ROOT, runtime, progress=progress)
            future[day] = packet
        except (RuntimeError, OSError, ValueError) as error:
            future[day] = {'profiles': {}, 'status': _missing_status(day, PROFILES[0]),
                           'reason': 'NATIVE_ARCHIVE_WEATHER_NOT_READY: '+str(error),
                           'source_pins': [], 'new_fits': 0}
            if progress:
                progress({'city': 'la', 'phase': 'weather_gap', 'day': day,
                          'reason': future[day]['reason']})
    class SourceBoundWeather:
        EvidencePending = weather.EvidencePending
        @staticmethod
        def load_forecasts(day, pid, la_root, runtime_root):
            if day not in future:
                return weather.load_forecasts(day, pid, la_root, runtime_root)
            packet = future[day]
            selected = packet.get('profiles', {}).get(pid)
            if selected is not None:
                return selected
            return {'rows': [], 'reason': packet.get('reason', 'MISSING_NATIVE_FORECAST'),
                    'source_pins': packet.get('source_pins', []), 'assumptions': ASSUMPTIONS}
    output = _run_profiles(days, SourceBoundWeather, native, context, runtime, progress)
    combined = {str(Path(ref['path']).resolve()): ref for ref in reviewed_pins+output['source_pins']+context.get('source_pins', [])}
    output.update(source_pins=list(combined.values()), release_id=frozen.manifest['release_id'],
                  started_ns=started, finished_ns=time.time_ns(),
                  source_acquisition=acquisition,
                  conditional_fee_admission=fee_admission,
                  weather_acquisition=[{'day': day, 'status': packet.get('status'), 'reason': packet.get('reason'),
                      'forecast_packet_pin': packet.get('forecast_packet_pin'),
                      'new_fits': 0} for day, packet in future.items()],
                  offline_wallets_separate_from_online=True,
                  numerical_process_isolation_required=True)
    json.dumps(output, allow_nan=False)
    return output
