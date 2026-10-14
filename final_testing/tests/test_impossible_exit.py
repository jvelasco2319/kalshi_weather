from _current_application import APPLICATION
"""Meaningful hard-invalidation and conditional liquidation accounting checks.

All certificates and books here are explicitly synthetic test fixtures. They
do not establish compatibility of the currently connected NWS/TWC sources.
"""
from pathlib import Path
import sys
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import importlib.util,sys,tempfile,threading,unittest
from unittest.mock import patch
from tempdesk.impossible_exit import assess, sell_quantity, rules_hash, market_monitor
from tempdesk.ledger import Ledger
from tempdesk.service import Service

NOW=1791835200000000000
DAY='2026-10-12';TICKER='KXHIGHLAX-26OCT12-B64.5'
CONTRACT={'ticker':TICKER,'lower':64,'upper':66,'raw':{'status':'active','rules_primary':'Synthetic fixture: TWC KLAX daily high','rules_secondary':''}}
MARKET={'contracts':[CONTRACT],'day':DAY,'event_ticker':'KXHIGHLAX-26OCT12','settlement_target':{'provider':'THE_WEATHER_COMPANY'}}
POSITION={'ticker':TICKER,'side':'YES','quantity':5,'day':DAY,'basis':'.32','first_fill_ns':NOW-100000000000}

def certificate(floor=66):
    return dict(schema='SETTLEMENT_HIGH_FLOOR_v1',validated_by_source_producer=True,irreversible=True,
        metric='FINAL_SETTLEMENT_ROUNDED_DAILY_HIGH_F',provider='THE_WEATHER_COMPANY',station='KLAX',day=DAY,
        event_ticker=MARKET['event_ticker'],rules_sha256=rules_hash(MARKET['contracts']),
        measured_ns=NOW-2000000000,received_ns=NOW-1000000000,source_sha256='a'*64,integer_lower_bound_f=floor)

def book(price='.02',size='4',at=NOW):
    return dict(ticker=TICKER,yes_bids=[[price,size]],no_bids=[['.90','100']],received_ns=at,source_age_clock='RECEIPT_CLOCK')

class ImpossibleRuleTests(unittest.TestCase):
    def test_temperature_checks_have_a_queue_separate_from_heavy_weather_models(self):
        class Queue:
            def __init__(self):self.jobs=[]
            def submit(self,*job):self.jobs.append(job)
        service=Service.__new__(Service);service.lock=threading.RLock()
        service.public_pool=Queue();service.pool=Queue();service.busy=set();service.last_public={}
        with patch('tempdesk.service.time.time',return_value=1000):service.refresh()
        self.assertEqual(len(service.public_pool.jobs),4);self.assertEqual(service.pool.jobs,[])
        self.assertEqual(service.last_public['la'],1000)
    def test_finite_yes_dies_only_after_inclusive_ceiling_is_passed(self):
        self.assertFalse(assess(POSITION,CONTRACT,certificate(65),MARKET,NOW)['definitely_impossible'])
        self.assertTrue(assess(POSITION,CONTRACT,certificate(66),MARKET,NOW)['definitely_impossible'])
    def test_no_in_finite_range_stays_held_even_when_high_is_inside(self):
        position={**POSITION,'side':'NO'}
        for floor in (63,64,65,66,80):
            self.assertFalse(assess(position,CONTRACT,certificate(floor),MARKET,NOW)['definitely_impossible'])
    def test_no_on_top_unbounded_range_can_be_dead(self):
        contract={**CONTRACT,'lower':70,'upper':None}
        market={**MARKET,'contracts':[contract]};proof=certificate(70);proof['rules_sha256']=rules_hash([contract])
        self.assertTrue(assess({**POSITION,'side':'NO'},contract,proof,market,NOW)['definitely_impossible'])
        self.assertFalse(assess(POSITION,contract,proof,market,NOW)['definitely_impossible'])
    def test_wrong_station_provider_date_precision_and_clocks_are_not_certainty(self):
        invalid=[('station','KSAN'),('provider','NATIONAL_WEATHER_SERVICE'),('day','2026-10-11'),
                 ('irreversible',False),('validated_by_source_producer',False),('integer_lower_bound_f',65.9),
                 ('received_ns',NOW+1),('measured_ns',NOW+1),('rules_sha256','b'*64),('source_sha256','bad')]
        for field,value in invalid:
            with self.subTest(field=field):
                proof=certificate();proof[field]=value
                self.assertFalse(assess(POSITION,CONTRACT,proof,MARKET,NOW)['definitely_impossible'])
        self.assertFalse(assess(POSITION,CONTRACT,None,MARKET,NOW)['definitely_impossible'])
    def test_closed_market_and_stale_certificate_stay_held(self):
        closed={**CONTRACT,'raw':{**CONTRACT['raw'],'status':'finalized'}}
        self.assertFalse(assess(POSITION,closed,certificate(),MARKET,NOW)['definitely_impossible'])
        self.assertFalse(assess(POSITION,CONTRACT,certificate(),MARKET,NOW+121000000000)['definitely_impossible'])
    def test_no_bid_and_stale_bid_do_not_manufacture_a_sale(self):
        quote=book();quote['yes_bids']=[]
        self.assertEqual(sell_quantity(POSITION,quote,NOW)[1],'NO_BUYERS')
        self.assertEqual(sell_quantity(POSITION,book(at=NOW-61000000000),NOW)[1],'BUYER_BOOK_STALE_OR_FUTURE')
    def test_partial_supported_whole_exit_and_half_depth(self):
        plan,_=sell_quantity(POSITION,book(size='4.99'),NOW,D('.5'))
        self.assertEqual(plan['quantity'],2);self.assertTrue(plan['partial_position_exit'])
    def test_nws_observations_do_not_become_a_twc_floor(self):
        state=market_monitor(MARKET,{'values':[{'temperature_f':90}], 'provider':'NATIONAL_WEATHER_SERVICE'},NOW)
        self.assertEqual(state['source_status'],'WAITING_FOR_VERIFIED_SETTLEMENT_TEMPERATURE')
        self.assertEqual(state['check_interval_seconds'],30)

class LiquidationAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        profiles=[{'wallet_id':name,'city':'la','profile_id':name} for name in ('a','b')]
        self.ledger=Ledger(Path(self.temp.name)/'paper.sqlite3',profiles)
        state=self.ledger.snapshot()
        for wallet in state['wallets'].values():
            wallet.update(cash='99.68',fees='.02')
            wallet['positions'][TICKER+'|YES']=deepcopy(POSITION)
        self.ledger.commit(state,'SYNTHETIC_TEST_POSITION',{})
    def tearDown(self):
        self.ledger.db.close();self.temp.cleanup()
    def reserve(self,wid='a',quantity=2):
        order={'action':'SELL','ticker':TICKER,'side':'YES','quantity':quantity,'limit_price':'.02',
               'reserved_outlay':'0','fee_rate':'.07','fee_quantum':'.01','fee_rounding':'RETAIL_CENT',
               'arrival_delay_seconds':5,'exit_policy':'LA-IMPOSSIBLE-ONLY-EXIT-v1'}
        return self.ledger.reserve(wid,DAY,{'orders':[order]},'.10',NOW)[0]
    def fill(self,oid,wid='a',size='4',at=NOW+5000000000):
        result={'status':'FILLED','quantity':2,'principal':'.04','fee':'.01','fills':[[2,'.02']]}
        self.ledger.reconcile(wid,oid,result,book(size=size,at=at),at)
    def test_partial_sell_credits_only_net_proceeds_preserves_basis_and_no_double_settlement(self):
        order=self.reserve();self.fill(order['order_id']);w=self.ledger.snapshot()['wallets']['a']
        self.assertEqual(D(w['cash']),D('99.71'));self.assertEqual(D(w['fees']),D('.03'))
        self.assertEqual(w['positions'][TICKER+'|YES']['quantity'],3)
        self.assertEqual(D(w['positions'][TICKER+'|YES']['basis']),D('.192'))
        self.assertEqual(D(w['realized']),D('-.098'))
        self.ledger.settle('a',{'ticker':TICKER,'status':'finalized','result':'no'},NOW+6000000000)
        w=self.ledger.snapshot()['wallets']['a']
        self.assertEqual(D(w['cash']),D('99.71'));self.assertEqual(D(w['realized']),D('-.290'))
        self.assertFalse(w['positions'])
    def test_pending_sell_reserves_inventory_and_repeat_cannot_sell_it_twice(self):
        self.reserve(quantity=4)
        with self.assertRaisesRegex(ValueError,'inventory'):self.reserve(quantity=2)
        self.assertEqual(D(self.ledger.free(self.ledger.snapshot()['wallets']['a'])),D('99.68'))
    def test_same_physical_bid_capacity_is_shared(self):
        a=self.reserve();b=self.reserve('b');self.fill(a['order_id'],size='3')
        with self.assertRaisesRegex(ValueError,'depth'):self.fill(b['order_id'],'b',size='3')
        self.assertEqual(D(self.ledger.snapshot()['wallets']['b']['cash']),D('99.68'))
    def test_early_or_unpriced_sale_does_not_credit_cash(self):
        order=self.reserve()
        with self.assertRaisesRegex(ValueError,'arrival'):self.fill(order['order_id'],at=NOW+4000000000)
        self.assertEqual(D(self.ledger.snapshot()['wallets']['a']['cash']),D('99.68'))
    def test_cancelled_sell_keeps_position_and_releases_inventory(self):
        order=self.reserve()
        self.ledger.reconcile('a',order['order_id'],{'status':'CANCELLED'}, {},NOW+5000000000)
        self.assertEqual(self.ledger.snapshot()['wallets']['a']['positions'][TICKER+'|YES']['quantity'],5)
        self.reserve(quantity=5)
    def test_legacy_sell_keeps_four_decimal_native_fee_and_cash(self):
        state=self.ledger.snapshot();w=state['wallets']['a']
        w['cash']='90.2403';w['positions'][TICKER+'|YES'].update(quantity=24,basis='9.7597')
        self.ledger.commit(state,'SYNTHETIC_LEGACY_POSITION',{})
        order={'action':'SELL','ticker':TICKER,'side':'YES','quantity':24,'limit_price':'.68',
               'reserved_outlay':'0','fee_rate':'.07','fee_quantum':'.0001','fee_rounding':'TOTAL_PROCEEDS_FLOOR',
               'arrival_delay_seconds':5,'exit_policy':'LA-IMPOSSIBLE-ONLY-EXIT-v1'}
        reserved=self.ledger.reserve('a',DAY,{'orders':[order]},'.10',NOW)[0]
        result={'status':'FILLED','quantity':24,'principal':'16.32','fee':'.3656','fills':[[24,'.68']]}
        self.ledger.reconcile('a',reserved['order_id'],result,book(price='.68',size='100',at=NOW+5000000000),NOW+5000000000)
        wallet=self.ledger.snapshot()['wallets']['a']
        self.assertEqual(D(wallet['cash']),D('106.1947'));self.assertEqual(D(wallet['realized']),D('6.1947'))
        self.assertFalse(wallet['positions'])
    def test_multilevel_retail_fee_matches_unchanged_native_fee_function(self):
        path=Path(__file__).resolve().parents[2]/'final_testing/assets/l/experiments/LA-F2-MAY-OCT9-20261010/trader/fixed_kernel/engine.py'
        spec=importlib.util.spec_from_file_location('hard_exit_test_native_fee',path)
        native=importlib.util.module_from_spec(spec);sys.modules[spec.name]=native;spec.loader.exec_module(native)
        fills=[(1,D('.03')),(1,D('.02'))];proceeds,fee=native.FeePolicy().sell_amount(fills)
        reserved=self.reserve();quote=book(at=NOW+5000000000);quote['yes_bids']=[['.03','1'],['.02','1']]
        result={'status':'FILLED','quantity':2,'principal':'.05','fee':str(fee),'fills':[[n,str(p)] for n,p in fills]}
        self.ledger.reconcile('a',reserved['order_id'],result,quote,NOW+5000000000)
        self.assertEqual(D(self.ledger.snapshot()['wallets']['a']['cash']),D('99.68')+proceeds)

if __name__=='__main__':unittest.main()
