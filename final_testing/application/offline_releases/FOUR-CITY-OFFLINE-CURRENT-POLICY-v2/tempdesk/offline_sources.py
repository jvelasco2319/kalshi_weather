"""Read-only archive acquisition. Credentials stay in the local server's RAM.

Raw downloads are evidence, not a simulated fill or an eligibility receipt.
City adapters must admit them using their original archive rules.
"""
from pathlib import Path
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
import hashlib, json, os, re, threading, time, uuid

SERIES = {'la':'KXHIGHLAX', 'ny':'KXHIGHNY', 'miami':'KXHIGHMIA', 'phoenix':'KXHIGHTPHX'}
_locks = {}
_guard = threading.Lock()

def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + str(os.getpid()) + '.' + str(threading.get_ident()) + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf8')
    # Windows can briefly hold a progress/history file while the UI reads it.
    # Retain the old file until a successful atomic replacement; never truncate.
    for attempt in range(6):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 5:raise
            time.sleep(.02 * 2**attempt)

def digest_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1_048_576),b''):h.update(block)
    return h.hexdigest()

def verified_capture(path, receipt_path):
    try:
        receipt=json.loads(Path(receipt_path).read_bytes())
        return Path(path).is_file() and digest_file(path)==receipt['sha256']
    except (OSError,ValueError,KeyError):return False

def save_capture(out,name,raw,receipt):
    """Keep each original capture, even when the latest cache is refreshed."""
    attempt=Path(out)/'captures'/(str(time.time_ns())+'-'+uuid.uuid4().hex[:8]);attempt.mkdir(parents=True)
    immutable=attempt/name;immutable.write_bytes(raw)
    receipt={**receipt,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'immutable_path':str(immutable)}
    receipt_name=Path(name).stem+'-receipt.json'
    atomic_json(attempt/receipt_name,receipt)
    (Path(out)/name).write_bytes(raw);atomic_json(Path(out)/receipt_name,receipt)
    return receipt

def _get(url, maximum=16_000_000):
    begin = time.time_ns()
    with urlopen(Request(url, headers={'User-Agent':'TempDesk offline historical research'}), timeout=40) as r:
        raw = r.read(maximum + 1)
        if len(raw)>maximum: raise ValueError('Source response exceeds bounded download size')
        receipt = {'started_ns':begin, 'received_ns':time.time_ns(), 'http_date':r.headers.get('Date'),
                   'source_url':url, 'sha256':hashlib.sha256(raw).hexdigest(), 'bytes':len(raw)}
    return raw, receipt

def query(sql, runtime_root, suffix='jsonl'):
    config = Path(runtime_root)/'archive-connection.json'
    if not config.is_file(): raise RuntimeError('CONNECTION_REQUIRED')
    conf = json.loads(config.read_bytes())
    url = conf['query_url']
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]+/api/offline/query', url):
        raise ValueError('Archive broker must be the local offline service')
    req = Request(url, data=json.dumps({'sql':sql}).encode(),
                  headers={'Content-Type':'application/json', 'Authorization':'Bearer '+conf['token']}, method='POST')
    from urllib.error import HTTPError
    begin = time.time_ns()
    try:
        with urlopen(req, timeout=240) as r:
            raw=r.read(536_870_913)
            if len(raw)>536_870_912: raise ValueError('Archive exceeds bounded response size')
    except HTTPError as e:
        if e.code in (401,409): raise RuntimeError('CONNECTION_REQUIRED') from None
        raise RuntimeError('ARCHIVE_QUERY_FAILED_HTTP_'+str(e.code)) from None
    return raw, {'started_ns':begin,'received_ns':time.time_ns(),'sha256':hashlib.sha256(raw).hexdigest(),
                 'bytes':len(raw),'source':'PROBALYTICS_NATIVE_ARCHIVE','format':suffix,
                 'actual_historical_delivery_proven':False}

def replay_book_window(city, day):
    """Download every revision at the fixed traders' required quote times.

    NY/Miami/Phoenix cannot admit a quote older than five seconds. No quality
    filter or argMax discards invalid latest states or conflicting tied copies.
    LA retains the entire 30-minute lookback and 17..20 UTC minute replay span.
    This covers these frozen replays, not arbitrary intraday strategy research.
    """
    if city=='ny':
        return day+' 13:59:55',day+' 17:00:05'," AND (toSecond(orderbook_snapshots.timestamp)>=55 OR toSecond(orderbook_snapshots.timestamp)<=5)"
    if city=='miami':return day+' 13:59:55',day+' 14:00:05',''
    if city=='la':return day+' 16:29:55',day+' 20:00:05',''
    if city=='phoenix':
        from datetime import timedelta
        spans=[]
        for clock in ('17:00','17:15','17:30','18:00','19:00'):
            at=datetime.fromisoformat(day+'T'+clock+':00')
            spans.append("(timestamp >= '"+(at-timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')+"' AND timestamp <= '"+(at+timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')+"')")
        return day+' 16:59:55',day+' 19:00:05',' AND ('+' OR '.join(spans)+')'
    raise ValueError('Unknown replay city')

def acquire_day(city, day, runtime_root, force=False, official_only=False):
    """Download six official definitions and bounded full-day native archive books.

    The archive contains both outcome sides, timestamps, indexing and integrity
    fields. No last-price bars substitute for executable depth. A row limit hit
    is incomplete evidence. Unsettled official results remain pending.
    """
    if city not in SERIES: raise ValueError('Unknown city')
    d=date.fromisoformat(day)
    if d.isoformat()!=day or d<date(2026,10,1): raise ValueError('Date outside history scope')
    root=Path(runtime_root).resolve(); out=root/'sources'/city/day
    with _guard: lock=_locks.setdefault((str(root),city,day),threading.Lock())
    with lock:
        out.mkdir(parents=True,exist_ok=True)
        status_path=out/'source-status.json'
        old=json.loads(status_path.read_bytes()) if status_path.is_file() else {}
        event=SERIES[city]+'-'+d.strftime('%y%b%d').upper()
        market_path=out/'official-event.json'; market_receipt=out/'official-event-receipt.json'
        market_valid=verified_capture(market_path,market_receipt)
        existing=json.loads(market_path.read_bytes()) if market_valid else {}
        settled=bool(existing.get('markets')) and all(m.get('result') in ('yes','no') and m.get('status') in ('settled','finalized') for m in existing['markets'])
        errors=[]
        if force or not settled:
            try:
                raw, receipt=_get('https://external-api.kalshi.com/trade-api/v2/events/'+event)
                data=json.loads(raw)
                if len(data.get('markets',[]))!=6 or any(not m['ticker'].startswith(event+'-') for m in data['markets']):
                    raise ValueError('Exact six dated native markets required')
                save_capture(out,'official-event.json',raw,receipt); existing=data
            except Exception as e: errors.append('OFFICIAL_SOURCE_'+type(e).__name__)
        books_path=out/'native-books.jsonl'; books_receipt=out/'native-books-receipt.json'
        # Completed past-day captures are reused. Today's archive remains open.
        utc_today=datetime.now(timezone.utc).date()
        books_valid=verified_capture(books_path,books_receipt)
        reuse=books_valid and old.get('books_complete') and old.get('official_final') and d<utc_today and not force
        complete=bool(old.get('books_complete') and books_valid) if official_only else bool(reuse)
        if not reuse and not official_only:
            try:
                identity_sql=("SELECT id, platform_id, toString(indexed_at) AS indexed_at FROM markets PREWHERE platform='KALSHI' "
                              "WHERE startsWith(platform_id,'"+event+"-') FORMAT JSONEachRow")
                raw, receipt=query(identity_sql,root)
                (out/'market-identities.jsonl').write_bytes(raw); atomic_json(out/'identities-receipt.json',receipt)
                by={}
                for line in raw.decode().splitlines():
                    row=json.loads(line)
                    if row['platform_id'] not in by or row['indexed_at']>by[row['platform_id']]['indexed_at']: by[row['platform_id']]=row
                expected={m['ticker'] for m in existing.get('markets',[])}
                if len(expected)!=6 or set(by)!=expected: raise ValueError('Archived market identities do not match official six brackets')
                from uuid import UUID
                ids=','.join("'"+str(UUID(row['id']))+"'" for row in by.values())
                fields='market_id,market_platform_id,platform,outcome,bids,asks,toString(timestamp) AS timestamp,toString(indexed_at) AS indexed_at,toString(hash) AS hash,state,continuity,path_index'
                # Only the trader's full life window is relevant. Include the
                # five-second source-age lookback and the final arrival bound.
                # NYC's DST-aware local10am..1pm window is expanded to13..18UTC;
                # Miami needs its local10am decision plus five-second arrival.
                source_start,source_end,clock_filter=replay_book_window(city,day)
                sql=("SELECT "+fields+" FROM orderbook_snapshots PREWHERE market_id IN ("+ids+") "
                     "WHERE timestamp >= '"+source_start+"' AND timestamp <= '"+source_end+"' "
                     +clock_filter+" ORDER BY market_platform_id,timestamp,indexed_at,path_index LIMIT 500001 FORMAT JSONEachRow")
                (out/'native-books.sql').write_text(sql,encoding='utf8')
                raw, receipt=query(sql,root)
                rows=sum(1 for line in raw.splitlines() if line.strip())
                if rows>500000: raise ValueError('Archive row ceiling reached; capture incomplete')
                receipt['rows']=rows
                end_at=datetime.strptime(source_end,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                complete=rows>0 and end_at<datetime.now(timezone.utc)
                receipt['source_start_utc']=source_start;receipt['source_end_utc']=source_end
                receipt['selection_scope']='FIXED_NATIVE_REPLAY_DECISION_AND_ARRIVAL_WINDOWS; all raw revisions, ties, sides and depth within these windows; not full-day research coverage'
                save_capture(out,'native-books.jsonl',raw,receipt)
            except Exception as e:
                errors.append('CONNECTION_REQUIRED' if str(e)=='CONNECTION_REQUIRED' else 'BOOK_SOURCE_'+type(e).__name__)
        settled=bool(existing.get('markets')) and all(m.get('result') in ('yes','no') and m.get('status') in ('settled','finalized') for m in existing['markets'])
        market_valid=verified_capture(market_path,market_receipt);books_valid=verified_capture(books_path,books_receipt)
        market_ref=json.loads(market_receipt.read_bytes()) if market_valid else {}
        book_ref=json.loads(books_receipt.read_bytes()) if books_valid else {}
        result={'city':city,'day':day,'event':event,'status':'DOWNLOADED' if market_valid and (complete or official_only) else 'PENDING',
                'market_path':market_ref.get('immutable_path',str(market_path)) if market_valid else None,
                'market_receipt_path':str(Path(market_ref['immutable_path']).parent/'official-event-receipt.json') if market_ref.get('immutable_path') else str(market_receipt) if market_valid else None,
                'books_path':book_ref.get('immutable_path',str(books_path)) if books_valid else None,
                'books_receipt_path':str(Path(book_ref['immutable_path']).parent/'native-books-receipt.json') if book_ref.get('immutable_path') else str(books_receipt) if books_valid else None,
                'books_complete':complete,'official_final':settled,'errors':errors,
                'books_requested':not official_only,
                'checked_ns':time.time_ns(),'historical_delivery_proven':False}
        atomic_json(status_path,result)
        return result
