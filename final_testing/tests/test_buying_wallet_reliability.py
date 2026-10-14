"""Portable, isolated buying/restart regressions; no source-PC dependency.

Load the selected application from deployment.json into a separate package name
so these tests exercise its real ledger without replacing any running module.
Financial fixtures are synthetic controls, never strategy return evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import importlib,json,sqlite3,sys,tempfile,types,unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from final_testing.tools.migrate_buying_reliability_wallets import migrate,verify_compatible,TARGET,POLICY
CONFIG=json.loads((ROOT/'final_testing/deployment.json').read_bytes())
APPLICATION=(ROOT/CONFIG['application_online']).resolve()
PACKAGE='_buying_wallet_application_under_test'
module=types.ModuleType(PACKAGE);module.__path__=[str(APPLICATION/'tempdesk')];module.__package__=PACKAGE
sys.modules[PACKAGE]=module
ledger_module=importlib.import_module(PACKAGE+'.ledger')
lifecycle_module=importlib.import_module(PACKAGE+'.wallet_lifecycle')
Ledger=ledger_module.Ledger;StaleWalletState=lifecycle_module.StaleWalletState
TEMP_ROOT=ROOT/'final_testing/runtime/buying-wallet-test-fixtures';TEMP_ROOT.mkdir(parents=True,exist_ok=True)
DAY='2026-10-13';DECISION=100_000_000_000;ARRIVAL=DECISION+5_000_000_000

def profile(wid='w',city='ny'):
    return {'wallet_id':wid,'city':city,'profile_id':'SYNTHETIC_NATIVE_CONTROL'}

def order(i,ticker=None):
    native={'order_id':'fixture-'+str(i),'ticker':ticker or 'TEST-'+str(i),'quantity':1,'limit':'.5',
            'reservation':'.5175','arrival_ns':ARRIVAL,'date':DAY,'status':'PENDING'}
    return {'ticker':native['ticker'],'side':'NO','quantity':1,'limit_price':'.5','reserved_outlay':'.5175',
            'fee_rate':'.07','fee_quantum':'.0001','fee_rounding':'TOTAL_OUTLAY_CEILING','native_order':native}

def native_state(orders):
    return {'profile_id':'SYNTHETIC_NATIVE_CONTROL','cash':'100','realized':'0',
            'orders':{o['native_order']['order_id']:deepcopy(o['native_order']) for o in orders},
            'positions':{},'daily_spend':{},'opening':{}}

def quote(ticker,size='10'):
    return {'ticker':ticker,'received_ns':ARRIVAL,'yes_bids':[['.5',size]],'no_bids':[['.49',size]]}

class BuyingWalletReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        self.path=Path(self.folder.name)/'paper.sqlite3';self.ledger=Ledger(self.path,[profile()])
    def tearDown(self):self.ledger.db.close();self.folder.cleanup()
    def reserve(self,count=1):
        planned=[order(i) for i in range(count)]
        return self.ledger.reserve('w',DAY,{'orders':planned,'policy_state':native_state(planned)},'.10',DECISION,
             expected_version=0,arrival_context={'feed':{'day':DAY},'forecast':{'synthetic_control':True}})
    def fill(self,o):
        with self.ledger.wallet_transaction('w'):
            version=self.ledger.begin_attempt('w',o['order_id'],ARRIVAL)
            with self.ledger.capacity_transaction():
                q=quote(o['ticker']);self.ledger.remaining_book(q)
                state=deepcopy(self.ledger.snapshot()['wallets']['w']['policy_state'])
                state['orders'][o['order_id']].update(status='PAPER_ASSUMED_FILL',amount='.5175',filled_quantity=1)
                state['cash']=str(D(state['cash'])-D('.5175'))
                state['daily_spend'][DAY]=str(D(state['daily_spend'].get(DAY,'0'))+D('.5175'))
                state['positions'][o['ticker']]={'ticker':o['ticker'],'quantity':1,'basis':'.5175'}
                result={'status':'FILLED','quantity':1,'principal':'.5','fee':'.0175',
                        'fills':[{'quantity':1,'price':'.5','fee':'.0175'}],'policy_state':state}
                self.ledger.record_arrival_result('w',o['order_id'],result,q,ARRIVAL,expected_version=version)
                self.ledger.reconcile('w',o['order_id'],result,q,ARRIVAL,expected_version=version)

    def test_two_concurrent_arrivals_preserve_cash_native_positions_and_fees(self):
        orders=self.reserve(2)
        with ThreadPoolExecutor(2) as pool:list(pool.map(self.fill,orders))
        w=self.ledger.snapshot()['wallets']['w'];lifecycle_module.assert_native_parity(w)
        self.assertEqual(D(w['cash']),D('98.9650'));self.assertEqual(D(w['fees']),D('.0350'))
        self.assertEqual(len(w['positions']),2);self.assertEqual(len(w['policy_state']['positions']),2)
        self.assertEqual(len([o for o in w['orders'].values() if o['status']=='FILLED']),2)

    def test_three_wallets_share_only_two_visible_contracts(self):
        self.ledger.db.close();self.ledger=Ledger(Path(self.folder.name)/'capacity.sqlite3',[profile(str(i),'miami') for i in range(3)])
        orders={str(i):self.ledger.reserve(str(i),DAY,{'orders':[order(i,'SHARED')]},'.10',DECISION)[0] for i in range(3)}
        def fill(wid):
            with self.ledger.wallet_transaction(wid),self.ledger.capacity_transaction():
                q=quote('SHARED','2');remaining=self.ledger.remaining_book(q)
                result={'status':'FILLED','quantity':1,'principal':'.5','fee':'.0175','fills':[{'quantity':1,'price':'.5','fee':'.0175'}]} if remaining['yes_bids'] else {'status':'CANCELLED'}
                self.ledger.reconcile(wid,orders[wid]['order_id'],result,q,ARRIVAL)
        with ThreadPoolExecutor(3) as pool:list(pool.map(fill,orders))
        state=self.ledger.snapshot()
        self.assertEqual(sum(o['status']=='FILLED' for w in state['wallets'].values() for o in w['orders'].values()),2)
        self.assertEqual(D(state['capacity']['SHARED|yes|0.5']['consumed']),2)

    def test_stale_worker_replacement_cannot_overwrite_new_cash(self):
        orders=self.reserve(2);self.fill(orders[0]);before=self.ledger.snapshot()
        with self.assertRaises(StaleWalletState):
            self.ledger.reconcile('w',orders[1]['order_id'],{'status':'UNKNOWN'},quote(orders[1]['ticker']),ARRIVAL,expected_version=1)
        self.assertEqual(before,self.ledger.snapshot())

    def test_future_pending_can_resume_without_backdating(self):
        self.reserve();plan=self.ledger.recover_orders(ARRIVAL-1)
        self.assertEqual(plan[0]['action'],'RESUME_FUTURE_UNATTEMPTED')
        self.assertEqual(plan[0]['actual_recovery_ns'],ARRIVAL-1)

    def test_expired_known_unattempted_cancels_native_and_common(self):
        o=self.reserve()[0]
        self.assertEqual(self.ledger.recover_orders(ARRIVAL)[0]['action'],'CANCEL_EXPIRED_UNATTEMPTED')
        self.ledger.recovery_transition('w',o['order_id'],'CANCELLED','EXPIRED_KNOWN_UNATTEMPTED',ARRIVAL)
        w=self.ledger.snapshot()['wallets']['w'];lifecycle_module.assert_native_parity(w)
        self.assertEqual(self.ledger.free(w),100);self.assertEqual(w['cash'],'100')
        self.assertEqual(w['policy_state']['orders'][o['order_id']]['status'],'ASSUMED_TERMINAL_NO_FILL')

    def test_restart_ambiguous_attempt_cannot_release_reservation(self):
        o=self.reserve()[0];self.ledger.begin_attempt('w',o['order_id'],ARRIVAL)
        self.ledger.db.close();self.ledger=Ledger(self.path,[profile()])
        self.assertEqual(self.ledger.recover_orders(ARRIVAL+1)[0]['action'],'QUARANTINE_UNKNOWN')
        with self.assertRaises(ValueError):self.ledger.recovery_transition('w',o['order_id'],'CANCELLED','UNSAFE',ARRIVAL+1)
        self.ledger.recovery_transition('w',o['order_id'],'UNKNOWN','INTERRUPTED_NATIVE_ATTEMPT',ARRIVAL+1)
        w=self.ledger.snapshot()['wallets']['w'];lifecycle_module.assert_native_parity(w)
        self.assertEqual(self.ledger.free(w),D('99.4825'));self.assertEqual(w['cash'],'100')

class BuyingReliabilityMigrationTests(unittest.TestCase):
    def manifests(self):
        old={'release_id':'FOUR-CITY-PAPER-BET-RANGES-v1','paper_only':True,'actual_orders':0,
             'checkpoint_month':'2026-10','no_automatic_month_fallback':True,
             'profiles':[{'wallet_id':'w','city':'ny','profile_id':'native','daily_cap':'.10'}],
             'files':[{'path':'vendor/source/model.py','sha256':'native-source-same'},
                      {'path':'tempdesk/ledger.py','sha256':'old-implementation'}]}
        new=deepcopy(old);new.update(release_id=TARGET,buying_reliability_policy=POLICY)
        new['files'][1]['sha256']='authorized-repair';return old,new
    def state(self):
        return {'release_id':'FOUR-CITY-PAPER-BET-RANGES-v1','capacity':{'observed':{'displayed':'10','consumed':'0'}},
                'wallets':{'w':{'wallet_id':'w','city':'ny','profile_id':'native','initial_cash':'100','cash':'100',
                               'realized':'0','fees':'0','orders':{},'positions':{},'history':[],
                               'daily_opening':{DAY:'100'},'daily_spend':{},'policy_state':{},'policy_state_version':0}}}
    def database(self,folder,state):
        path=Path(folder)/'wallet.sqlite3';db=sqlite3.connect(path)
        db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,json TEXT)')
        db.execute('CREATE TABLE journal(at_ns INTEGER,kind TEXT,payload TEXT)')
        db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(state),));db.commit();db.close();return path
    def read(self,path):
        db=sqlite3.connect(path)
        try:return json.loads(db.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
        finally:db.close()
    def test_unused_migration_preserves_every_balance_and_metadata(self):
        old,new=self.manifests();before=self.state()
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as folder:
            path=self.database(folder,before);receipt=migrate(path,old,new)
            self.assertFalse(receipt['balances_reset']);self.assertEqual(self.read(path),{**before,'release_id':TARGET})
            self.assertEqual(json.loads((Path(folder)/receipt['backup']).read_bytes()),before)
            db=sqlite3.connect(path)
            try:self.assertEqual(db.execute('SELECT count(*) FROM journal').fetchone()[0],1)
            finally:db.close()
            self.assertTrue(migrate(path,old,new)['already_current'])
    def test_active_wallet_is_rejected_without_changing_or_resetting_it(self):
        old,new=self.manifests()
        variants=[{'cash':'99.48'},{'orders':{'o':{'status':'PENDING','reserved_outlay':'.52'}}},
                  {'orders':{'o':{'status':'UNKNOWN','reserved_outlay':'.52'}}},
                  {'positions':{'t|NO':{'quantity':1,'basis':'.52'}}},{'history':[{'profit':'.48'}]},
                  {'policy_state':{'orders':{'native':{'status':'PENDING'}}}}]
        for change in variants:
            with self.subTest(change=change),tempfile.TemporaryDirectory(dir=TEMP_ROOT) as folder:
                before=self.state();before['wallets']['w'].update(change);path=self.database(folder,before)
                with self.assertRaises(ValueError):migrate(path,old,new)
                self.assertEqual(self.read(path),before);self.assertEqual(list(Path(folder).glob('before-*.json')),[])
    def test_changed_native_source_or_cash_cap_and_unknown_old_release_are_rejected(self):
        old,new=self.manifests();new['files'][0]['sha256']='changed-model'
        with self.assertRaises(ValueError):verify_compatible(old,new)
        old,new=self.manifests();new['profiles'][0]['daily_cap']='.20'
        with self.assertRaises(ValueError):verify_compatible(old,new)
        old,new=self.manifests();old['release_id']='unregistered'
        with self.assertRaises(ValueError):verify_compatible(old,new)

if __name__=='__main__':unittest.main(verbosity=2)
