"""Durable paper cash, reservations and shared complementary book capacity."""
from copy import deepcopy
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
from pathlib import Path
import json,sqlite3,threading,time,hashlib
D=Decimal
ZERO=D('0')

class Ledger:
 def __init__(self,path,profiles):
  Path(path).parent.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(path,check_same_thread=False);self.lock=threading.RLock()
  self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL)')
  self.db.execute('CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, at_ns INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
  if not self.db.execute('SELECT 1 FROM state').fetchone():
   state={'wallets':{},'capacity':{},'release_id':None}
   for p in profiles:
    state['wallets'][p['wallet_id']]={'wallet_id':p['wallet_id'],'city':p['city'],'profile_id':p['profile_id'],
     'initial_cash':'100','cash':'100','realized':'0','fees':'0','orders':{},'positions':{},
     'daily_opening':{},'daily_spend':{},'policy_state':{},'history':[]}
   self.db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(state),));self.db.commit()
  existing=set(self.snapshot()['wallets']);expected={p['wallet_id'] for p in profiles}
  if existing!=expected:
   self.db.close();raise ValueError('Wallet set differs; never silently import/reset balances')
  for p in profiles:
   w=self.snapshot()['wallets'][p['wallet_id']]
   if (w['city'],w['profile_id'])!=(p['city'],p['profile_id']):
    self.db.close();raise ValueError('Persisted wallet identity differs')
 def snapshot(self):
  with self.lock:return json.loads(self.db.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
 def commit(self,state,kind,payload):
  with self.lock,self.db:
   self.db.execute('UPDATE state SET json=? WHERE id=1',(json.dumps(state,allow_nan=False),))
   self.db.execute('INSERT INTO journal(at_ns,kind,payload) VALUES(?,?,?)',(time.time_ns(),kind,json.dumps(payload,allow_nan=False)))
 def free(self,w):
  return D(w['cash'])-sum((D(o['reserved_outlay']) for o in w['orders'].values() if o['status'] in ('PENDING','UNKNOWN')),ZERO)
 def opening(self,w,day):
  w['daily_opening'].setdefault(day,str(self.free(w)));return D(w['daily_opening'][day])
 def request_cash(self,wid,day):
  with self.lock:
   state=self.snapshot();w=state['wallets'][wid];opening=self.opening(w,day)
   self.commit(state,'DAY_OPEN',{'wallet_id':wid,'day':day,'opening_cash':str(opening)})
   return {'opening_cash':str(opening),'available_cash':str(self.free(w)),
    'policy_state':deepcopy(w['policy_state']),'state':deepcopy(w['policy_state']),
    'submitted':any(o['day']==day for o in w['orders'].values())}
 def reserve(self,wid,day,result,cap,actual_ns):
  with self.lock:
   state=self.snapshot();w=state['wallets'][wid];opening=self.opening(w,day)
   orders=deepcopy(result.get('orders',[]));remaining=opening*D(cap)-D(w['daily_spend'].get(day,'0'))
   remaining-=sum((D(o['reserved_outlay']) for o in w['orders'].values() if o['day']==day and o['status'] in ('PENDING','UNKNOWN')),ZERO)
   total=sum((D(o['reserved_outlay']) for o in orders),ZERO)
   if total>remaining or total>self.free(w) or total<ZERO:raise ValueError('Fee-inclusive daily/free cash cap exceeded')
   for i,o in enumerate(orders):
    if type(o['quantity']) is not int or o['quantity']<=0 or o['side'] not in ('YES','NO') or not ZERO<D(o['limit_price'])<1:
     raise ValueError('Invalid frozen whole-contract order')
    action=o.get('action','BUY')
    if action not in ('BUY','SELL'):raise ValueError('Invalid paper action')
    if action=='BUY' and D(o['reserved_outlay'])<o['quantity']*D(o['limit_price']):raise ValueError('Reservation below principal')
    if action=='SELL':
     if o.get('exit_policy')!='LA-IMPOSSIBLE-ONLY-EXIT-v1' or w['city']!='la':raise ValueError('Unregistered paper liquidation')
     if D(o['reserved_outlay'])!=ZERO:raise ValueError('Sell reserves inventory, never cash')
     key=o['ticker']+'|'+o['side'];position=w['positions'].get(key,{})
     reserved=sum(v['quantity'] for v in w['orders'].values() if v.get('action')=='SELL' and v['status'] in ('PENDING','UNKNOWN') and v['ticker']+'|'+v['side']==key)
     if o['quantity']>position.get('quantity',0)-reserved:raise ValueError('Sell exceeds unreserved inventory')
    oid=o.get('native_order',{}).get('order_id') or hashlib.sha256(json.dumps([wid,day,actual_ns,i,o],sort_keys=True).encode()).hexdigest()[:24]
    if oid in w['orders']:raise ValueError('Duplicate paper order')
    o.update(order_id=oid,status='PENDING',day=day,actual_decision_ns=actual_ns,
     arrival_due_ns=actual_ns+int(o.get('arrival_delay_seconds',5))*10**9)
    w['orders'][oid]=o
   if result.get('policy_state') is not None:w['policy_state']=result['policy_state']
   self.commit(state,'PAPER_INTENT',{'wallet_id':wid,'orders':orders,'actual_orders':0})
   return orders
 @staticmethod
 def physical_side(side,action):
  # A NO purchase and a YES sale consume the same YES bids.
  return ('yes' if side=='NO' else 'no') if action=='BUY' else side.lower()
 def observe_capacity(self,quote,state):
  """Only an observed increase at the SAME physical price restores capacity.

  Equal refetches, another side changing and HTTP metadata never replenish a
  price. This remains a conservative snapshot assumption, not a real fill.
  """
  ticker=quote['ticker'];received=quote['received_ns']
  for side in ('yes','no'):
   seen={str(D(p).normalize()):D(q) for p,q in quote.get(side+'_bids',[])}
   prefix=ticker+'|'+side+'|'
   prices=set(seen)|{k[len(prefix):] for k in state['capacity'] if k.startswith(prefix)}
   for price in prices:
    key=prefix+price;v=state['capacity'].setdefault(key,{'displayed':'0','consumed':'0','received_ns':0})
    if received<=v['received_ns']:continue
    current=seen.get(price,ZERO);increase=max(ZERO,current-D(v['displayed']))
    v.update(displayed=str(current),consumed=str(max(ZERO,D(v['consumed'])-increase)),received_ns=received)
 def remaining_book(self,quote):
  q=deepcopy(quote)
  with self.lock:
   state=self.snapshot();self.observe_capacity(q,state)
   self.commit(state,'PUBLIC_DEPTH_OBSERVED',{'ticker':q['ticker'],'received_ns':q['received_ns']})
  for side in ('yes','no'):
   rows=[]
   for price,quantity in q.get(side+'_bids',[]):
    key='|'.join((q['ticker'],side,str(D(price).normalize())))
    left=max(ZERO,D(quantity)-D(state['capacity'][key]['consumed']))
    if left:rows.append([price,str(left)])
   q[side+'_bids']=rows
  q['no_asks']=sorted([[str(1-D(p)),qty] for p,qty in q['yes_bids']],key=lambda x:D(x[0]))
  q['yes_asks']=sorted([[str(1-D(p)),qty] for p,qty in q['no_bids']],key=lambda x:D(x[0]))
  q.update(bids=q['no_bids'],asks=q['no_asks'],bid=q['no_bids'][0][0] if q['no_bids'] else None,
   ask=q['no_asks'][0][0] if q['no_asks'] else None)
  q['shared_capacity_reduction']=True
  return q
 def reconcile(self,wid,oid,result,quote,at_ns):
  with self.lock:
   state=self.snapshot();w=state['wallets'][wid];o=w['orders'][oid]
   if o['status'] not in ('PENDING','UNKNOWN'):raise ValueError('Order already reconciled')
   if o.get('action')=='SELL':return self._reconcile_sell(state,wid,oid,result,quote,at_ns)
   status=result.get('status','UNKNOWN')
   if status not in ('FILLED','CANCELLED','UNKNOWN'):status='UNKNOWN'
   if status=='FILLED':
    qty=result.get('filled_quantity',result.get('quantity'));principal=D(result['principal']);fee=D(result['fee'])
    if qty!=o['quantity'] or principal<0 or fee<0 or principal+fee>D(o['reserved_outlay']):raise ValueError('Whole FOK/reservation/fee check failed')
    self.observe_capacity(quote,state)
    side=self.physical_side(o['side'],'BUY');ladder={1-D(p):D(v) for p,v in quote.get(side+'_bids',[])}
    rawfills=result.get('fills')
    if not rawfills:
     price=principal/qty
     if price not in ladder:raise ValueError('Exact native per-level fills required')
     rawfills=[{'quantity':qty,'price':str(price),'fee':str(fee)}]
    fills=[]
    for f in rawfills:
     n,price=(f['quantity'],D(f['price'])) if isinstance(f,dict) else (f[0],D(f[1]))
     if type(n) is not int or n<=0 or price not in ladder or price>D(o['limit_price']):raise ValueError('Invalid native fill price/quantity')
     quantum=D(o['fee_quantum']);rate=D(o['fee_rate'])
     rawfee=rate*n*price*(1-price)
     native_fee=((n*price+rawfee)/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum-n*price if o.get('fee_rounding')=='TOTAL_OUTLAY_CEILING' else (rawfee/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum
     if isinstance(f,dict) and 'fee' in f and D(f['fee'])!=native_fee:raise ValueError('Native per-level fee mismatch')
     fills.append((n,price,native_fee))
    if sum(n for n,p,f in fills)!=qty or sum((n*p for n,p,f in fills),ZERO)!=principal or sum((f for n,p,f in fills),ZERO)!=fee:
     raise ValueError('Native principal/fee reconciliation failed')
    allocations={}
    for n,p,f in fills:
     key='|'.join((o['ticker'],side,str((1-p).normalize())))
     allocations[key]=allocations.get(key,0)+n
    for key,n in allocations.items():
     v=state['capacity'][key]
     if n>D(v['displayed'])-D(v['consumed']):raise ValueError('Shared physical depth insufficient')
     v['consumed']=str(D(v['consumed'])+n)
    w['cash']=str(D(w['cash'])-principal-fee);w['fees']=str(D(w['fees'])+fee)
    w['daily_spend'][o['day']]=str(D(w['daily_spend'].get(o['day'],'0'))+principal+fee)
    key=o['ticker']+'|'+o['side'];pos=w['positions'].setdefault(key,{'ticker':o['ticker'],'side':o['side'],
     'day':o['day'],'quantity':0,'basis':'0','first_fill_ns':at_ns})
    pos['quantity']+=qty;pos['basis']=str(D(pos['basis'])+principal+fee)
   o.update(status=status,arrival_result=result,actual_arrival_ns=at_ns)
   if result.get('policy_state') is not None:w['policy_state']=result['policy_state']
   if status=='UNKNOWN' and w['policy_state'].get('schema')!='LA_TRIO_NATIVE_POLICY_STATE_v1':w['policy_state']['unknown_execution']=True
   self.commit(state,'PAPER_ARRIVAL',{'wallet_id':wid,'order_id':oid,'status':status,'actual_exchange_fill_proven':False})
 def _reconcile_sell(self,state,wid,oid,result,quote,at_ns):
  w=state['wallets'][wid];o=w['orders'][oid];status=result.get('status','UNKNOWN')
  if status not in ('FILLED','CANCELLED','UNKNOWN'):status='UNKNOWN'
  if status=='FILLED':
   qty=result.get('filled_quantity',result.get('quantity'))
   if type(qty) is not int or not 0<qty<=o['quantity']:raise ValueError('Invalid whole sell quantity')
   if at_ns<o['arrival_due_ns'] or quote.get('received_ns',0)<o['arrival_due_ns'] or quote.get('received_ns',at_ns+1)>at_ns:
    raise ValueError('Sell arrival cannot precede actual quote receipt or five-second delay')
   key=o['ticker']+'|'+o['side'];pos=w['positions'].get(key)
   if not pos or qty>pos['quantity']:raise ValueError('Sell exceeds owned inventory')
   self.observe_capacity(quote,state)
   side=self.physical_side(o['side'],'SELL');ladder={D(p):D(v) for p,v in quote.get(side+'_bids',[])}
   raw=result.get('fills') or result.get('native_receipt',{}).get('fills')
   if not raw:raise ValueError('Exact native sell fills required')
   fills=[];allocations={}
   for f in raw:
    n,p=(f['quantity'],D(f['price'])) if isinstance(f,dict) else (f[0],D(f[1]))
    if type(n) is not int or n<=0 or p not in ladder or p<D(o['limit_price']):raise ValueError('Invalid native sell price/quantity')
    fills.append((n,p));pool='|'.join((o['ticker'],side,str(p.normalize())))
    allocations[pool]=allocations.get(pool,0)+n
   principal=sum((n*p for n,p in fills),ZERO)
   if sum(n for n,p in fills)!=qty or principal!=D(result['principal']):raise ValueError('Sell principal reconciliation failed')
   rate=D(o['fee_rate']);quantum=D(o['fee_quantum'])
   if o['fee_rounding']=='TOTAL_PROCEEDS_FLOOR':
    rawfee=sum((rate*n*p*(1-p) for n,p in fills),ZERO)
    proceeds=(principal-rawfee).quantize(quantum,rounding=ROUND_FLOOR)
   else:
    # Preserve native retail signed cashflow, including per-fill six-decimal
    # trade fees and the order's rounding-fee rebate accumulator.
    proceeds=ZERO;accumulator=ZERO
    for n,p in fills:
     revenue=n*p;trade_fee=(rate*n*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
     aligned=(revenue-trade_fee).quantize(quantum,rounding=ROUND_FLOOR)
     rounding_fee=revenue-trade_fee-aligned;accumulator+=rounding_fee
     rebate=(min(accumulator,trade_fee+rounding_fee)/quantum).to_integral_value(rounding=ROUND_FLOOR)*quantum
     accumulator-=rebate;proceeds+=aligned+rebate
   fee=principal-proceeds
   if fee!=D(result['fee']) or proceeds<=0:raise ValueError('Native sell fee/net proceeds mismatch')
   for pool,n in allocations.items():
    v=state['capacity'][pool]
    if n>D(v['displayed'])-D(v['consumed']):raise ValueError('Shared physical buyer depth insufficient')
    v['consumed']=str(D(v['consumed'])+n)
   basis=D(pos['basis']) if qty==pos['quantity'] else D(pos['basis'])*D(qty)/D(pos['quantity'])
   profit=proceeds-basis;w['cash']=str(D(w['cash'])+proceeds);w['fees']=str(D(w['fees'])+fee)
   w['realized']=str(D(w['realized'])+profit)
   row={**pos,'quantity':qty,'basis':str(basis),'payout':str(proceeds),'profit':str(profit),
        'exit_kind':'IMPOSSIBLE_POSITION_PAPER_SALE','exit_policy':o['exit_policy'],'sold_ns':at_ns,'order_id':oid}
   w['history'].append(row)
   pos['quantity']-=qty;pos['basis']=str(D(pos['basis'])-basis)
   if not pos['quantity']:del w['positions'][key]
  o.update(status=status,arrival_result=result,actual_arrival_ns=at_ns)
  if result.get('policy_state') is not None:w['policy_state']=result['policy_state']
  self.commit(state,'PROTECTIVE_PAPER_SELL_ARRIVAL',{'wallet_id':wid,'order_id':oid,'status':status,'actual_exchange_fill_proven':False})
 def settle(self,wid,market,received_ns,native_state=None):
  if market.get('status') not in ('finalized','settled') or market.get('result') not in ('yes','no'):
   raise ValueError('Official finalized binary result required')
  with self.lock:
   state=self.snapshot();w=state['wallets'][wid];closed=[]
   for key,p in list(w['positions'].items()):
    if p['ticker']!=market['ticker']:continue
    payout=D(p['quantity']) if p['side'].lower()==market['result'] else ZERO
    profit=payout-D(p['basis']);w['cash']=str(D(w['cash'])+payout);w['realized']=str(D(w['realized'])+profit)
    row={**p,'payout':str(payout),'profit':str(profit),'observed_settlement_ns':received_ns,'official_result':market['result']}
    w['history'].append(row);closed.append(row);del w['positions'][key]
   if native_state is not None:w['policy_state']=native_state
   if any(o['status']=='UNKNOWN' for o in w['orders'].values()) and w['policy_state'].get('schema')!='LA_TRIO_NATIVE_POLICY_STATE_v1':w['policy_state']['unknown_execution']=True
   if closed:self.commit(state,'OFFICIAL_PAPER_SETTLEMENT',{'wallet_id':wid,'closed':closed})
   return closed
