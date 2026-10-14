"""Actual native paper clocks, readiness and diagnostic metadata.

Clocks are never widened and a missed slot is never replayed as an online buy.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

NS = 10**9
MINUTE = 60*NS
POLICY_VERSION = 'FOUR_CITY_BUYING_RELIABILITY_v1'

def midnight(day):
    return int(datetime.fromisoformat(day+'T00:00:00+00:00').timestamp())*NS

def anchor(city, day):
    if city in ('ny','miami'):
        return int(datetime.fromisoformat(day+'T10:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp())*NS
    return midnight(day)+17*3600*NS

def entry_slots(profile, day):
    city=profile['city'];pid=profile['profile_id'];begin=anchor(city,day)
    if city=='miami' or city=='ny' and pid=='CONSERVATIVE_P09':return [begin]
    if city=='ny':return list(range(begin,begin+3*3600*NS+MINUTE,MINUTE))
    if city=='phoenix':return [begin+x*MINUTE for x in (0,15,30,60,120)]
    if 'V8D' in pid:return [midnight(day)+18*3600*NS]
    return list(range(begin,begin+3*3600*NS+MINUTE,MINUTE))

def publication_slots(city, day):
    begin=anchor(city,day)
    return [begin+i*3600*NS for i in range(4)] if city=='la' else [begin]

def in_slot(profile, day, at_ns):
    if not day or day[:7]!='2026-10' or type(at_ns) is not int:return False
    nominal=at_ns//MINUTE*MINUTE
    if profile['city']=='ny' and profile['profile_id']=='SAFE5_TARGET_ONLY' and at_ns>anchor('ny',day)+3*3600*NS:return False
    if profile['city']=='la' and 'V8D' not in profile['profile_id'] and at_ns>anchor('la',day)+3*3600*NS:return False
    return nominal in entry_slots(profile,day) and nominal<=at_ns<nominal+NS

def slot_end(profile, day, nominal_ns):
    clipped=(profile['city']=='ny' and profile['profile_id']=='SAFE5_TARGET_ONLY' or
             profile['city']=='la' and 'V8D' not in profile['profile_id'])
    return nominal_ns+1 if clipped and nominal_ns==entry_slots(profile,day)[-1] else nominal_ns+NS

def window_metadata(profile, day, now_ns):
    if not day:return {'status':'WAITING_FOR_DATED_MARKET','window_open':False}
    if day[:7]!='2026-10':return {'status':'NEW_MONTH_CHECKPOINT_REQUIRED','window_open':False}
    slots=entry_slots(profile,day);nominal=now_ns//MINUTE*MINUTE
    current=in_slot(profile,day,now_ns);next_slot=next((s for s in slots if s>now_ns),None)
    inside=slots[0]<=now_ns<slot_end(profile,day,slots[-1])
    status='ENTRY_SLOT_OPEN' if current else 'WAITING_FOR_ENTRY_SLOT' if next_slot else 'ENTRY_WINDOW_FINISHED'
    iso=lambda n:datetime.fromtimestamp(n/NS,timezone.utc).isoformat() if n is not None else None
    return {'status':status,'window_open':current,'within_buying_window':inside,'first_buy_utc':iso(slots[0]),'last_buy_utc':iso(slots[-1]),
            'next_buy_utc':iso(next_slot),'next_buy_ns':next_slot,'native_slot_seconds':1,
            'nominal_slot_ns':nominal if current else None,'late_clocks_replayed':False}

def prepared_ready(result, city, nominal_ns):
    inner=result.get('forecast',result)
    if result.get('status') in ('BLOCKED','UNAVAILABLE','UNKNOWN') or result.get('blockers') or inner.get('blockers'):
        return False,'PREPARED_FORECAST_HAS_BLOCKERS'
    if result.get('prepared') is not True and inner.get('prepared') is not True:
        return False,'FORECAST_WAS_NOT_PREPARED_FOR_PUBLICATION'
    ready=result.get('prepared_ready_ns',inner.get('prepared_ready_ns'))
    if type(ready) is not int or not 0<=ready<nominal_ns:return False,'FORECAST_NOT_READY_BEFORE_NATIVE_SLOT'
    if not inner.get('probabilities_yes'):return False,'PREPARED_PROBABILITIES_UNAVAILABLE'
    if city=='phoenix':
        available=inner.get('available_ns')
        if type(available) is not int or available>=nominal_ns:return False,'PHOENIX_STATIC_FORECAST_COMPLETED_AFTER_CUTOFF'
        if inner.get('actual_generation_ns')!=ready or available<ready:return False,'PHOENIX_PREPARATION_CLOCKS_INCONSISTENT'
    if city=='miami' and inner.get('weather_trade_eligible') is not True:return False,'WEATHER_RECEIVED_AFTER_NATIVE_CUTOFF'
    return True,None
