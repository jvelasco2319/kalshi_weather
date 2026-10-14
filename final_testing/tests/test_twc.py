from _current_application import APPLICATION
"""Synthetic schema/timing tests. These do not prove future source correctness."""
from pathlib import Path
import sys
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from tempdesk.twc import normalize, climate_window
from tempdesk.impossible_exit import assess, rules_hash
from tempdesk.public_data import PublicData
from unittest.mock import patch

DAY='2026-10-12'; END=int(datetime(2026,10,13,8,tzinfo=timezone.utc).timestamp()*1e9)
RECEIPT={'received_ns':END+10**9,'response_sha256':'a'*64,
         'url':'https://weather.com/kalshi/api/climate/primary?date='+DAY}
HOURLY={'stations':[{'icaoId':'KLAX','observations':[
    {'icaoId':'KLAX','reportTimeUTC':'2026-10-12T18:00:00Z','tempF':69,'status':'settled'},
    {'icaoId':'KLAX','reportTimeUTC':'2026-10-12T07:30:00Z','tempF':80,'status':'settled'}]}]}
DAILY={'date':DAY,'results':[{'station':{'icao':'KLAX','cliId':'LAX'},'status':'official',
       'data':{'reportDate':DAY,'stationId':'LAX','maxTemp':69,'isOfficial':True}}]}
CONTRACT={'ticker':'test','lower':68,'upper':70,'raw':{'status':'active','rules_primary':'TWC CLILAX','rules_secondary':''}}
MARKET={'contracts':[CONTRACT],'day':DAY,'event_ticker':'event','settlement_target':{'provider':'THE_WEATHER_COMPANY'}}

def output(daily=DAILY,receipt=RECEIPT):
    return normalize('KLAX',DAY,[HOURLY],daily,[receipt],receipt,MARKET)

class TwcTests(unittest.TestCase):
    def test_actual_published_fahrenheit_and_climate_standard_time(self):
        r=output();self.assertEqual([x['temperature_f'] for x in r['values']],[69])
        self.assertEqual(r['provider'],'THE_WEATHER_COMPANY')
        self.assertEqual(r['climate_start_utc'],'2026-10-12T08:00:00+00:00')
        self.assertEqual(climate_window('KNYC',DAY)[0].hour,5)

    def test_official_report_is_revisable_and_not_an_irreversible_observation(self):
        r=output();p=r['settlement_high_floor']
        self.assertEqual(p['schema'],'SETTLEMENT_DAILY_REPORT_v1')
        self.assertTrue(p['material_revision_possible']);self.assertNotIn('irreversible',p)
        self.assertEqual(p['integer_lower_bound_f'],69)

    def test_preliminary_and_settled_hourly_do_not_generate_daily_proof(self):
        for status in ('preliminary','no_report','settled'):
            d=deepcopy(DAILY);d['results'][0]['status']=status
            self.assertIsNone(output(d)['settlement_high_floor'])

    def test_official_today_before_climate_day_end_is_rejected(self):
        self.assertIsNone(output(receipt={**RECEIPT,'received_ns':END-1})['settlement_high_floor'])

    def test_no_rounded_guess_or_station_date_fallback(self):
        for field,value in [('maxTemp',68.9),('maxTemp',True),('stationId','SAN'),('reportDate','2026-10-11'),('isOfficial',False)]:
            d=deepcopy(DAILY);d['results'][0]['data'][field]=value
            self.assertIsNone(output(d)['settlement_high_floor'])
        d=deepcopy(DAILY);d['date']='2026-10-11'
        with self.assertRaises(ValueError):output(d)
        d=deepcopy(DAILY);d['results'].append(deepcopy(d['results'][0]))
        with self.assertRaises(ValueError):output(d)

    def test_final_completed_daily_report_can_invalidate_no_inside_range(self):
        p=output()['settlement_high_floor'];now=RECEIPT['received_ns']
        position={'ticker':'test','side':'NO','quantity':2,'day':DAY,'first_fill_ns':END-10000000000}
        self.assertTrue(assess(position,CONTRACT,p,MARKET,now)['definitely_impossible'])
        self.assertFalse(assess({**position,'side':'YES'},CONTRACT,p,MARKET,now)['definitely_impossible'])
        self.assertFalse(assess(position,{**CONTRACT,'raw':{**CONTRACT['raw'],'status':'closed'}},p,MARKET,now)['definitely_impossible'])

    def test_twc_error_never_falls_back_to_nws(self):
        provider=PublicData.__new__(PublicData)
        with patch('tempdesk.twc.collect',side_effect=ConnectionError('missing')),patch.object(provider,'observations') as nws:
            with self.assertRaises(ConnectionError):provider.settlement_observations('KLAX',DAY,MARKET)
            nws.assert_not_called()

    def test_report_station_must_also_match_each_actual_market_rule(self):
        market=deepcopy(MARKET);market['contracts'][0]['raw']['rules_primary']='TWC CLISAN'
        r=normalize('KLAX',DAY,[HOURLY],DAILY,[RECEIPT],RECEIPT,market)
        self.assertIsNone(r['settlement_high_floor'])

if __name__=='__main__':unittest.main()
