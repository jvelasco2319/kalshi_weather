"""Bounded retry/cache overlay for the exact frozen LA NOAA point decoder."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone
from copy import deepcopy
import hashlib, json, threading, time, uuid

def collect(request, original):
    day=request['day'];root=Path(request['capture_dir']).resolve()
    runtime=Path(__import__('os').environ['TEMP_DESK_RUNTIME_ROOT']).resolve()
    if not root.is_relative_to(runtime):raise ValueError('Weather capture outside paper runtime')
    funcs=original._collector();fetch=funcs['fetch'];limited=threading.BoundedSemaphore(2)
    def retry(*args,**kwargs):
        with limited:
            for attempt in range(4):
                try:return fetch(*args,**kwargs)
                except Exception as e:
                    code=getattr(getattr(e,'response',None),'status_code',None)
                    if attempt==3 or code not in (429,500,502,503,504):raise
                    time.sleep(.5*2**attempt)
    funcs['fetch']=retry
    previous=request.get('cached_packet',{}).get('forecasts',{})
    old={}
    if previous.get('climate_date')==day:
        for row in previous.get('forecasts',[]):
            try:
                original._raw_identity(row)
                decoded=funcs['decode'](Path(row['source_raw_path']).read_bytes(),
                    {'issued':row['nominal_issue_time_utc'],'model':row['model'],'member':row.get('member_id'),'lead':row['lead_hours']},row['field_id'])
                if any(row.get(k)!=v for k,v in decoded.items()):continue
                index=Path(row['index_raw_path'])
                if hashlib.sha256(index.read_bytes()).hexdigest()!=row['index_sha256']:continue
                old[(row['model'],row.get('member_id'),row['lead_hours'],row['field_id'])]=row
            except (ValueError,OSError,KeyError):continue
    folder=root/'weather'/day/('repair-'+uuid.uuid4().hex);errors=[];rows=list(old.values())
    planned=[]
    for item in funcs['plan'](day):
        fields=[f for f in item['fields'] if (item['model'],item['member'],item['lead'],f) not in old]
        if fields:planned.append({**item,'fields':fields})
    with ThreadPoolExecutor(max_workers=2) as pool:
        for job in as_completed([pool.submit(funcs['collect_object'],s,day,folder) for s in planned]):
            try:
                values,failures=job.result();errors.extend(failures)
                for row in values:
                    try:
                        row.update(funcs['decode'](row.pop('_body'),row.pop('_source'),row['field_id']));rows.append(row)
                    except Exception as e:errors.append({'reason':str(e)})
            except Exception as e:errors.append({'reason':str(e)})
    rows.sort(key=lambda r:(r['model'],r.get('member_id') or '',r['lead_hours'],r['field_id']))
    count=sum(r['field_id']=='temperature_2m' for r in rows)
    forecasts=dict(climate_date=day,forecasts=rows,errors=errors,required_temperature_count=count,
                   complete=count==20,completed_utc=datetime.now(timezone.utc).isoformat(),fits=0)
    observations=funcs['collect_observations'](day,root)
    frame=dict(climate_date=day,forecasts=rows,observations=observations['observations'],
               observation_target_compatible=False,availability_mode='actual_receipt')
    original.save(root/'weather'/day/'latest-forecasts.json',forecasts)
    return dict(climate_date=day,status='PUBLIC_WEATHER_CAPTURED' if count==20 else 'INCOMPLETE',
                forecasts=forecasts,observations=observations,frame=frame,weather_root=str(root),
                complete=count==20,actual_public_receipts=True,reused_verified_fields=len(old),
                retry_limit=4,maximum_HTTP_concurrency=2,fits=0,orders=0)
