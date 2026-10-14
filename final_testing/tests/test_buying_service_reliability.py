"""Candidate Service controls, synthetic books/money, actual durable Ledger."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json, sys, tempfile, threading, unittest, importlib, types

ROOT=Path(__file__).resolve().parents[2]
CONFIG=json.loads((ROOT/'final_testing/deployment.json').read_bytes())
APPLICATION=ROOT/CONFIG['application_online'];PACKAGE='_buying_service_application_under_test'
module=types.ModuleType(PACKAGE);module.__path__=[str(APPLICATION/'tempdesk')];module.__package__=PACKAGE;sys.modules[PACKAGE]=module
Service=importlib.import_module(PACKAGE+'.service').Service
Ledger=importlib.import_module(PACKAGE+'.ledger').Ledger
clocks=importlib.import_module(PACKAGE+'.execution_clock')
entry_slots,in_slot,prepared_ready,NS=clocks.entry_slots,clocks.in_slot,clocks.prepared_ready,clocks.NS

PROFILE={'city':'miami','profile_id':'SELECTED_CHAMPION','wallet_id':'miami:SELECTED_CHAMPION','daily_cap':'.10'}
DAY='2026-10-13';DUE=1791892805*NS

class ServiceControls(unittest.TestCase):
    def setUp(self):
        base=ROOT/'final_testing/runtime/service-control-runtime';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='isolated-',dir=base);root=Path(self.tmp.name)
        self.service=Service.__new__(Service);s=self.service
        s.runtime=root;s.profiles=[PROFILE];s.ledger=Ledger(root/'synthetic-paper.sqlite3',[PROFILE])
        s.lock=threading.RLock();s.stop=threading.Event();s.feed={'miami':{'day':DAY}};s.operational=False
        s.policy_locks={PROFILE['wallet_id']:s.ledger.wallet_transaction(PROFILE['wallet_id'])}
        s.native_inputs=lambda *a,**k:{}
        s.provider=SimpleNamespace(book=lambda ticker:self.quote(ticker))
        s.call=self.native_control
        self.called=[]

    def tearDown(self):
        self.service.ledger.db.close();self.tmp.cleanup()

    @staticmethod
    def quote(ticker):
        return {'ticker':ticker,'received_ns':DUE-NS,'status':'AVAILABLE','market_id':'synthetic-market',
                'source_age_clock':'RECEIPT_CLOCK','yes_bids':[['.5','10']], 'no_bids':[['.4','10']]}

    def native_control(self,city,request):
        oid=request['frozen_order']['order_id'];w=self.service.ledger.snapshot()['wallets'][PROFILE['wallet_id']]
        self.assertEqual(w['orders'][oid]['lifecycle']['phase'],'ATTEMPT_STARTED')
        self.called.append(oid)
        n=request['policy_state'].get('count',0)+1
        return {'status':'FILLED','quantity':1,'principal':'.5','fee':'.0175','fills':[[1,'.5']],
                'policy_state':{'count':n},'worker_started_ns':DUE}

    def orders(self,count=1):
        rows=[{'ticker':'synthetic-contract','side':'NO','quantity':1,'limit_price':'.5',
               'reserved_outlay':'.5175','arrival_delay_seconds':5,'fee_rate':'.07',
               'fee_quantum':'.0001','fee_rounding':'TOTAL_OUTLAY_CEILING'} for _ in range(count)]
        return self.service.ledger.reserve(PROFILE['wallet_id'],DAY,{'orders':rows,'policy_state':{'count':0}},'.10',DUE-5*NS,
            arrival_context={'feed':{'day':DAY},'forecast':{}})

    def test_two_simultaneous_arrivals_keep_both_state_updates(self):
        orders=self.orders(2)
        with patch('_buying_service_application_under_test.service.time.time_ns',return_value=DUE):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda o:self.service.arrive(PROFILE,{'day':DAY},{},o),orders))
        w=self.service.ledger.snapshot()['wallets'][PROFILE['wallet_id']]
        self.assertEqual(w['policy_state']['count'],2);self.assertEqual(w['cash'],'98.9650')
        self.assertEqual(w['positions']['synthetic-contract|NO']['quantity'],2)
        self.assertTrue(all(o['status']=='FILLED' and o['lifecycle']['phase']=='COMMITTED' for o in w['orders'].values()))
        self.assertEqual(len(self.called),2)

    def test_book_failure_before_attempt_cancels_without_fabricated_fill(self):
        order=self.orders()[0]
        def fail(ticker):raise OSError('labelled synthetic capture failure')
        self.service.provider.book=fail
        with patch('_buying_service_application_under_test.service.time.time_ns',return_value=DUE):self.service.arrive(PROFILE,{'day':DAY},{},order)
        w=self.service.ledger.snapshot()['wallets'][PROFILE['wallet_id']]
        self.assertEqual(w['orders'][order['order_id']]['status'],'CANCELLED')
        self.assertEqual(self.service.ledger.free(w),Decimal('100'));self.assertEqual(self.called,[])

    def test_failure_after_durable_attempt_retains_unknown_reservation(self):
        order=self.orders()[0]
        def fail(city,request):raise TimeoutError('labelled synthetic native timeout')
        self.service.call=fail
        with patch('_buying_service_application_under_test.service.time.time_ns',return_value=DUE):self.service.arrive(PROFILE,{'day':DAY},{},order)
        w=self.service.ledger.snapshot()['wallets'][PROFILE['wallet_id']]
        self.assertEqual(w['orders'][order['order_id']]['status'],'UNKNOWN')
        self.assertEqual(w['cash'],'100');self.assertEqual(self.service.ledger.free(w),Decimal('99.4825'))
        self.assertEqual(w['positions'],{})

    def test_restart_commits_recorded_result_once_without_native_redispatch(self):
        s=self.service;order=self.orders()[0];oid=order['order_id'];wid=PROFILE['wallet_id']
        s.ledger.prepare_arrival(wid,oid,DUE-NS);version=s.ledger.begin_attempt(wid,oid,DUE)
        result={'status':'FILLED','quantity':1,'principal':'.5','fee':'.0175','fills':[[1,'.5']],'policy_state':{'count':1}}
        s.ledger.record_arrival_result(wid,oid,result,self.quote(order['ticker']),DUE,expected_version=version)
        with patch('_buying_service_application_under_test.service.time.time_ns',return_value=DUE+10*NS):
            s.recover_pending_orders();s.recover_pending_orders()
        w=s.ledger.snapshot()['wallets'][wid]
        self.assertEqual(w['cash'],'99.4825');self.assertEqual(w['positions']['synthetic-contract|NO']['quantity'],1)
        self.assertEqual(self.called,[])

    def test_missed_fixed_slot_is_not_reopened(self):
        p=PROFILE;anchor=entry_slots(p,DAY)[0]
        self.assertTrue(in_slot(p,DAY,anchor+NS//2));self.assertFalse(in_slot(p,DAY,anchor+NS))
        self.assertFalse(in_slot(p,DAY,anchor+60*NS));self.assertFalse(in_slot(p,'2026-11-01',anchor))

    def test_phoenix_requires_true_preanchor_finished_calculation(self):
        anchor=entry_slots({'city':'phoenix','profile_id':'03-V10-Ensemble-Quarter-Kelly'},DAY)[0]
        result={'prepared':True,'forecast':{'probabilities_yes':{'synthetic':'.5'},
                 'prepared_ready_ns':anchor-1,'actual_generation_ns':anchor-1,'available_ns':anchor-1}}
        self.assertTrue(prepared_ready(result,'phoenix',anchor)[0])
        result['forecast']['prepared_ready_ns']=anchor
        self.assertFalse(prepared_ready(result,'phoenix',anchor)[0])

    def test_la_arrival_captures_book_at_or_after_native_due_time(self):
        s=self.service;s.ledger.db.close()
        p={'city':'la','profile_id':'F3_V10_PHYSICAL_FORECAST_EXIT','wallet_id':'la:F3_V10_PHYSICAL_FORECAST_EXIT','daily_cap':'.10'}
        s.profiles=[p];s.ledger=Ledger(s.runtime/'synthetic-la-paper.sqlite3',[p]);s.feed={'la':{'day':DAY}}
        clock=[DUE-4*NS];captured=[]
        class SimulatedWait:
            def wait(self,seconds):clock[0]+=round(seconds*NS);return False
            def is_set(self):return False
        s.stop=SimulatedWait()
        def capture(ticker):
            captured.append(clock[0]);q=self.quote(ticker);q['received_ns']=clock[0];return q
        s.provider.book=capture
        s.call=lambda city,request:{'status':'CANCELLED','quantity':0,'principal':'0','fee':'0','policy_state':{}}
        order=s.ledger.reserve(p['wallet_id'],DAY,{'orders':[{'ticker':'synthetic-contract','side':'NO',
          'quantity':1,'limit_price':'.5','reserved_outlay':'.52','arrival_delay_seconds':5,
          'fee_rate':'.07','fee_quantum':'.01','fee_rounding':'RETAIL_CENT'}]},'.10',DUE-5*NS)[0]
        with patch('_buying_service_application_under_test.service.time.time_ns',side_effect=lambda:clock[0]):
            s.arrive(p,{'day':DAY},{},order)
        self.assertTrue(captured);self.assertGreaterEqual(captured[0],DUE)
        self.assertEqual(s.ledger.snapshot()['wallets'][p['wallet_id']]['orders'][order['order_id']]['status'],'CANCELLED')

    def test_fatal_scheduler_error_is_exposed_not_silently_ignored(self):
        def fail():raise RuntimeError('labelled synthetic scheduler failure')
        self.service._run_loop=fail;self.service.run()
        self.assertEqual(self.service.scheduler_status,'BLOCKED')
        self.assertIn('synthetic scheduler failure',self.service.scheduler_error)
        self.assertEqual(self.service.feed['miami']['execution_events'][-1]['phase'],'SCHEDULER')

if __name__=='__main__':unittest.main(verbosity=2)
