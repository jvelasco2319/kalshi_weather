"""Dated, evidence-bound paper mapping and fee admission. No fitted models.

Comparisons admit a declared same-scalar paper hypothesis, never prove future
TWC publications/revisions equal NWS or guarantee calibrated trading odds.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlparse
import hashlib, json, re, time
from .public_data import CITIES
from .impossible_exit import rules_hash
from .twc import climate_window, BASE

SCHEMA='PAIRED_OFFICIAL_DAILY_CLI_PAPER_MAPPING_v1'
CLI={'la':'LAX','ny':'NYC','miami':'MIA','phoenix':'PHX'}
FEE_URL='https://kalshi.com/docs/kalshi-fee-schedule.pdf'
TERMS_URL='https://assets.kalshi.com/contract_terms/GLOBALTEMPERATURE.pdf'
NS=10**9

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def checked_receipt(receipt,workdir=None):
    p=Path(receipt['raw_capture'])
    if not p.is_absolute():p=Path(workdir or Path.cwd())/p
    if digest(p)!=receipt['response_sha256'] or json.loads(p.with_suffix('.receipt.json').read_bytes())!=receipt:
        raise ValueError('Public receipt bytes changed')
    if not 0<=receipt['request_started_ns']<=receipt['received_ns']<=time.time_ns():
        raise ValueError('Invalid public receipt clock')
    return p

def parse_cli(body, city):
    text=body.get('productText','');cli=CLI[city]
    if body.get('productCode')!='CLI' or not re.search(r'^CLI'+cli+r'\s*$',text,re.M) or 'NATIONAL WEATHER SERVICE' not in text or 'TEMPERATURE (F)' not in text:
        raise ValueError('Wrong official NWS station/product/units')
    matched=re.search(r'CLIMATE SUMMARY FOR\s+([A-Z]+\s+\d{1,2}\s+\d{4})',text)
    high=re.search(r'^\s+MAXIMUM\s+(-?\d+)\s',text,re.M)
    if not matched or not high:raise ValueError('Official daily date/high unavailable')
    day=datetime.strptime(matched[1],'%B %d %Y').date().isoformat()
    issued=datetime.fromisoformat(body['issuanceTime'].replace('Z','+00:00'))
    if issued<climate_window(CITIES[city]['station'],day)[1]:raise ValueError('NWS report is an incomplete-day update')
    return {'day':day,'high_f':int(high[1]),'issued_ns':int(issued.timestamp()*1e9)}

def build(provider, city, market, root):
    """At least three recent completed days, both official publishers, no mismatch."""
    day=market['day'];station=CITIES[city]['station'];cli=CLI[city]
    if not market.get('layout_complete') or market['settlement_target']['provider']!='THE_WEATHER_COMPANY':
        raise ValueError('Complete dated TWC daily market required')
    if not all('CLI'+cli in c['raw'].get('rules_primary','').upper() and 'maximum temperature' in c['raw'].get('rules_primary','').lower() and 'fahrenheit' in c['raw'].get('rules_primary','').lower() for c in market['contracts']):
        raise ValueError('Dated market station/scalar changed')
    registration=json.loads(Path(__file__).with_name('public-policy-registration.json').read_bytes())
    terms,terms_r=provider.cached_get(TERMS_URL,decode_json=False)
    if hashlib.sha256(terms).hexdigest()!=registration['contract_terms_sha256']:
        raise ValueError('Official contract terms changed; review required')
    index,index_r=provider.get('https://api.weather.gov/products/types/CLI/locations/'+cli)
    reports={};sources=[]
    for item in index.get('@graph',[])[:16]:
        url=item.get('@id','')
        if urlparse(url).hostname!='api.weather.gov' or not urlparse(url).path.startswith('/products/'):
            continue
        body,r=provider.get(url)
        try:parsed=parse_cli(body,city)
        except ValueError:continue
        if parsed['day']>=day or parsed['day']<(date.fromisoformat(day)-timedelta(days=7)).isoformat():continue
        previous=reports.get(parsed['day'])
        if previous is None or parsed['issued_ns']>previous[0]['issued_ns']:reports[parsed['day']]=(parsed,r)
    pairs=[]
    for d,(nws,nws_r) in sorted(reports.items()):
        body,twc_r=provider.cached_get(BASE+'/api/climate/primary?date='+d)
        rows=[x for x in body.get('results',[]) if x.get('station',{}).get('icao')==station]
        if len(rows)!=1 or body.get('date')!=d:continue
        row=rows[0];data=row.get('data') or {}
        if row.get('status') not in ('official','revised') or data.get('isOfficial') is not True:continue
        if data.get('stationId')!=cli or data.get('reportDate')!=d:raise ValueError('TWC report identity mismatch')
        high=data.get('maxTemp')
        if type(high) not in (int,float) or Decimal(str(high))!=Decimal(str(high)).to_integral_value():raise ValueError('TWC settlement precision changed')
        if int(high)!=nws['high_f']:raise ValueError('NWS/TWC official high divergence for '+city+' '+d)
        pairs.append(dict(day=d,high_f=int(high),nws_receipt=nws_r,twc_receipt=twc_r))
    if len(pairs)<3 or pairs[-1]['day']!=(date.fromisoformat(day)-timedelta(days=1)).isoformat():
        raise ValueError('Three matching days including latest completed climate day required')
    # This PDF endpoint sometimes rate-limits programmatic reads. The original
    # genuine retained PDF is already part of the portable source closure. A
    # retained document never receives a fabricated new download timestamp.
    fee_r=None
    try:
        fee_bytes,fee_r=provider.cached_get(FEE_URL,decode_json=False)
    except Exception:
        fee_bytes=Path(registration['retained_fee_path']).read_bytes()
    if hashlib.sha256(fee_bytes).hexdigest()!=registration['fee_schedule_sha256']:
        raise ValueError('Official fee document changed; review required')
    series,series_r=provider.get('https://external-api.kalshi.com/trade-api/v2/series/'+CITIES[city]['series'])
    s=series.get('series',{})
    if s.get('ticker')!=CITIES[city]['series'] or s.get('fee_type')!='quadratic' or Decimal(str(s.get('fee_multiplier')))!=1:
        raise ValueError('Native paper fee coefficient/multiplier no longer matches this series')
    changes,changes_r=provider.get('https://external-api.kalshi.com/trade-api/v2/series/fee_changes?series_ticker='+CITIES[city]['series']+'&show_historical=true')
    # Fee changes require reviewed interpretation; never ignore an unknown row.
    if changes.get('series_fee_change_arr'):raise ValueError('Series fee-change history requires reviewed admission')
    proof=dict(schema=SCHEMA,date=day,city=city,station=station,series=CITIES[city]['series'],producer_workdir=str(Path.cwd().resolve()),
        event_ticker=market['event_ticker'],rules_sha256=rules_hash(market['contracts']),
        admitted_for_paper=True,physical_to_settlement_equivalence_guaranteed=False,
        hypothesis='Same named whole-F daily climate scalar; checked against independent official publishers. Future revisions remain possible.',
        pairs=pairs,market_receipt=market['receipt'],contract_terms_receipt=terms_r,
        nws_index_receipt=index_r,received_ns=time.time_ns(),valid_through_ns=int(climate_window(station,day)[1].timestamp()*1e9),
        fee_policy=dict(coefficient='.07',multiplier='1',verified=True,available_ns=series_r['received_ns'],
                        valid_from_ns=series_r['received_ns'],valid_through_ns=time.time_ns()+24*3600*NS,
                        source_ref=FEE_URL+'#sha256='+registration['fee_schedule_sha256'],
                        schedule_receipt=fee_r,series_receipt=series_r,changes_receipt=changes_r,
                        retained_document={'path':registration['retained_fee_path'],'sha256':registration['fee_schedule_sha256'],
                            'original_retrieved_utc':registration['retained_fee_original_retrieved_utc'],'new_download_clock_claimed':False},
                        private_account_fee_precision_verified=False,verified_scope='PUBLIC_FORMULA_AND_SERIES_FOR_NATIVE_PAPER_SIMULATION',
                        convention='Preserved native paper rounding; retail-cent variants are conservative versus centicent fees'))
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(proof,sort_keys=True,indent=2,allow_nan=False).encode()
    path=root/(city+'-'+day+'-'+hashlib.sha256(raw).hexdigest()[:16]+'-mapping.json')
    if not path.exists():path.write_bytes(raw)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}

def verify(binding, city, day, contracts, now_ns, *, verify_sources=True):
    if not isinstance(binding,dict):raise ValueError('Dated source mapping required')
    raw=Path(binding['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=binding['sha256']:raise ValueError('Source mapping changed')
    p=json.loads(raw)
    if (p.get('schema')!=SCHEMA or p.get('city')!=city or p.get('date')!=day or p.get('station')!=CITIES[city]['station'] or p.get('series')!=CITIES[city]['series'] or
        p.get('admitted_for_paper') is not True or not p['received_ns']<=now_ns<=p['valid_through_ns'] or p['rules_sha256']!=rules_hash(contracts) or len(p.get('pairs',[]))<3):
        raise ValueError('Mapping scope/clock/station/rules unsupported')
    if verify_sources:
        registration=json.loads(Path(__file__).with_name('public-policy-registration.json').read_bytes())
        document=p['fee_policy']['retained_document']
        if document['sha256']!=registration['fee_schedule_sha256'] or digest(document['path'])!=document['sha256']:raise ValueError('Retained official fee bytes changed')
        if p['contract_terms_receipt']['response_sha256']!=registration['contract_terms_sha256']:raise ValueError('Unregistered contract terms')
        fee=p['fee_policy']
        if fee.get('verified') is not True or Decimal(fee['coefficient'])!=Decimal('.07') or Decimal(fee['multiplier'])!=1 or not fee['available_ns']<=now_ns<=fee['valid_through_ns']:raise ValueError('Fee policy scope or clock differs')
        for receipt in [p['market_receipt'],p['contract_terms_receipt'],p['nws_index_receipt'],p['fee_policy']['schedule_receipt'],p['fee_policy']['series_receipt'],p['fee_policy']['changes_receipt']]+[r for x in p['pairs'] for r in (x['nws_receipt'],x['twc_receipt'])]:
            if receipt:checked_receipt(receipt,p.get('producer_workdir'))
        series=json.loads(checked_receipt(fee['series_receipt'],p.get('producer_workdir')).read_bytes())['series']
        if series.get('ticker')!=p['series'] or series.get('fee_type')!='quadratic' or Decimal(str(series.get('fee_multiplier')))!=1:raise ValueError('Actual series fee differs')
        if json.loads(checked_receipt(fee['changes_receipt'],p.get('producer_workdir')).read_bytes()).get('series_fee_change_arr'):raise ValueError('Unreviewed fee change')
        days=[]
        for pair in p['pairs']:
            nws=parse_cli(json.loads(checked_receipt(pair['nws_receipt'],p.get('producer_workdir')).read_bytes()),city)
            twc=json.loads(checked_receipt(pair['twc_receipt'],p.get('producer_workdir')).read_bytes())
            rows=[x for x in twc.get('results',[]) if x.get('station',{}).get('icao')==p['station']]
            if len(rows)!=1:raise ValueError('Paired report station differs')
            row=rows[0];data=row.get('data') or {}
            if nws['day']!=pair['day'] or twc.get('date')!=pair['day'] or data.get('reportDate')!=pair['day'] or row.get('status') not in ('official','revised') or data.get('isOfficial') is not True or data.get('stationId')!=CLI[city] or data.get('maxTemp')!=nws['high_f'] or pair['high_f']!=nws['high_f']:raise ValueError('Independent official pair differs')
            if not (date.fromisoformat(day)-timedelta(days=7)).isoformat()<=pair['day']<day:raise ValueError('Pair outside recent completed days')
            days.append(pair['day'])
        if len(set(days))!=len(days) or max(days)!=(date.fromisoformat(day)-timedelta(days=1)).isoformat():raise ValueError('Latest completed day or unique pairs missing')
    return p
