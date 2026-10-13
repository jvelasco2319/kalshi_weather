"""New liquidation-only overlay on exact unchanged, mapped LA native state.

The original entry/forecast/settlement gate is retained. This module may create a
SELL only after an irreversible settlement-high-floor certificate passes. No
probability is manufactured to force the old forecast-based exit.
"""
from decimal import Decimal as D
from .impossible_exit import assess, sell_quantity, NS


def hard_exit(native, request):
    profile = request['profile_id']
    rt = native['runtime'](profile); ex = rt['engine']
    body = native['_state'](request, rt); obj = body['object']
    wallet = native['_wallet_check'](request, body)
    now = native['clock'](request['at_ns'])
    if any(o.status in ('PENDING','UNKNOWN_EXECUTION') for o in obj.orders.values()):
        return native['_finish'](request, body, {'status':'HOLD','reason':'PENDING_OR_UNRESOLVED_NATIVE_ORDER','orders':[]})
    market = request['protection_market']; proof = request.get('settlement_high_floor')
    reports, orders = [], []
    legacy = profile == native['PROFILES'][2]
    contracts = None if legacy else native['_contracts'](request, ex)
    for key, pos in wallet.positions.items():
        if pos.quantity <= 0:
            continue
        ticker, side = key
        contract = next((c for c in market['contracts'] if c['ticker']==ticker), None)
        position = dict(ticker=ticker,side=side,quantity=pos.quantity,day=getattr(pos,'climate_date',request['day']),first_fill_ns=getattr(pos,'first_fill_ns',0) or 0)
        if contract is None:
            reports.append(dict(ticker=ticker,status='HOLD',reason='DATED_CONTRACT_UNAVAILABLE'));continue
        signal = assess(position,contract,proof,market,now)
        reports.append({k:v for k,v in signal.items() if k!='proof'})
        if not signal['definitely_impossible']:
            continue
        quote = request.get('quotes',{}).get(ticker)
        fraction = D(1) if legacy else obj.config.queue_capacity_fraction
        proposed, reason = sell_quantity(position,quote,now,fraction,1800 if legacy else obj.config.max_quote_age_s)
        if reason:
            reports[-1].update(status='HOLD',reason=reason);continue
        book = native['_book'](quote,ticker,ex)
        if book is None:
            reports[-1].update(status='HOLD',reason='NATIVE_BOOK_RECEIPT_UNVERIFIED');continue
        if legacy:
            if body['unresolved_legacy']:
                reports[-1].update(status='HOLD',reason='UNRESOLVED_LEGACY_EXECUTION');continue
            intent=ex.OrderIntent(ticker,side,'SELL',D(proposed['limit_price']),quantity=proposed['quantity'],minimum_expected_return=None,reason='IMPOSSIBLE_ONLY_v1')
            if ex.execution_amount([(proposed['quantity'],D(proposed['limit_price']))],'SELL')[0]<=0:
                reports[-1].update(status='HOLD',reason='NO_POSITIVE_NET_PROCEEDS');continue
            created=obj.submit_basket([intent],now)
            order=created[0]
        else:
            fee=native['_fee'](request,ex)
            if not ex.validate_book(book,now,obj.config.max_quote_age_s)[0] or not fee.validate(now):
                reports[-1].update(status='HOLD',reason='NATIVE_EXIT_BOOK_OR_FEE_UNVERIFIED');continue
            top=book.ladder(side,'SELL')[0]
            quantity=min(proposed['quantity'],obj._capacity(book,side,'SELL',top))
            if quantity<=0:
                reports[-1].update(status='HOLD',reason='NATIVE_BUYER_CAPACITY_EXHAUSTED');continue
            if fee.sell_amount([(quantity,top.price)])[0]<=0:
                reports[-1].update(status='HOLD',reason='NO_POSITIVE_NET_PROCEEDS');continue
            obj._time(now);obj.sequence+=1;sid=next(iter(obj.wallets))
            c=next(c for c in contracts if c.ticker==ticker)
            order=ex.Order(f'F2-{obj.sequence:06}',sid,'SELL',c,side,quantity,top.price,D(0),now,now+obj.config.latency_s*NS,D(0),None,0)
            obj.orders[order.order_id]=order
        body.setdefault('impossible_exit_orders',{})[order.order_id]=signal['proof_sha256']
        normalized=native['_order'](order,ex,legacy)
        normalized.update(exit_policy='LA-IMPOSSIBLE-ONLY-EXIT-v1',fee_rate='.07',fee_quantum='.0001' if legacy else '.01',fee_rounding='TOTAL_PROCEEDS_FLOOR' if legacy else 'RETAIL_CENT',source_proof_sha256=signal['proof_sha256'])
        orders.append(normalized)
        # One native pending liquidation at a time; remainder is checked later.
        break
    return native['_finish'](request,body,dict(status='SUBMITTED' if orders else 'HOLD',orders=orders,position_checks=reports,reason='IMPOSSIBLE_ONLY_v1'))


def hard_arrival(native, request):
    rt=native['runtime'](request['profile_id']);body=native['_state'](request,rt)
    oid=request.get('order_id') or request['frozen_order']['order_id']
    if oid not in body.get('impossible_exit_orders',{}):
        raise ValueError('Only a proof-bound hard-exit SELL may use this arrival route')
    obj=body['object'];order=obj.orders[oid]
    action=order.intent.action if request['profile_id']==native['PROFILES'][2] else order.action
    if action!='SELL':
        raise ValueError('Liquidation-only route rejects BUY')
    return native['arrival'](request)
