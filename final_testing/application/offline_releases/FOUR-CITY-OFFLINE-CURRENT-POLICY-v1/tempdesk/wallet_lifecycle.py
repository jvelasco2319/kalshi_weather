"""Paper-only recovery and parity; never replay an old clock or infer a fill.

The service must acquire wallet_transaction before a worker-state round trip.
For arrivals acquire that lock FIRST, then capacity_transaction. Native workers
never acquire either lock. This ordering serializes one wallet's state and the
physical book separately, without locking unrelated forecasts/HTTP preparation.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib,json

SCHEMA='PAPER_ORDER_LIFECYCLE_v1'
LA_SCHEMA='LA_TRIO_NATIVE_POLICY_STATE_v1'

class StaleWalletState(ValueError):pass
class NativeStateMismatch(ValueError):pass

def frozen_hash(order):
    keys=('ticker','side','action','quantity','limit_price','reserved_outlay',
          'arrival_due_ns','actual_decision_ns','native_order','fee_rate','fee_quantum','fee_rounding','exit_policy',
          'native_partial_buy_allowed','quantity_is_native_budget_bound')
    return hashlib.sha256(json.dumps({k:order[k] for k in keys if k in order},sort_keys=True,separators=(',',':')).encode()).hexdigest()

def recovery_plan(state,now_ns):
    """Pure classification: legacy/attempted uncertainty cannot release money."""
    out=[]
    for wid,wallet in state['wallets'].items():
        for oid,order in wallet['orders'].items():
            if order['status'] not in ('PENDING','UNKNOWN'):continue
            life=order.get('lifecycle',{});phase=life.get('phase')
            if order['status']=='UNKNOWN':action='RETAIN_UNKNOWN'
            elif phase=='RESULT_RECORDED':action='COMMIT_RECORDED_RESULT'
            elif life.get('schema')!=SCHEMA or phase not in ('INTENT_RECORDED','ARRIVAL_PREPARING'):
                action='QUARANTINE_UNKNOWN'
            elif frozen_hash(order)!=life.get('frozen_order_sha256'):action='QUARANTINE_UNKNOWN'
            elif now_ns>=order['arrival_due_ns']:action='CANCEL_EXPIRED_UNATTEMPTED'
            elif not life.get('arrival_context'):action='CANCEL_UNRESUMABLE_UNATTEMPTED'
            else:action='RESUME_FUTURE_UNATTEMPTED'
            out.append({'wallet_id':wid,'order_id':oid,'city':wallet['city'],'profile_id':wallet['profile_id'],
                        'action':action,'phase':phase or 'LEGACY_NO_EXECUTION_STAGE',
                        'arrival_due_ns':order['arrival_due_ns'],'actual_recovery_ns':now_ns})
    return out

def _plain(value):
    """Inspect the pinned LA JSON encoding without loading classes or weights."""
    if isinstance(value,list):return [_plain(v) for v in value]
    if not isinstance(value,dict):return value
    if '$decimal' in value:return Decimal(value['$decimal'])
    if '$dict' in value:return {_plain(k):_plain(v) for k,v in value['$dict']}
    if '$tuple' in value:return tuple(_plain(v) for v in value['$tuple'])
    if '$set' in value:return set(_plain(v) for v in value['$set'])
    if '$class' in value:return {**{k:_plain(v) for k,v in value['fields'].items()},'_class':value['$class']}
    if '$capacity' in value:return _plain(value['$capacity'])
    raise NativeStateMismatch('Unknown native state encoding')

def _la_body(state):
    obj=deepcopy(state);expected=obj.pop('sha256',None)
    actual=hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if obj.get('schema')!=LA_SCHEMA or actual!=expected:raise NativeStateMismatch('LA sealed state hash differs')
    return _plain(obj['body'])

def native_view(city,state):
    if city=='ny' and 'orders' in state:
        return {'cash':state['cash'],'reserved':sum((Decimal(str(o['reservation'])) for o in state['orders'].values()
                 if o['status'] in ('PENDING','UNKNOWN_EXECUTION')),Decimal(0)),
                'positions':state.get('positions',{}),'orders':state['orders'],'realized':state.get('realized')}
    if city=='la' and state.get('schema')==LA_SCHEMA:
        body=_la_body(state);native=body['native']
        wallet=native.get('wallet')
        if wallet is None:
            wallets=native.get('wallets',{})
            if len(wallets)!=1:raise NativeStateMismatch('Exactly one LA native wallet is required')
            wallet=next(iter(wallets.values()))
        return {'cash':wallet['cash'],'reserved':wallet.get('reserved',sum(wallet.get('buy_reservations',{}).values(),Decimal(0))),
                'positions':wallet['positions'],'orders':native['orders'],
                'realized':wallet.get('realized_net'),'body':body}
    return None # Miami/Phoenix intentionally keep only decision state; common ledger owns cash.

def native_buy_mode(wallet,order):
    """Widen quantity ONLY where the exact sealed original LA order permits it."""
    if wallet['city']!='la' or wallet.get('policy_state',{}).get('schema')!=LA_SCHEMA:return 'FOK'
    view=native_view('la',wallet['policy_state']);native=view['orders'].get(order['order_id'])
    if native is None:raise NativeStateMismatch('LA frozen order absent from sealed state')
    if order.get('quantity_is_native_budget_bound') is True:
        intent=native.get('intent',{})
        if (native.get('_class')!='PendingOrder' or native.get('quantity') is not None
            or Decimal(str(native.get('budget'))) != Decimal(order['reserved_outlay'])
            or intent.get('ticker')!=order['ticker'] or intent.get('side')!=order['side']
            or intent.get('action')!='BUY' or Decimal(str(intent.get('limit_price')))!=Decimal(order['limit_price'])):
            raise NativeStateMismatch('Unproven variable quantity in original LA budget order')
        return 'NATIVE_BUDGET_BOUND'
    if order.get('native_partial_buy_allowed') is True:
        if (native.get('_class')!='Order' or native.get('quantity')!=order['quantity']
            or Decimal(str(native.get('reservation'))) != Decimal(order['reserved_outlay'])
            or native.get('contract',{}).get('ticker')!=order['ticker'] or native.get('side')!=order['side']
            or native.get('action')!='BUY'):
            raise NativeStateMismatch('Unproven partial quantity in original LA order')
        return 'NATIVE_PARTIAL'
    if (native.get('_class')=='PendingOrder' and native.get('quantity')==order['quantity']
        and Decimal(str(native.get('budget')))==Decimal(order['reserved_outlay'])):
        return 'NATIVE_LEGACY_FOK'
    return 'FOK'

def assert_native_parity(wallet):
    view=native_view(wallet['city'],wallet.get('policy_state',{}))
    if view is None:return
    eq=lambda a,b:Decimal(str(a))==Decimal(str(b))
    if not eq(view['cash'],wallet['cash']):raise NativeStateMismatch('Native/common cash mismatch')
    common_reserved=sum((Decimal(o['reserved_outlay']) for o in wallet['orders'].values() if o['status'] in ('PENDING','UNKNOWN')),Decimal(0))
    if not eq(view['reserved'],common_reserved):raise NativeStateMismatch('Native/common cash reservations mismatch')
    if view.get('realized') is not None and not eq(view['realized'],wallet['realized']):
        raise NativeStateMismatch('Native/common realized cash mismatch')
    native_positions={}
    for key,p in view['positions'].items():
        if not p.get('quantity'):continue
        ticker=p.get('ticker') or (key[0] if isinstance(key,tuple) else key)
        side=p.get('side') or (key[1] if isinstance(key,tuple) else 'NO')
        native_positions[ticker+'|'+side]=(int(p['quantity']),Decimal(str(p['basis'])))
    common_positions={k:(int(p['quantity']),Decimal(p['basis'])) for k,p in wallet['positions'].items() if p['quantity']}
    if native_positions!=common_positions:raise NativeStateMismatch('Native/common position quantity or basis mismatch')
    for oid,o in wallet['orders'].items():
        native=view['orders'].get(oid)
        if native is None:raise NativeStateMismatch('Common order absent from native state')
        status=native['status']
        if o['status']=='PENDING' and status!='PENDING':raise NativeStateMismatch('Native/common pending status mismatch')
        if o['status']=='UNKNOWN' and 'UNKNOWN' not in status:raise NativeStateMismatch('Native/common unknown status mismatch')
        if o['status']=='CANCELLED' and status in ('PENDING','UNKNOWN_EXECUTION','UNKNOWN_EXECUTION_EVIDENCE'):
            raise NativeStateMismatch('Native pending reservation survives common cancellation')
        if o['status']=='FILLED' and 'FILL' not in status:raise NativeStateMismatch('Native/common fill status mismatch')

def recover_simple_policy(city,state,order,status,reason,at_ns):
    """Exact NY raw state and stateless Miami/Phoenix; LA requires native hook."""
    if status not in ('CANCELLED','UNKNOWN'):raise ValueError('Recovery cannot fabricate a fill')
    result=deepcopy(state)
    if city=='la':raise ValueError('LA recovery must use recover_la_pending with its native engine')
    if city=='ny' and 'orders' in result:
        native=result['orders'].get(order['order_id'])
        if not native or native['status'] not in ('PENDING','UNKNOWN_EXECUTION'):
            raise NativeStateMismatch('Original pending NY order required')
        native.update(status='ASSUMED_TERMINAL_NO_FILL' if status=='CANCELLED' else 'UNKNOWN_EXECUTION',
                      reason=reason,recovery_ns=at_ns)
        if status=='CANCELLED':native.update(amount='0',filled_quantity=0)
    elif city not in ('miami','phoenix','ny'):raise ValueError('Unknown city schema')
    result['recovery_reason']=reason
    if status=='UNKNOWN':result['unknown_execution']=True
    else:
        # The service still checks every common unresolved order; an unrelated
        # unknown must never be cleared by cancellation of this one intent.
        if city=='ny' and 'orders' in result:
            result['unknown_execution']=any(o['status']=='UNKNOWN_EXECUTION' for o in result['orders'].values())
    return result

def recover_la_pending(native,request):
    """Use exact original LA object/engine reservation methods; no book or fit."""
    profile=request['profile_id'];rt=native.runtime(profile);body=native._state(request,rt)
    obj=body['object'];oid=request['order_id'];order=obj.orders[oid]
    status=request['recovery_status'];reason=request['recovery_reason'];at=request['at_ns']
    if status not in ('CANCELLED','UNKNOWN'):raise ValueError('Recovery cannot fabricate a fill')
    if order.status not in ('PENDING','UNKNOWN_EXECUTION','UNKNOWN_EXECUTION_EVIDENCE'):
        raise NativeStateMismatch('Original pending/unknown LA order required')
    legacy=profile==native.PROFILES[2]
    wallet=obj.wallet if legacy else obj.wallets[order.strategy_id]
    if status=='CANCELLED':
        if order.status!='PENDING':raise ValueError('Ambiguous attempted LA orders cannot be cancelled automatically')
        if legacy:
            report=obj.cancel_order(oid,at,reason)
            body['policy'].observe_execution(report,obj)
        else:
            obj._time(at);obj._reserve(order,-1);order.status='KNOWN_NO_FILL'
            report={'order_id':oid,'status':'KNOWN_NO_FILL','reason':reason,'arrival_ns':at,'quantity':0,'recovery_only':True}
            wallet.ledger.append(report)
    else:
        obj._time(at)
        order.status='UNKNOWN_EXECUTION_EVIDENCE' if legacy else 'UNKNOWN_EXECUTION'
        if legacy:body['unresolved_legacy']=True
        else:wallet.uncertain=True
        report={'order_id':oid,'status':order.status,'reason':reason,'arrival_ns':at,'quantity':0,'recovery_only':True}
        wallet.ledger.append(report)
    return native._finish(request,body,{'status':status,'order_id':oid,'reason':reason,'quantity':0,'principal':'0','fee':'0',
                                      'recovery_only':True,'native_receipt':report})
