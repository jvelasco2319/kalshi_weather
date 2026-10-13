"""Explicit reviewed temporal admission; no fee fitting or live-account claim.

The source-only helper extends the copied native date-validity field only.
An independent metadata review must bind authentic captured fee evidence and
the finite dates first. The saved original fee rule is never modified.
"""
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
from urllib.request import Request,urlopen
from urllib.parse import urlparse
import hashlib,json,time,os
from datetime import timedelta

SCOPE='LA_OFFLINE_CONDITIONAL_FEE_TEMPORAL_EXTENSION_V1'
PRIMARY_PDF_SHA='c326a69f596a11e8f8be2620402d39a8d4823920c21cc97c93a114d862699601'
PRIMARY_MANIFEST_SHA='a5d7a57f8a41ee2d729cdcfad314cff1a6100cc36ce94b57e519523019b97cc2'
PRIMARY_PDF_URL='https://kalshi.com/docs/kalshi-fee-schedule.pdf'
SERIES_URL='https://external-api.kalshi.com/trade-api/v2/series/KXHIGHLAX'
CHANGES_URL='https://external-api.kalshi.com/trade-api/v2/series/fee_changes?series_ticker=KXHIGHLAX&show_historical=true'
PRODUCER_MODE='PREAPPROVED_AUTHENTIC_METADATA_PRODUCER'
RECIPE=dict(coefficient='.07',multiplier='1',rounding='RETAIL_CENT',fee_type='quadratic',series='KXHIGHLAX')
LIMITATIONS=['HISTORICAL_FEE_EFFECTIVE_DATE_COVERAGE_UNPROVEN','PRIVATE_ACCOUNT_PRECISION_AND_COUNTERPARTY_SPLITTING_UNPROVEN','CONDITIONAL_ARCHIVE_ONLY_NOT_LIVE_FEE_VERIFICATION']
PRODUCER_REVIEW=Path('C:/Users/jarve/Documents/Codex/Kalshi Temp Bot/Dashboard/runtime/offline-preparation-20261012/la-fee-temporal-producer-review-v1.json')

def _read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'),parse_float=str)
def _unchanged_series(value):
    if not isinstance(value,dict) or not isinstance(value.get('series'),dict):return False
    series=value['series']
    try:return series.get('ticker')=='KXHIGHLAX' and series.get('fee_type')=='quadratic' and Decimal(str(series.get('fee_multiplier')))==1
    except (ValueError,TypeError,ArithmeticError):return False
def _empty_changes(value):return isinstance(value,dict) and isinstance(value.get('series_fee_change_arr'),list) and not value['series_fee_change_arr']
def _pin(path):
    path=Path(path).resolve();before=path.stat();h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(2*1024*1024),b''):h.update(chunk)
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Fee source changed during pin')
    return dict(path=str(path),sha256=h.hexdigest(),bytes=after.st_size)

def _retained_primary_pdf(receipt,raw):
    """Authenticate an old capture without inventing exact HTTP clocks."""
    manifest_pin=receipt.get('original_manifest_pin')
    if receipt.get('sha256')!=raw['sha256'] or receipt.get('bytes')!=raw['bytes']:raise ValueError('Retained PDF raw/receipt hash or bytes changed')
    if manifest_pin is None and receipt.get('source_class')=='RETAINED_ORIGINAL_PRIMARY_HTTP_CAPTURE':
        if receipt.get('original_source_manifest_sha256')!=PRIMARY_MANIFEST_SHA or receipt.get('new_http_request_performed') is not False or receipt.get('new_http_receipt_clock_claimed') is not False or receipt.get('exact_original_request_start_ns_unavailable') is not True:raise ValueError('Retained original-capture qualification changed')
        manifest_pin=_pin(receipt['original_source_manifest_path'])
    if not manifest_pin or manifest_pin.get('sha256')!=PRIMARY_MANIFEST_SHA or _pin(manifest_pin['path'])!=manifest_pin:raise ValueError('Retained PDF original manifest is unbound')
    manifest=_read(manifest_pin['path']);index=receipt.get('original_source_entry_index')
    if index is None:
        candidates=[i for i,x in enumerate(manifest['sources']) if x.get('sha256')==PRIMARY_PDF_SHA and x.get('bytes')==281129 and x.get('final_url')==PRIMARY_PDF_URL]
        if len(candidates)!=1:raise ValueError('Retained PDF primary source entry is ambiguous')
        index=candidates[0]
    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(manifest['sources']):raise ValueError('Retained PDF exact original source entry required')
    entry=manifest['sources'][index]
    if entry.get('authority')!='kalshi_official' or str(entry.get('status'))!='200' or entry.get('final_url')!=PRIMARY_PDF_URL or entry.get('sha256')!=PRIMARY_PDF_SHA or entry.get('bytes')!=281129:raise ValueError('Retained PDF primary capture entry differs')
    if raw['sha256']!=PRIMARY_PDF_SHA or raw['bytes']!=281129 or receipt.get('source_url')!=PRIMARY_PDF_URL:raise ValueError('Retained primary PDF raw bytes differ original capture')
    if receipt.get('original_capture_completion_utc',receipt.get('original_retrieved_at_utc'))!=manifest.get('retrieved_at_utc'):raise ValueError('Retained PDF capture-completion proxy changed')
    reused=receipt.get('actual_reuse_ns',time.time_ns())
    if isinstance(reused,bool) or not isinstance(reused,int) or not 0<reused<=time.time_ns():raise ValueError('Actual retained PDF local reuse clock required')
    if 'started_ns' in receipt or 'received_ns' in receipt:raise ValueError('Retained campaign clock must not be relabeled exact HTTP receipt/start')
    return dict(source_type='REUSED_PINNED_PRIMARY_CAPTURE',original_manifest_pin=manifest_pin,original_source_entry_index=index,original_capture_completion_utc=manifest['retrieved_at_utc'],capture_clock_basis='ORIGINAL_CAMPAIGN_COMPLETION_PROXY_NOT_EXACT_HTTP_TIMING',actual_reuse_ns=reused,pdf_sha256=PRIMARY_PDF_SHA)

def refresh_temporal_metadata(runtime_root,climate_days=None,producer_review_path=None):
    """At an authorized Offline History click, renew two public GET captures.

    This deterministic producer is independently reviewed once by exact code
    hash. Each invocation seals new raw evidence/admission before any financial
    run. It does not claim another independent review occurred on that day.
    """
    today=datetime.now(ZoneInfo('America/Los_Angeles')).date();origin=date(2026,10,1);default=[(origin+timedelta(days=i)).isoformat() for i in range((today-origin).days+1)];days=sorted(set(climate_days or default))
    if not days or any(date.fromisoformat(d).isoformat()!=d or d[:7]!='2026-10' or date.fromisoformat(d)>today for d in days):raise ValueError('Finite past/current Pacific October days required')
    review_path=Path(producer_review_path or os.environ.get('OFFLINE_LA_FEE_REVIEW_PATH',str(PRODUCER_REVIEW)));review=_read(review_path);producer=_pin(__file__)
    if review.get('passes') is not True or review.get('scope')!=SCOPE or review.get('review_mode')!=PRODUCER_MODE or review.get('metadata_producer_pin',{}).get('sha256')!=producer['sha256'] or not set(days)<=set(review.get('climate_days',[])) or review.get('unchanged_recipe')!=RECIPE or not set(LIMITATIONS)<=set(review.get('limitations',[])):raise ValueError('Authentic fee metadata producer lacks exact static independent review')
    folder=Path(runtime_root)/'sources/la/fee-metadata';pdf=folder/'general-fee.pdf';pdf_receipt=folder/'general-fee-receipt.json';raw_pdf=_pin(pdf);retained=_retained_primary_pdf(_read(pdf_receipt),raw_pdf)
    if review.get('retained_pdf_sha256')!=PRIMARY_PDF_SHA or review.get('retained_manifest_sha256')!=PRIMARY_MANIFEST_SHA:raise ValueError('Producer static review does not bind retained primary PDF')
    attempt_ns=time.time_ns();capture=folder/'captures'/str(attempt_ns);capture.mkdir(parents=True,exist_ok=False);refs=[]
    def write(path,value):
        with path.open('x',encoding='utf8') as f:json.dump(value,f,indent=2)
    def latest(value):
        p=folder/('fee-metadata-latest-attempt.'+str(time.time_ns())+'.tmp');write(p,value);os.replace(p,folder/'fee-metadata-latest-attempt.json')
    latest(dict(status='STARTED',started_ns=attempt_ns,climate_days=days))
    try:
        for stem,url in (('series',SERIES_URL),('fee-changes',CHANGES_URL)):
            started=time.time_ns()
            with urlopen(Request(url,headers={'User-Agent':'TempDesk conditional offline fee metadata'}),timeout=30) as response:
                body=response.read(4_000_001);received=time.time_ns()
                if len(body)>4_000_000 or response.status!=200 or response.geturl()!=url:raise ValueError('Exact official successful bounded fee GET required')
                receipt=dict(source_url=url,started_ns=started,received_ns=received,http_status=response.status,http_date=response.headers.get('Date'),sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),method='GET',source_class='ACTUAL_FRESH_OFFICIAL_PUBLIC_GET',current_metadata_not_historical_account_proof=True)
            raw_path=capture/(stem+'.json');raw_path.write_bytes(body);receipt_path=capture/(stem+'-receipt.json');write(receipt_path,receipt);refs.extend([_pin(raw_path),_pin(receipt_path)])
        series=_read(capture/'series.json');changes=_read(capture/'fee-changes.json')
        if not _unchanged_series(series):raise ValueError('Current series changed or omitted the frozen assumed fee recipe')
        if not _empty_changes(changes):raise ValueError('Fee change schedule present or unknown; separate review required')
        refs.extend([raw_pdf,_pin(pdf_receipt)])
        admission=dict(scope=SCOPE,passes=True,new_fits=0,climate_days=days,unchanged_recipe=RECIPE,limitations=LIMITATIONS,source_pins=refs,independent_review_pin=_pin(review_path),metadata_producer_pin=producer,validation_kind='DETERMINISTIC_INPUT_CHECK_BY_STATIC_REVIEWED_PRODUCER',new_daily_independent_review_claimed=False,retained_primary_pdf=retained,produced_ns=time.time_ns(),counterfactual_only=True,live_fee_eligible=False)
        admissions=folder/'admissions';admissions.mkdir(parents=True,exist_ok=True);path=admissions/(days[-1]+'-'+str(attempt_ns)+'.json');write(path,admission)
        pointer=dict(schema='LA_FEE_TEMPORAL_ADMISSION_POINTER_V1',immutable_admission_pin=_pin(path));temporary=folder/('temporal-admission-current.'+str(attempt_ns)+'.tmp');write(temporary,pointer);os.replace(temporary,folder/'temporal-admission-current.json')
        write(capture/'finish.json',dict(status='ADMITTED_CONDITIONAL_UNCHANGED_TEMPORAL_RECIPE',started_ns=attempt_ns,finished_ns=time.time_ns(),admission_pin=_pin(path),source_pins=refs,financial_runs=0))
        latest(dict(status='ADMITTED',started_ns=attempt_ns,finished_ns=time.time_ns(),admission_pin=_pin(path)))
        return admission
    except Exception as error:
        write(capture/'failure.json',dict(status='FAILED_FEE_METADATA_ADMISSION',started_ns=attempt_ns,finished_ns=time.time_ns(),error_type=type(error).__name__,reason=str(error),source_pins=refs,financial_runs=0))
        latest(dict(status='FAILED',started_ns=attempt_ns,finished_ns=time.time_ns(),failure_pin=_pin(capture/'failure.json')))
        raise

def apply_temporal_admission(day,fee_policy,runtime_root):
    """Return (new fee dictionary, explicit admission receipt, source pins).

    Missing/failed metadata is a reasoned capability gap, never an implicit
    extension. The outer backend translates FileNotFoundError to evidence gap.
    """
    folder=Path(runtime_root)/'sources/la/fee-metadata';admission_path=folder/'temporal-admission-current.json'
    if not admission_path.is_file():admission_path=folder/'temporal-admission.json'
    admission=_read(admission_path)
    if admission.get('schema')=='LA_FEE_TEMPORAL_ADMISSION_POINTER_V1':
        ref=admission['immutable_admission_pin']
        if not Path(ref['path']).resolve().is_relative_to((folder/'admissions').resolve()) or _pin(ref['path'])!=ref:raise ValueError('Temporal fee admission pointer is unbound')
        admission_path=Path(ref['path']);admission=_read(admission_path)
    if admission.get('passes') is not True or admission.get('scope')!=SCOPE or admission.get('new_fits')!=0 or day not in admission.get('climate_days',[]):raise ValueError('Missing finite reviewed fee temporal admission')
    if day[:7]!='2026-10' or date.fromisoformat(day)>datetime.now(ZoneInfo('America/Los_Angeles')).date():raise ValueError('Fee temporal admission outside observed October scope')
    expected=RECIPE
    if admission.get('unchanged_recipe')!=expected:raise ValueError('Temporal admission changed the saved native fee recipe')
    if Decimal(str(fee_policy['coefficient']))!=Decimal('.07') or Decimal(str(fee_policy['multiplier']))!=1 or fee_policy['rounding']!='RETAIL_CENT':raise ValueError('Original native fee profile differs temporal recipe')
    uncertainty=admission.get('limitations',[])
    required=set(LIMITATIONS)
    if not required<=set(uncertainty):raise ValueError('Fee admission must preserve effective-date/account uncertainty')
    review=admission.get('independent_review_pin')
    if not review or _pin(review['path'])['sha256']!=review['sha256']:raise ValueError('Fee temporal metadata review unbound')
    reviewed=_read(review['path'])
    if reviewed.get('passes') is not True or reviewed.get('scope')!=SCOPE:raise ValueError('Fee temporal review has not passed')
    if not set(admission['climate_days'])<=set(reviewed.get('climate_days',[])) or reviewed.get('unchanged_recipe')!=expected or not required<=set(reviewed.get('limitations',[])):raise ValueError('Fee metadata admission exceeds independently reviewed dates/recipe/limitations')
    producer_mode=reviewed.get('review_mode')==PRODUCER_MODE
    if producer_mode and (reviewed.get('metadata_producer_pin',{}).get('sha256')!=_pin(__file__)['sha256'] or admission.get('metadata_producer_pin',{}).get('sha256')!=_pin(__file__)['sha256']):raise ValueError('Fee deterministic producer changed after static independent review')
    latest_path=folder/'fee-metadata-latest-attempt.json'
    if producer_mode and latest_path.is_file():
        latest=_read(latest_path)
        if latest.get('status')!='ADMITTED' and latest.get('started_ns',0)>=admission.get('produced_ns',0):raise ValueError('Latest fee metadata refresh is unfinished/failed; older admission fallback forbidden')
    source_refs=admission.get('source_pins',[]);by={Path(r['path']).name:r for r in source_refs}
    required_names=['series.json','series-receipt.json','fee-changes.json','fee-changes-receipt.json','general-fee.pdf','general-fee-receipt.json']
    if any(name not in by for name in required_names):raise ValueError('Fee source closure incomplete')
    for r in source_refs:
        if not Path(r['path']).resolve().is_relative_to(folder.resolve()) or _pin(r['path'])!=r:raise ValueError('Captured fee source differs finite metadata pin')
    declared={str(Path(r['path']).resolve()):r['sha256'] for r in reviewed.get('source_pins',[])}
    if not producer_mode and any(declared.get(str(Path(r['path']).resolve()))!=r['sha256'] for r in source_refs):raise ValueError('Fee raw sources absent from independent review')
    clocks=[];retained_pdf=None
    for stem in ('series','fee-changes','general-fee'):
        raw=by[stem+'.pdf' if stem=='general-fee' else stem+'.json'];receipt=_read(by[stem+'-receipt.json']['path']);url=urlparse(receipt.get('source_url',''))
        if receipt.get('sha256')!=raw['sha256'] or receipt.get('bytes')!=raw['bytes']:raise ValueError('Fee capture bytes/hash differ actual receipt')
        if url.scheme!='https' or url.hostname not in ('external-api.kalshi.com','api.elections.kalshi.com','kalshi.com','www.kalshi.com'):raise ValueError('Fee source is not an official public endpoint')
        if stem=='general-fee' and (receipt.get('source_type')=='REUSED_PINNED_PRIMARY_CAPTURE' or receipt.get('source_class')=='RETAINED_ORIGINAL_PRIMARY_HTTP_CAPTURE'):
            retained_pdf=_retained_primary_pdf(receipt,raw);continue
        if stem in ('series','fee-changes') and receipt.get('source_url')!=({'series':SERIES_URL,'fee-changes':CHANGES_URL}[stem]):raise ValueError('Wrong fee metadata endpoint')
        if producer_mode and stem in ('series','fee-changes') and (receipt.get('method')!='GET' or receipt.get('source_class')!='ACTUAL_FRESH_OFFICIAL_PUBLIC_GET' or receipt.get('http_status')!=200):raise ValueError('Actual producer HTTP200 GET receipt required')
        if not isinstance(receipt.get('started_ns'),int) or not isinstance(receipt.get('received_ns'),int) or not 0<receipt['started_ns']<=receipt['received_ns']<=time.time_ns():raise ValueError('Actual fee capture clocks required')
        if datetime.fromtimestamp(receipt['received_ns']//10**9,timezone.utc).date()<date.fromisoformat(day):raise ValueError('Fee capture predates temporal extension date')
        clocks.append(receipt['received_ns'])
    series=_read(by['series.json']['path'])
    if not _unchanged_series(series):raise ValueError('Actual series fee metadata differs or omits unchanged recipe')
    changes=_read(by['fee-changes.json']['path'])
    if not _empty_changes(changes):raise ValueError('Fee change schedule present or unknown; separate review required')
    output=deepcopy(fee_policy);old=output['valid_through_utc'];output['valid_through_utc']=day+'T20:00:05Z';output['source_ref']+=' | '+SCOPE+'; unchanged .07 retail-cent recipe, conditional effective-date/account precision assumptions'
    receipt=dict(scope=SCOPE,climate_date=day,changes={'valid_through_utc':{'from':old,'to':output['valid_through_utc']},'source_ref_metadata_only':True},unchanged_recipe=expected,actual_metadata_capture_ns=max(clocks),retained_primary_pdf=retained_pdf,limitations=uncertainty,admission_pin=_pin(admission_path),independent_review_pin=deepcopy(review),review_basis='STATIC_REVIEWED_PRODUCER_AND_NEW_AUTHENTIC_INPUT_VALIDATION; no new daily independent review claimed' if producer_mode else 'FINITE_CAPTURE_SPECIFIC_INDEPENDENT_REVIEW',historical_account_fee_verified=False,live_fee_eligible=False,new_fits=0)
    return output,receipt,source_refs+[_pin(admission_path),_pin(review['path'])]+([retained_pdf['original_manifest_pin']] if retained_pdf else [])
