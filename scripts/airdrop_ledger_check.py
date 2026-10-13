"""Local ledger integration checks; uses disposable databases only."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import Flask
from binance_alpha.services.store_application import StoreApplication
from binance_alpha.constants import ENABLED, DISABLED
from binance_alpha.local_database import LocalDatabase
from binance_alpha.repositories.sqlite_airdrop_repository import SQLiteAirdropRepository
from binance_alpha.services.airdrop_ledger_service import AirdropLedgerService
from binance_alpha.web import register_routes


class LedgerChecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='alpha-airdrop-check-')
        self.addCleanup(self.folder.cleanup)
        self.db = LocalDatabase(Path(self.folder.name))
        self.db.sync_members([{'name':'test-A', 'status':ENABLED}, {'name':'test-B', 'status':ENABLED}])
        self.expected = {'score':'13','before_balance':'100','after_balance':'97','manual_wear':'3','income':'6.5','other_expense':'2'}
        self.db.record_score_entries('2026-10-04', [{'name':'test-A', **self.expected}])
        self.repo = SQLiteAirdropRepository(self.db._connect)
        self.service = AirdropLedgerService(self.repo)
        members = self.service.view()['members']
        self.event = {'id':'event1','token':'CT','date':'2026-09-30','amount':200,'price':0.5,'priceAt':'2026-10-04T18:00',
                      'quoteMode':'manual','historyId':'6cae33ad67e8b4135d483607','accounts':[
                          {**m,'amount':200,'claimed':True} for m in members], 'sales':[
                          {'id':'sale1','account':members[0]['id'],'quantity':200,'price':0.4,'fee':0.5,'time':'2026-10-04T18:01'}]}
        self.payload = {'revision':0,'state':{'selected':'event1','events':[self.event]}}

    def test_save_reopen_and_daily_fields(self):
        self.assertEqual(self.service.save(self.payload), 1)
        reopened_db = LocalDatabase(Path(self.folder.name))
        service = AirdropLedgerService(SQLiteAirdropRepository(reopened_db._connect))
        result = service.view()
        event = result['state']['events'][0]
        for key in ('token','date','amount','price','priceAt','historyId','quoteMode','accounts','sales'):
            self.assertEqual(event[key], self.event[key])
        saved = reopened_db.get_score_rows_for_date('2026-10-04')[0]
        for key, value in self.expected.items():
            self.assertEqual(str(saved[key]), value)

    def test_stale_window_cannot_overwrite(self):
        self.service.save(self.payload)
        with self.assertRaises(RuntimeError):
            self.service.save(self.payload)
        self.assertEqual(self.repo.read()['revision'], 1)

    def test_manual_order_survives_save_reload_and_members_refresh(self):
        self.event['accounts'].reverse()
        self.service.save(self.payload)
        self.assertEqual([a['name'] for a in self.service.view()['state']['events'][0]['accounts']], ['test-B','test-A'])
        self.db.add_member('test-C')
        refreshed = self.service.view()
        self.assertEqual([a['name'] for a in refreshed['state']['events'][0]['accounts']], ['test-B','test-A','test-C'])
        self.service.save({'revision':refreshed['revision'], 'state':refreshed['state']})
        self.assertEqual([a['name'] for a in self.repo.read()['state']['events'][0]['accounts']], ['test-B','test-A','test-C'])
        self.assertEqual(self.repo.read()['state']['events'][0]['sales'], self.event['sales'])

    def test_members_refresh_preserves_claims_sales_and_amounts(self):
        self.event['accounts'][1]['claimed'] = False
        self.service.save(self.payload)
        self.db.update_member('test-A', '', status=DISABLED)
        self.db.update_member('test-B', '', status=DISABLED)
        result = self.service.view()
        self.assertEqual(result['members'], [])
        event = result['state']['events'][0]
        self.assertEqual(len(event['accounts']), 1)
        self.assertTrue(event['accounts'][0]['inactive'])
        self.assertTrue(event['accounts'][0]['claimed'])
        self.assertEqual(event['sales'], self.event['sales'])
        self.assertEqual(event['accounts'][0]['amount'], 200)
        # A stale browser cannot put an unclaimed disabled account back on screen.
        self.payload['revision'] = 1
        self.service.save(self.payload)
        self.assertEqual(len(self.repo.read()['state']['events'][0]['accounts']), 1)
        self.db.update_member('test-A', '', status=ENABLED)
        self.db.update_member('test-B', '', status=ENABLED)
        self.db.add_member('test-C')
        event = self.service.view()['state']['events'][0]
        self.assertEqual(len(event['accounts']), 3)
        self.assertFalse(any(a.get('inactive') for a in event['accounts']))
        self.assertEqual(len(event['sales']), 1)
        self.assertFalse(event['accounts'][1]['claimed'])
        self.assertEqual(event['accounts'][2]['amount'], 200)

    def test_disabled_claim_without_sale_retains_remaining_value(self):
        self.service.save(self.payload)
        self.db.update_member('test-B', '', status=DISABLED)
        event = self.service.view()['state']['events'][0]
        account = next(a for a in event['accounts'] if a['name'] == 'test-B')
        self.assertTrue(account['inactive'])
        self.assertTrue(account['claimed'])
        self.assertEqual(account['amount'] * event['price'], 100)

    def test_partial_or_duplicate_sales_rejected(self):
        bad = deepcopy(self.payload)
        bad['state']['events'][0]['sales'][0]['quantity'] = 100
        with self.assertRaises(ValueError): self.service.save(bad)
        bad = deepcopy(self.payload)
        bad['state']['events'][0]['sales'].append({**bad['state']['events'][0]['sales'][0], 'id':'sale2'})
        with self.assertRaises(ValueError): self.service.save(bad)
        self.assertEqual(self.repo.read()['state']['events'], [])

    def test_failed_database_write_rolls_back(self):
        self.service.save(self.payload)
        with closing(self.db._connect()) as connection:
            connection.execute("CREATE TRIGGER fail_airdrop_insert BEFORE INSERT ON airdrop_sales BEGIN SELECT RAISE(ABORT, 'test'); END")
            connection.commit()
        updated = deepcopy(self.payload)
        updated['revision'] = 1
        updated['state']['events'][0]['token'] = 'CHANGED'
        with self.assertRaises(Exception): self.service.save(updated)
        self.assertEqual(self.repo.read()['state']['events'][0]['token'], 'CT')
        self.assertEqual(self.repo.read()['revision'], 1)

    def test_explicit_delete_preserves_other_event_and_daily_fields(self):
        other = deepcopy(self.event)
        other.update(id='event2', token='XDP')
        other['sales'][0]['id'] = 'sale2'
        self.payload['state']['events'].append(other)
        self.service.save(self.payload)
        result = self.service.delete({'event_id':'event1', 'revision':1})
        self.assertEqual(result, {'revision':2, 'selected':'event2'})
        saved = self.repo.read()
        self.assertEqual(saved['state']['events'], [self.service.view()['state']['events'][0]])
        self.assertEqual(saved['state']['events'][0]['sales'], other['sales'])
        self.assertEqual(saved['state']['events'][0]['accounts'], other['accounts'])
        daily = self.db.get_score_rows_for_date('2026-10-04')[0]
        for key, value in self.expected.items():
            self.assertEqual(str(daily[key]), value)
        with closing(self.db._connect()) as db:
            for table in ('airdrop_claims', 'airdrop_sales'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE event_id=?', ('event1',)).fetchone()[0], 0)
        self.assertEqual(self.service.delete({'event_id':'event2', 'revision':2}), {'revision':3, 'selected':''})
        self.assertEqual(self.repo.read()['state'], {'selected':'', 'events':[]})

    def test_delete_nonselected_event_preserves_selection(self):
        other = deepcopy(self.event)
        other.update(id='event2', token='XDP', sales=[])
        self.payload['state']['events'].append(other)
        self.service.save(self.payload)
        self.assertEqual(self.service.delete({'event_id':'event2', 'revision':1})['selected'], 'event1')
        self.assertEqual(self.repo.read()['state']['events'][0]['sales'], self.event['sales'])

    def test_stale_invalid_and_implicit_deletion_rejected(self):
        self.service.save(self.payload)
        before = self.repo.read()
        with self.assertRaises(RuntimeError):
            self.service.delete({'event_id':'event1', 'revision':0})
        for payload in (None, {}, {'event_id':'event1', 'revision':True}, {'event_id':'missing', 'revision':1}):
            with self.assertRaises(ValueError): self.service.delete(payload)
        with self.assertRaises(ValueError):
            self.service.save({'revision':1, 'state':{'selected':'', 'events':[]}})
        self.assertEqual(self.repo.read(), before)

    def test_failed_delete_rolls_back_records_and_revision(self):
        self.service.save(self.payload)
        before = self.repo.read()
        with closing(self.db._connect()) as db:
            db.execute("CREATE TRIGGER fail_airdrop_delete BEFORE DELETE ON airdrop_claims BEGIN SELECT RAISE(ABORT, 'test'); END")
            db.commit()
        with self.assertRaises(Exception):
            self.service.delete({'event_id':'event1', 'revision':1})
        self.assertEqual(self.repo.read(), before)

    def test_page_and_save_route(self):
        app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))
        app.secret_key = 'isolated-test'
        store = SimpleNamespace(read_only=False, workbook_path=Path('test.xlsx'), get_quick_scores=lambda:[], airdrop_ledger_service=self.service)
        register_routes(app, store)
        client = app.test_client()
        page = client.get('/airdrops')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'"mode": "local"',page.data)
        self.assertEqual(client.post('/api/airdrops/save',json=self.payload).json['ok'],True)
        self.assertIn(b'event1',client.get('/airdrops').data)
        self.db.update_member('test-B', '', status=DISABLED)
        members = client.get('/api/airdrops/members')
        self.assertEqual([m['name'] for m in members.json['members']], ['test-A'])
        self.assertIn('no-store', members.headers['Cache-Control'])
        conflict = client.post('/api/airdrops/delete', json={'event_id':'event1', 'revision':0}).json
        self.assertFalse(conflict['ok'])
        self.assertTrue(conflict['conflict'])
        self.assertFalse(client.post('/api/airdrops/delete', json={}).json['ok'])
        deleted = client.post('/api/airdrops/delete', json={'event_id':'event1', 'revision':1}).json
        self.assertEqual(deleted, {'ok':True, 'revision':2, 'selected':''})
        self.assertEqual(self.repo.read()['state']['events'], [])


if __name__ == '__main__':
    if '--serve' in sys.argv:
        from binance_alpha.services.store_application import StoreApplication
        directory = Path(tempfile.mkdtemp(prefix='alpha-airdrop-ui-'))
        store = StoreApplication(directory)
        app = Flask(__name__, template_folder=str(ROOT/'templates'), static_folder=str(ROOT/'static'))
        app.secret_key='isolated-ui-test'
        app.config['TEMPLATES_AUTO_RELOAD']=True
        register_routes(app,store)
        print('Isolated test data:',directory,flush=True)
        app.run(host='127.0.0.1',port=5058,debug=False)
    else:
        unittest.main()
