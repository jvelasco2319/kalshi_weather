from _current_application import APPLICATION
"""Prospective publication, retained definition and source admission boundaries."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import hashlib,json,sys,tempfile,threading,unittest
ROOT=Path(__file__).resolve().parents[1]
(ROOT/'runtime').mkdir(exist_ok=True)
from tempdesk.service import Service
from tempdesk.policy_receipts import verify,parse_cli,SCHEMA
from tempdesk.impossible_exit import rules_hash

class Queue:
 def __init__(self):self.jobs=[]
 def submit(self,fn,*args):self.jobs.append((fn,args))

class Repairs(unittest.TestCase):
 def test_cached_hashes_reused_only_while_all_file_metadata_is_unchanged(self):
  from tempdesk.integrity_cache import EvidenceCache
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as directory:
   root=Path(directory);(root/'manifest.json').write_text('{}');file=root/'code.py';file.write_text('original')
   calls=[]
   def checked():
    calls.append(1)
    if file.read_text()!='original':raise ValueError('Changed code')
    return {'status':'VERIFIED'}
   release=SimpleNamespace(root=root,manifest={'files':[{'path':'code.py'}]},verify=checked)
   cache=EvidenceCache();cache.check(release);cache.check(release);self.assertEqual(len(calls),1)
   file.write_text('altered code')
   with self.assertRaisesRegex(ValueError,'Changed'):cache.check(release)
   self.assertEqual(len(calls),2)
 def bare(self,city='ny'):
  s=object.__new__(Service);s.operational=True;s.lock=threading.RLock();s.clock_pool=Queue();s.clock_seen=set();s.feed={};s.save_feed=lambda:None
  s.profiles=[{'city':city,'profile_id':'CONSERVATIVE_P09' if city=='ny' else 'SELECTED_CHAMPION','wallet_id':'test'}]
  s.native_inputs=lambda *a,**k:{}
  return s
 def test_prepared_publication_keeps_creation_clock_and_uses_actual_publication(self):
  s=self.bare();at=1791890400000000000
  prepared={'prepared':True,'prepared_ready_ns':at-1000000000,'forecast':{'prepared_ready_ns':at-1000000000,'probabilities_yes':{'unit':.3},'trade_eligible':False,'asof_ns':at,'generated_ns':at-120000000000}}
  f={'day':'2026-10-13','prepared_forecasts':{str(at):{'CONSERVATIVE_P09':prepared}}};s.feed['ny']=f
  s.call=lambda *a,**k:self.fail('Prepared publication should not start a cold inference')
  with patch('tempdesk.service.time.time_ns',return_value=at+100000000):s.publish_city('ny',f,at)
  result=s.feed['ny']['forecasts']['CONSERVATIVE_P09']
  self.assertTrue(result['native_publication_admitted']);self.assertEqual(result['forecast']['generated_ns'],at-120000000000)
  self.assertEqual(result['forecast']['available_ns'],at-1000000000);self.assertEqual(len(s.clock_pool.jobs),1)
  self.assertFalse(prepared['forecast']['trade_eligible'])
 def test_late_publication_never_dispatches_entry(self):
  s=self.bare();at=1791890400000000000
  f=dict(day='2026-10-13',prepared_forecasts={str(at):{'CONSERVATIVE_P09':dict(prepared=True,forecast=dict(probabilities_yes={'unit':.3}))}});s.feed['ny']=f
  with patch('tempdesk.service.time.time_ns',return_value=at+2000000000):s.publish_city('ny',f,at)
  self.assertFalse(s.clock_pool.jobs);self.assertIsNot(s.feed['ny']['forecasts']['CONSERVATIVE_P09'].get('forecast',{}).get('trade_eligible'),True)
 def test_miami_late_weather_not_promoted_by_preparation(self):
  s=self.bare('miami');at=1791890400000000000
  f={'day':'2026-10-13','prepared_forecasts':{str(at):{'SELECTED_CHAMPION':{'prepared':True,'probabilities_yes':{'unit':.3},'weather_trade_eligible':False}}}};s.feed['miami']=f
  with patch('tempdesk.service.time.time_ns',return_value=at+100000000):s.publish_city('miami',f,at)
  self.assertFalse(s.clock_pool.jobs)
 def test_retained_definition_receipt_remains_original_and_changed_rules_block(self):
  s=self.bare('phoenix');contracts=[{'ticker':'unit','lower':None,'upper':None,'raw':{'rules_primary':'unit immutable rules','status':'active'}}]
  old={'contracts':deepcopy(contracts),'contracts_received_ns':11,'receipt':{'received_ns':11},'rules_sha256':rules_hash(contracts)}
  f={'definitions':old,'market':{'contracts':contracts,'contracts_received_ns':99,'receipt':{'received_ns':99}}}
  self.assertEqual(s.definition_market('phoenix',f)['contracts_received_ns'],11)
  f['market']['contracts'][0]['raw']['rules_primary']='changed rules'
  with self.assertRaisesRegex(ValueError,'changed'):s.definition_market('phoenix',f)
 def test_la_17_publication_skips_v8d_without_removing_its_settlement(self):
  s=self.bare('la');s.profiles=[{'city':'la','profile_id':'V8D_NO18_SETTLEMENT','wallet_id':'unit'}]
  at=int(datetime(2026,10,13,17,tzinfo=timezone.utc).timestamp()*1e9);s.feed['la']={'day':'2026-10-13'}
  s.call=lambda *a,**k:self.fail('V8D is 18UTC only')
  with patch('tempdesk.service.time.time_ns',return_value=at+100000000):s.publish_city('la',s.feed['la'],at)
  from types import SimpleNamespace
  s.ledger=SimpleNamespace(wallet_transaction=lambda wid:threading.RLock())
  s.settle_known('la',{'contracts':[]})
  self.assertFalse(s.clock_pool.jobs)
 def test_mapping_scope_hash_and_clock_fail_closed(self):
  contracts=[{'ticker':'unit','raw':{'rules_primary':'unit'}}];at=1791890400000000000
  p={'schema':SCHEMA,'city':'ny','date':'2026-10-13','station':'KNYC','series':'KXHIGHNY','admitted_for_paper':True,'received_ns':at,'valid_through_ns':at+10,'rules_sha256':rules_hash(contracts),'pairs':[{}]*3}
  with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as directory:
   path=Path(directory)/'fixture.json';raw=json.dumps(p).encode();path.write_bytes(raw);binding={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
   verify(binding,'ny','2026-10-13',contracts,at,verify_sources=False)
   for city,day,clock in [('miami','2026-10-13',at),('ny','2026-10-14',at),('ny','2026-10-13',at-1),('ny','2026-10-13',at+11)]:
    with self.assertRaises(ValueError):verify(binding,city,day,contracts,clock,verify_sources=False)
   path.write_bytes(raw+b' ')
   with self.assertRaisesRegex(ValueError,'changed'):verify(binding,'ny','2026-10-13',contracts,at,verify_sources=False)
 def test_wrong_station_and_incomplete_nws_report_rejected(self):
  b={'productCode':'CLI','productText':'CLINYC\nNATIONAL WEATHER SERVICE\nTEMPERATURE (F)\nCLIMATE SUMMARY FOR OCTOBER 12 2026\n MAXIMUM 70 \n','issuanceTime':'2026-10-13T07:00:00Z'}
  self.assertEqual(parse_cli(b,'ny')['high_f'],70)
  with self.assertRaises(ValueError):parse_cli(b,'la')
  b['issuanceTime']='2026-10-12T20:00:00Z'
  with self.assertRaisesRegex(ValueError,'incomplete'):parse_cli(b,'ny')
 def test_phoenix_prepared_or_late_monitor_is_not_a_published_trade_signal(self):
  from tempdesk.adapters import phoenix as phx
  day='2026-10-13';anchor=phx.ns(day);pid=phx.PROFILES[0]
  packet={k:{'available_ns':anchor-120*phx.NS} for k in ('frame','observation','atmosphere','hrrr14')};packet['receipts']=[]
  request=dict(day=day,profile_id=pid,contracts=[],contracts_received_ns=anchor-120*phx.NS,weather=packet,paper_mapping_mode='VERIFIED_DAILY_CLI_PROXY_v1')
  with patch.object(phx,'profile',return_value={}),patch.object(phx,'contracts_native',return_value=[]),patch.object(phx,'settlement_binding',return_value={}),patch.object(phx,'_weather_validate'),patch.object(phx,'_infer',side_effect=lambda *a:{p:{'probabilities_yes':{'unit':.3}} for p in phx.PROFILES}):
   with patch.object(phx.time,'time_ns',return_value=anchor-60*phx.NS):prepared=phx.forecast({**request,'at_ns':anchor-60*phx.NS,'purpose':'prepare_publication'})
   self.assertTrue(prepared['prepared']);self.assertFalse(prepared['trade_eligible'])
   with patch.object(phx.time,'time_ns',return_value=anchor+100000000):published=phx.forecast({**request,'at_ns':anchor+100000000})
   self.assertFalse(published['trade_eligible'])
   with patch.object(phx.time,'time_ns',return_value=anchor+60*phx.NS):late=phx.forecast({**request,'at_ns':anchor+60*phx.NS})
   self.assertFalse(late['trade_eligible']);self.assertEqual(late['forecast']['available_ns'],anchor+60*phx.NS)
 def test_phoenix_generated_data_can_be_read_without_frozen_source_fallback(self):
  from tempdesk.adapters import phoenix as phx
  with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as directory:
   root=Path(directory);data=root/'derived.json';data.write_text('{"actual_generated":true}')
   mapping=root/'source-map.json';mapping.write_text('{"mapping":{}}')
   with patch.dict(__import__('os').environ,{'TEMP_DESK_RUNTIME_ROOT':str(root),'TEMP_DESK_SOURCE_MAP':str(mapping)}):
    self.assertEqual(phx.collected_json(data),{'actual_generated':True})
    with self.assertRaises(phx.Blocked):phx.collected_json(root.parent/'unregistered-source.json')
