"""Read the public weather.com/kalshi portal; hourly is not daily settlement.

No API key, trading client, interpolation, NWS fallback, or forecast fitting.
Official reports can still be revised under Kalshi's published rules.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode
import math
from .impossible_exit import rules_hash

BASE = 'https://weather.com/kalshi'
STATIONS = {'KLAX': ('LAX', -8), 'KNYC': ('NYC', -5),
            'KMIA': ('MIA', -5), 'KPHX': ('PHX', -7)}


def climate_window(station, day):
    _, offset = STATIONS[station]
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(),
                             timezone(timedelta(hours=offset)))
    return start.astimezone(timezone.utc), (start+timedelta(days=1)).astimezone(timezone.utc)


def normalize(station, day, hourly, daily, hourly_receipts, daily_receipt, market=None):
    """Reject ambiguous station/day/schema; use published Fahrenheit unchanged."""
    cli, _ = STATIONS[station]
    start, end = climate_window(station, day)
    values = {}
    for body, receipt in zip(hourly, hourly_receipts, strict=True):
        rows = [r for r in body.get('stations', []) if r.get('icaoId') == station]
        if len(rows) != 1:
            raise ValueError('TWC hourly station missing or duplicated')
        for r in rows[0].get('observations', []):
            if r.get('icaoId') != station:
                raise ValueError('TWC hourly station mismatch')
            stamp = r.get('reportTimeUTC')
            if not stamp: continue
            at = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if at.tzinfo is None: raise ValueError('TWC observation clock has no zone')
            if not start <= at < end or int(at.timestamp()*1e9) > receipt['received_ns']: continue
            temperature = r.get('tempF')
            if type(temperature) not in (int, float) or not math.isfinite(temperature) or not -100 <= temperature <= 150:
                continue
            if stamp in values and values[stamp]['temperature_f'] != temperature:
                raise ValueError('Conflicting TWC observations')
            values[stamp] = dict(timestamp=stamp, temperature_f=temperature,
                                 publication_status=r.get('status'), daily_settlement_final=False)
    rows = [r for r in daily.get('results', []) if r.get('station', {}).get('icao') == station]
    if daily.get('date') != day or len(rows) != 1:
        raise ValueError('TWC daily report date/station missing or duplicated')
    row = rows[0]; data = row.get('data') or {}
    if row['station'].get('cliId') != cli: raise ValueError('TWC climate station mismatch')
    high = data.get('maxTemp')
    official = row.get('status') in ('official', 'revised') and data.get('isOfficial') is True
    official = official and data.get('stationId') == cli and data.get('reportDate') == day
    official = official and type(high) in (int, float) and math.isfinite(high) and Decimal(str(high)) == Decimal(str(high)).to_integral_value()
    official = bool(official and daily_receipt['received_ns'] >= int(end.timestamp()*1e9))
    report = dict(status=row.get('status', 'UNAVAILABLE'), high_f=high,
                  station=station, climate_day=day, official_nonpreliminary=official,
                  received_ns=daily_receipt['received_ns'], source_sha256=daily_receipt['response_sha256'],
                  source_url=daily_receipt['url'], material_revision_possible=True)
    proof = None
    contracts = (market or {}).get('contracts', [])
    rules_match = bool(contracts) and all(
        any(token in c.get('raw', {}).get('rules_primary', '').upper()
            for token in ('CLI'+cli, station)) for c in contracts)
    if official and rules_match and market and market.get('day') == day and market.get('settlement_target', {}).get('provider') == 'THE_WEATHER_COMPANY':
        # The final published report is authoritative now; never call it irrevocable.
        proof = dict(schema='SETTLEMENT_DAILY_REPORT_v1', validated_by_source_producer=True,
                     official_nonpreliminary=True, material_revision_possible=True,
                     metric='FINAL_SETTLEMENT_ROUNDED_DAILY_HIGH_F', provider='THE_WEATHER_COMPANY',
                     station=station, day=day, event_ticker=market['event_ticker'],
                     rules_sha256=rules_hash(market['contracts']), measured_ns=int(end.timestamp()*1e9),
                     received_ns=daily_receipt['received_ns'], source_sha256=daily_receipt['response_sha256'],
                     source_url=daily_receipt['url'], integer_lower_bound_f=int(high))
    return dict(station=station, values=sorted(values.values(), key=lambda r:r['timestamp']),
                status='AVAILABLE' if values else 'OBSERVATIONS_UNAVAILABLE',
                provider='THE_WEATHER_COMPANY', observation_product='HOURLY_METAR_DISPLAY_ONLY',
                official_settlement_high=official, daily_report=report, settlement_high_floor=proof,
                receipt=hourly_receipts[-1], daily_receipt=daily_receipt,
                climate_start_utc=start.isoformat(), climate_end_utc=end.isoformat())


def collect(provider, station, day, market=None):
    start, end = climate_window(station, day)
    # Local standard climate days can cross a portal calendar/week boundary.
    weeks = sorted({(d-timedelta(days=d.weekday())).isoformat()
                    for d in (date.fromisoformat(day), date.fromisoformat(day)+timedelta(days=1))})
    hourly, receipts = [], []
    for week in weeks:
        body, receipt = provider.cached_get(BASE+'/api/metar?'+urlencode(dict(primary='true', weekStart=week)))
        hourly.append(body); receipts.append(receipt)
    daily, daily_receipt = provider.cached_get(BASE+'/api/climate/primary?'+urlencode(dict(date=day)))
    return normalize(station, day, hourly, daily, receipts, daily_receipt, market)
