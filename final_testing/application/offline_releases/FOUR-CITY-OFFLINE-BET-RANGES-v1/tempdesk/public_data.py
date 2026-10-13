"""Public GET-only data; exact capture clocks, no authentication or orders."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen
import hashlib, json, math, time, threading
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

API='https://external-api.kalshi.com/trade-api/v2'
ALLOWED={'external-api.kalshi.com','api.weather.gov','weather.com','kalshi.com','assets.kalshi.com'}
CITIES={
 'la':dict(name='Los Angeles',station='KLAX',zone='America/Los_Angeles',series='KXHIGHLAX'),
 'ny':dict(name='New York',station='KNYC',zone='America/New_York',series='KXHIGHNY'),
 'miami':dict(name='Miami',station='KMIA',zone='America/New_York',series='KXHIGHMIA'),
 'phoenix':dict(name='Phoenix',station='KPHX',zone='America/Phoenix',series='KXHIGHTPHX')}
D=Decimal
MONTHS=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
def event_ticker(city,day):
 date=datetime.fromisoformat(day)
 return f"{CITIES[city]['series']}-{date.year%100:02d}{MONTHS[date.month-1]}{date.day:02d}"

class PublicData:
 def __init__(self,capture_dir):
  self.root=Path(capture_dir);self.root.mkdir(parents=True,exist_ok=True)
  self.cache={};self.cache_lock=threading.Lock()
 def cached_get(self,url,decode_json=True):
  # Share the four-city portal response; keep its true original receipt clock.
  with self.cache_lock:
   item=self.cache.get(url)
   lifetime=3600 if not decode_json else 25
   if item and time.time_ns()-item[1]['received_ns']<lifetime*10**9:return item
   item=self.get(url,decode_json=decode_json);self.cache[url]=item
   # Bounded runtime cache, including day/week rollover.
   if len(self.cache)>12:self.cache.pop(next(iter(self.cache)))
   return item
 def get(self,url,decode_json=True):
  if urlparse(url).scheme!='https' or urlparse(url).hostname not in ALLOWED:
   raise ValueError('Only allowlisted public HTTPS GET endpoints')
  if urlparse(url).hostname=='weather.com' and urlparse(url).path not in ('/kalshi/api/metar','/kalshi/api/climate/primary'):
   raise ValueError('Only the exact public Kalshi Weather Company products')
  if urlparse(url).hostname in ('kalshi.com','assets.kalshi.com') and url not in ('https://kalshi.com/docs/kalshi-fee-schedule.pdf','https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf'):
   raise ValueError('Only the registered public fee and contract documents')
  started=time.time_ns()
  request=Request(url,headers={'User-Agent':'TempDesk-paper/1.0 (read-only weather research)','Accept':'application/json' if decode_json else 'application/pdf'})
  for attempt in range(4):
   try:response=urlopen(request,timeout=15);break
   except HTTPError as error:
    if error.code not in (429,500,502,503,504) or attempt==3:raise
    delay=min(20,max(1,int(error.headers.get('Retry-After','0')))) if error.headers.get('Retry-After','0').isdigit() else 2**attempt
    time.sleep(max(delay,2**attempt))
  with response:
   if urlparse(response.geturl()).hostname != urlparse(url).hostname:
    raise ValueError('Public source redirected to another authority')
   raw=response.read(5_000_001);received=time.time_ns()
   if len(raw)>5_000_000:raise ValueError('Bounded public response exceeded')
   date=response.headers.get('Date');server_ns=None
   if date:
    try:server_ns=int(parsedate_to_datetime(date).timestamp()*1e9)
    except (TypeError,ValueError):pass
  body=json.loads(raw) if decode_json else raw
  digest=hashlib.sha256(raw).hexdigest()
  folder=self.root/datetime.now(timezone.utc).date().isoformat();folder.mkdir(exist_ok=True)
  path=folder/(f'{received}-{digest[:12]}'+('.json' if decode_json else '.raw'));path.write_bytes(raw)
  receipt=dict(url=url,request_started_ns=started,received_ns=received,server_time_ns=server_ns,
   response_sha256=digest,response_bytes=len(raw),source_age_clock='HTTP_RECEIPT',
   exchange_event_time_provided=False,raw_capture=str(path))
  path.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
  return body,receipt
 def markets(self,city,day):
  ticker=event_ticker(city,day)
  body,receipt=self.get(API+'/markets?'+urlencode(dict(event_ticker=ticker,limit=100)))
  markets=[x for x in body.get('markets',[]) if x.get('event_ticker')==ticker]
  contracts=[]
  for m in markets:
   low,high=bounds(m)
   raw=dict(m);raw['_received_ns']=receipt['received_ns']
   label=m.get('yes_sub_title') or m.get('subtitle') or range_label(low,high)
   contracts.append(dict(ticker=m['ticker'],lower=low,upper=high,label=label,raw=raw))
  contracts.sort(key=lambda x:-math.inf if x['lower'] is None else x['lower'])
  complete=len(contracts)==6 and contracts[0]['lower'] is None and contracts[-1]['upper'] is None
  complete=complete and all(a['upper']==b['lower'] for a,b in zip(contracts,contracts[1:]))
  complete=complete and len({x['ticker'] for x in contracts})==6
  return dict(day=day,event_ticker=ticker,contracts=contracts,contracts_received_ns=receipt['received_ns'],
    receipt=receipt,layout_complete=bool(complete),settlement_target=settlement_target(contracts),
    status='AVAILABLE' if complete else 'MARKET_LAYOUT_UNAVAILABLE')
 def book(self,ticker):
  body,receipt=self.get(API+'/markets/'+quote(ticker,safe='')+'/orderbook')
  book=body.get('orderbook_fp')
  if book is None:
   old=body.get('orderbook',{});book={k+'_dollars':[[str(D(str(p))/100),str(q)] for p,q in old.get(k,[])] for k in ('yes','no')}
  yes=levels(book.get('yes_dollars',[]),reverse=True);no=levels(book.get('no_dollars',[]),reverse=True)
  noasks=sorted([[str(1-D(p)),q] for p,q in yes],key=lambda x:D(x[0]))
  yesasks=sorted([[str(1-D(p)),q] for p,q in no],key=lambda x:D(x[0]))
  return dict(ticker=ticker,bids=no,asks=noasks,bid=no[0][0] if no else None,ask=noasks[0][0] if noasks else None,
   yes_bids=yes,yes_asks=yesasks,no_bids=no,no_asks=noasks,received_ns=receipt['received_ns'],
   server_time_ns=receipt['server_time_ns'],event_ns=None,source_age_clock='RECEIPT_CLOCK',receipt=receipt,
   physical_epoch=hashlib.sha256(json.dumps([yes,no],separators=(',',':')).encode()).hexdigest(),
   status='AVAILABLE' if yes and no else 'EMPTY_BOOK_SIDE')
 def books(self,contracts):
  def capture(c):
   try:return c['ticker'],self.book(c['ticker'])
   except Exception as e:return c['ticker'],dict(ticker=c['ticker'],status='SOURCE_UNAVAILABLE',error=f'{type(e).__name__}: {e}')
  with ThreadPoolExecutor(max_workers=6) as pool:return dict(pool.map(capture,contracts))
 def settlement_observations(self,station,day,market):
  if market.get('settlement_target',{}).get('provider')=='THE_WEATHER_COMPANY':
   from .twc import collect
   return collect(self,station,day,market)
  return self.observations(station,day)
 def observations(self,station,day):
  body,receipt=self.get(f'https://api.weather.gov/stations/{quote(station)}/observations?limit=100')
  values=[]
  for feature in body.get('features',[]):
   p=feature.get('properties',{});temperature=p.get('temperature') or {}
   if temperature.get('value') is None or temperature.get('unitCode')!='wmoUnit:degC':continue
   stamp=p.get('timestamp')
   if not stamp:continue
   zone=next(c['zone'] for c in CITIES.values() if c['station']==station)
   if datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(ZoneInfo(zone)).date().isoformat()!=day:continue
   values.append(dict(timestamp=stamp,temperature_f=float(temperature['value'])*9/5+32))
  values.sort(key=lambda x:x['timestamp'])
  return dict(station=station,values=values,receipt=receipt,status='AVAILABLE' if values else 'OBSERVATIONS_UNAVAILABLE',
    provider='NATIONAL_WEATHER_SERVICE',official_settlement_high=False,settlement_high_floor=None)

def levels(rows,reverse=False):
 out=[]
 for p,q in rows:
  p,q=D(str(p)),D(str(q))
  if not p.is_finite() or not q.is_finite() or not 0<=p<=1 or q<0:raise ValueError('Invalid public price/quantity')
  if q>0:out.append([str(p),str(q)])
 return sorted(out,key=lambda x:D(x[0]),reverse=reverse)
def bounds(m):
 kind=m.get('strike_type');floor=m.get('floor_strike');cap=m.get('cap_strike')
 ceil=lambda v:int(D(str(v)).to_integral_value(rounding=ROUND_CEILING))
 down=lambda v:int(D(str(v)).to_integral_value(rounding=ROUND_FLOOR))
 if kind=='between' and floor is not None and cap is not None:return ceil(floor),down(cap)+1
 if kind=='greater' and floor is not None:return down(floor)+1,None
 if kind=='greater_or_equal' and floor is not None:return ceil(floor),None
 if kind=='less' and cap is not None:return None,ceil(cap)
 if kind=='less_or_equal' and cap is not None:return None,down(cap)+1
 raise ValueError(f'Unsupported genuine market strike structure: {kind}')
def range_label(low,high):
 if low is None:return f'{high-1}° or below'
 if high is None:return f'{low}° or above'
 return f'{low}–{high-1}°'

def settlement_target(contracts):
 """Classify actual dated rules; a legacy CLI marker never overrides TWC."""
 providers=[]
 for c in contracts:
  r=c.get('raw',{});text=(r.get('rules_primary','')+'\n'+r.get('rules_secondary','')).lower()
  providers.append('THE_WEATHER_COMPANY' if 'weather company' in text or 'twc' in text else
   'NATIONAL_WEATHER_SERVICE' if 'national weather service' in text else 'UNVERIFIED')
 provider=providers[0] if providers and len(set(providers))==1 else 'UNVERIFIED'
 return dict(provider=provider,model_target='NWS_STATION_PHYSICAL_HIGH',
  compatible=provider=='NATIONAL_WEATHER_SERVICE',
  status='SETTLEMENT_TARGET_INCOMPATIBLE' if provider=='THE_WEATHER_COMPANY' else
   'NWS_RULES_REQUIRE_NATIVE_CITY_VALIDATION' if provider=='NATIONAL_WEATHER_SERVICE' else 'SETTLEMENT_TARGET_UNVERIFIED',
  explanation='Current settlement uses The Weather Company. Saved models predict NWS station highs; equivalence is unproved.' if provider=='THE_WEATHER_COMPANY' else
   'The native city adapter must verify the exact station, day and official report before admitting a trade.')

if __name__=='__main__':
 import argparse
 a=argparse.ArgumentParser();a.add_argument('--probe',action='store_true');a.add_argument('--runtime',default='runtime/public');args=a.parse_args()
 if args.probe:
  from zoneinfo import ZoneInfo
  provider=PublicData(args.runtime)
  for city,c in CITIES.items():
   try:
    day=datetime.now(ZoneInfo(c['zone'])).date().isoformat();m=provider.markets(city,day)
    print(json.dumps(dict(city=city,day=day,status=m['status'],contracts=len(m['contracts']),
      settlement_target=m['settlement_target'],labels=[x['label'] for x in m['contracts']])))
   except Exception as e:print(json.dumps(dict(city=city,status='SOURCE_UNAVAILABLE',error=f'{type(e).__name__}: {e}')))
