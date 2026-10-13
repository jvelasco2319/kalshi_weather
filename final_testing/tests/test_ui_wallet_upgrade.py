from pathlib import Path
from copy import deepcopy
import json,sqlite3,tempfile,unittest
from final_testing.tools.migrate_ui_application_wallets import migrate,verify_ui_only

class UiWalletUpgradeTests(unittest.TestCase):
    def manifests(self):
        old={'release_id':'old','profiles':[{'wallet_id':'la:model','daily_cap':'.10'}],
             'checkpoint_month':'2026-10','files':[{'path':'tempdesk/ledger.py','sha256':'same'},
             {'path':'source-map.json','sha256':'same'},{'path':'static/app.js','sha256':'before'}]}
        new=deepcopy(old);new['release_id']='new';new['files'][-1]['sha256']='after';return old,new

    def test_changed_model_or_money_code_is_rejected(self):
        old,new=self.manifests();new['files'][0]['sha256']='changed'
        with self.assertRaises(ValueError):verify_ui_only(old,new)
        old,new=self.manifests();new['profiles'][0]['daily_cap']='.20'
        with self.assertRaises(ValueError):verify_ui_only(old,new)

    def test_active_cash_orders_positions_history_and_capacity_are_exactly_preserved(self):
        old,new=self.manifests()
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'paper.sqlite3';db=sqlite3.connect(file)
            db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY,json TEXT)')
            db.execute('CREATE TABLE journal(at_ns INTEGER,kind TEXT,payload TEXT)')
            state={'release_id':'old','wallets':{'la:model':{'cash':'84.32','fees':'1.24',
                'orders':{'pending':{'status':'UNKNOWN','reserved_outlay':'3.55'}},
                'positions':{'contract|NO':{'quantity':17,'basis':'12.33'}},'history':[{'profit':'4.56'}]}},
                'capacity':{'shared-price':{'consumed':'17'}}}
            db.execute('INSERT INTO state VALUES(1,?)',(json.dumps(state),));db.commit();db.close()
            receipt=migrate(file,old,new);self.assertTrue(receipt['wallets_and_capacity_exactly_preserved'])
            db=sqlite3.connect(file);after=json.loads(db.execute('SELECT json FROM state').fetchone()[0]);db.close()
            self.assertEqual(after,{**state,'release_id':'new'})
            self.assertEqual(json.loads((Path(folder)/'before-ui-new.json').read_bytes()),state)

if __name__=='__main__':unittest.main()
