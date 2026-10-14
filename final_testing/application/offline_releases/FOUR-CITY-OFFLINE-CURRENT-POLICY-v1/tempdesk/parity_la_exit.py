"""LA current exit overlay. Original buys/accounting remain native.

An eventual binary result is never a daily-temperature certificate. This
wrapper only accepts an authentic, pinned TWC producer packet recorded at
its actual receipt time. Missing certificates mean hold, not a guessed sale.
"""
from contextlib import contextmanager
from decimal import Decimal
from .impossible_exit import assess, NS
from .twc import normalize, climate_window
from .parity_policy import LA_EXIT_POLICY

def recorded_packets(source, pin, actual_asof_ns):
    accepted, refs = [], []
    for capture in source.get('completed_report_captures', []):
        # The optional packet is admitted by hash and actual receipt, never by
        # an official payout timestamp or a derived archive availability proxy.
        raw_ref = pin(capture['raw_path'])
        if raw_ref['sha256'] != capture['raw_sha256']:
            raise ValueError('Historical daily report capture changed')
        import json
        receipt_ref = pin(capture['receipt_path'])
        if receipt_ref['sha256'] != capture['receipt_sha256']:
            raise ValueError('Historical report actual receipt changed')
        receipt = json.loads(open(capture['receipt_path'], encoding='utf-8-sig').read())
        if receipt.get('response_sha256') != raw_ref['sha256'] or type(receipt.get('received_ns')) is not int or not 0 < receipt['received_ns'] <= actual_asof_ns:
            raise ValueError('Actual historical report receipt/hash required')
        if capture.get('historical_first_delivery_proven') is not True:
            raise ValueError('A later download cannot prove a pre-close report receipt')
        raw = json.loads(open(capture['raw_path'], encoding='utf-8-sig').read())
        market = capture['market']
        day = market['day']
        if receipt.get('url') != 'https://weather.com/kalshi/api/climate/primary?date='+day:
            raise ValueError('Historical report source URL mismatch')
        # Only actual stored observation inputs are used; no synthetic hourly
        # values are needed to manufacture a final-report proof.
        hourly, hourly_receipts = [], []
        for observation in capture.get('hourly_captures', []):
            ref = pin(observation['raw_path'])
            if ref['sha256'] != observation['receipt']['response_sha256']:
                raise ValueError('Historical hourly producer capture changed')
            hourly.append(json.loads(open(observation['raw_path'], encoding='utf-8-sig').read()))
            hourly_receipts.append(observation['receipt']); refs.append(ref)
        if not hourly:
            raise ValueError('Exact recorded TWC producer inputs required')
        observed = normalize('KLAX', day, hourly, raw, hourly_receipts, receipt, market)
        proof = observed.get('settlement_high_floor')
        if not proof or proof['schema'] != 'SETTLEMENT_DAILY_REPORT_v1':
            raise ValueError('Stored report is not an accepted completed daily report')
        accepted.append({'proof': proof, 'market': market}); refs += [raw_ref,receipt_ref]
    return accepted, refs

def verify_completed_report_window(day, contracts, engine):
    """Known producer clock plus dated contract closes, not price hindsight.

    Under the CURRENT producer no completed-day proof is admitted before the
    climate day ends. If all dated markets close by then, there can be no
    eligible pre-close hard exit. A later close needs authentic trade-life
    evidence and cannot be silently replayed on the old 17–20 UTC book grid.
    """
    _, end = climate_window('KLAX',day)
    earliest = int(end.timestamp())*NS
    closes = [engine.clock(c.get('close_ns',c.get('close_time'))) for c in contracts]
    if len(closes)!=6 or any(close>earliest for close in closes):
        return None
    return dict(earliest_completed_report_ns=earliest,dated_close_ns=closes,
        protection_opportunity='NONE_BEFORE_DATED_MARKET_CLOSE_UNDER_CURRENT_PRODUCER',
        basis='Exact pinned TWC producer completed-day requirement plus dated official market windows')

def protection_scope(prepared):
    packets = prepared.get('recorded_protection_packets', [])
    return {'policy_id': LA_EXIT_POLICY, 'soft_forecast_exit_enabled': False,
        'recorded_completed_reports': len(packets),
        'intraday_temperature_proof': 'RECORDED_PRODUCER_PACKET' if packets else 'UNAVAILABLE',
        'missing_proof_action': 'HOLD_TO_OFFICIAL_SETTLEMENT',
        'archive_monitoring_scope': 'Saved native entry/monitoring grid only; no invented full trade-life books',
        'completed_report_window_proof': prepared.get('completed_report_window_proof'),
        'exact_live_delivery_equivalence': False}

@contextmanager
def current_exit(replay, portfolio, prepared):
    """Scoped subclass changes request_exit only; restore original afterwards."""
    original = replay.Portfolio
    engine = prepared['runtime']['engine']
    protection = (prepared.get('recorded_protection_packets', []), prepared)
    class CurrentCompletedReportPortfolio(original):
        _current_live_exit_policy = LA_EXIT_POLICY
        def request_exit(self, sid, key, book, fee, forecast, contracts, now):
            now = self._time(now)
            packets, registered = self._current_protection
            wallet = self.wallets[sid]; pos = wallet.positions.get(key)
            if not pos or pos.quantity <= 0:
                return self._record_decision(sid, now, 'SKIP', 'NO_POSITION')
            eligible = [p for p in packets if p['proof']['received_ns'] <= now and p['proof']['day'] == pos.climate_date]
            if not eligible:
                return self._record_decision(sid, now, 'SKIP', 'COMPLETED_REPORT_PROOF_UNAVAILABLE_HOLD')
            packet = max(eligible, key=lambda p:p['proof']['received_ns'])
            market = packet['market']; contract = next((c for c in market['contracts'] if c['ticker'] == pos.ticker), None)
            if contract is None:
                return self._record_decision(sid, now, 'SKIP', 'DATED_CONTRACT_UNAVAILABLE')
            position = dict(ticker=pos.ticker, side=pos.side, quantity=pos.quantity,
                day=pos.climate_date, first_fill_ns=pos.first_fill_ns or 0)
            signal = assess(position, contract, packet['proof'], market, now)
            if not signal['definitely_impossible']:
                return self._record_decision(sid, now, 'SKIP', signal['reason'])
            if any(o.strategy_id == sid and o.status in ('PENDING','UNKNOWN_EXECUTION') for o in self.orders.values()):
                return self._record_decision(sid, now, 'SKIP', 'PENDING_OR_UNRESOLVED_NATIVE_ORDER')
            valid, reason = engine.validate_book(book, now, self.config.max_quote_age_s)
            if not valid or not fee.validate(now):
                return self._record_decision(sid, now, 'UNKNOWN', 'HARD_EXIT_BOOK_OR_FEE_UNAVAILABLE:'+str(reason))
            bids = book.ladder(pos.side, 'SELL')
            if not bids:
                return self._record_decision(sid, now, 'SKIP', 'NO_BUYERS')
            top = bids[0]
            quantity = min(pos.quantity, self._capacity(book, pos.side, 'SELL', top))
            if quantity <= 0 or top.price <= 0 or fee.sell_amount([(quantity, top.price)])[0] <= 0:
                return self._record_decision(sid, now, 'SKIP', 'NO_POSITIVE_SUPPORTED_BUYERS')
            native_contract = next(c for c in contracts if c.ticker == pos.ticker)
            if not native_contract.open_ns <= now < native_contract.close_ns:
                return self._record_decision(sid, now, 'SKIP', 'MARKET_NOT_OPEN')
            self.sequence += 1
            order = engine.Order(f'F2-{self.sequence:06}', sid, 'SELL', native_contract,
                pos.side, quantity, top.price, Decimal(0), now,
                now+self.config.latency_s*NS, Decimal(0), None, 0)
            self.orders[order.order_id] = order
            return self._record_decision(sid, now, 'SUBMITTED_PAPER', 'OFFICIAL_COMPLETED_DAILY_REPORT_INVALIDATES_POSITION',
                order_id=order.order_id, policy_id=LA_EXIT_POLICY, proof_sha256=signal['proof_sha256'],
                actual_historical_fill_proven=False)
    CurrentCompletedReportPortfolio._current_protection = protection
    if portfolio is not None:
        if getattr(portfolio, '_current_live_exit_policy', None) != LA_EXIT_POLICY:
            raise ValueError('A legacy soft-exit portfolio cannot enter the current-policy history')
        portfolio._current_protection = protection
    replay.Portfolio = CurrentCompletedReportPortfolio
    try:
        yield replay
    finally:
        replay.Portfolio = original
