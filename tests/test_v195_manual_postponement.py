"""Synthetic temporary SQLite API tests. No inherited DB configuration/network."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_v194 as fixture
import test_v194_remaining as previous
from app import main


class ManualPostponementCases:
    setup_calendar = previous.RemainingTests.setup_calendar
    delivery = previous.RemainingTests.delivery
    row = previous.RemainingTests.row
    snapshot = previous.RemainingTests.snapshot

    def prepare(self):
        self.setup_calendar()
        with self.connection() as c:
            c.execute("UPDATE branches SET delivery_weekdays='5' WHERE id IN (1,2)")
            c.execute('UPDATE drivers SET pin_hash=? WHERE id=1', (main.hash_secret('8642'),))
            c.execute("INSERT INTO driver_devices(driver_id,device_key,activated_at) VALUES(1,'v195-preserved-device',?)", (main.now(),))
            device=c.execute("SELECT id FROM driver_devices WHERE device_key='v195-preserved-device'").fetchone()['id']
            c.execute('INSERT INTO driver_sessions(token_hash,driver_id,device_id,expires_at) VALUES(?,1,?,?)',
                      (main.thash('v195-preserved-session'),device,'2099-01-01T00:00:00+00:00'))
            c.execute('INSERT INTO driver_activation_tokens(driver_id,token_hash,created_at,expires_at) VALUES(1,?,?,?)',
                      (main.thash('v195-preserved-qr'), main.now(),'2099-01-01T00:00:00+00:00'))
        return self.delivery('2030-01-04')

    def request(self, **changes):
        body = dict(exception_type='TYPHOON', start_date='2030-01-04',
                    end_date='2030-01-04', manual_target_date='2030-01-07', reason='合成颱風測試')
        body.update(changes)
        return self.client.post('/api/schedule-exceptions/range', headers=self.admin, json=body)

    def confirm(self, **changes):
        preview = self.request(preview=True, **changes)
        self.assertEqual(preview.status_code, 200, preview.text)
        return self.request(plan_token=preview.json()['plan_token'], **changes)

    def test_friday_to_nonfixed_monday_survives_rebuild_and_prefill(self):
        source = self.prepare()
        before = self.snapshot()
        preview = self.request(preview=True)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(before, self.snapshot())
        item = preview.json()['plan'][0]
        self.assertEqual((item['target_date'], item['total']), ('2030-01-07', 5))
        result = self.request(plan_token=preview.json()['plan_token'])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.row(source)['status'], 'VOID')
        with self.connection() as c:
            for _ in range(3): main.rebuild_service_date(c, '2030-01-07', commit=False)
            branch = c.execute('SELECT * FROM branches WHERE id=1').fetchone()
            self.assertTrue(main.branch_expected_on(c, branch, '2030-01-07'))
            target = main.ensure_prefill_delivery(c, '2030-01-07', 1)
            self.assertEqual(target['document_final'], 5)
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM deliveries WHERE service_date='2030-01-07' AND branch_id=1").fetchone()['n'], 1)
            self.assertEqual(c.execute("SELECT exception_type FROM delivery_exceptions WHERE service_date='2030-01-07' AND branch_id=1").fetchone()['exception_type'], 'ADD')
            self.assertTrue(c.execute("SELECT 1 FROM audit_logs WHERE action='ADD_MANUAL_DOCUMENT_DAY'").fetchone())

    def test_invalid_dates_and_non_typhoon_are_read_only(self):
        self.prepare()
        for value in ('2030-01-05', '2030-01-06', '2030-01-04', '2030-01-03',
                      '2029-12-31', '2030-02-30', '20300107', '', 20300107, True):
            with self.subTest(value=value):
                before = self.snapshot()
                self.assertIn(self.request(preview=True, manual_target_date=value).status_code, (400,409))
                self.assertEqual(before, self.snapshot())
        self.assertEqual(self.request(preview=True, exception_type='CLOSED_ALL').status_code, 400)
        self.assertEqual(self.request(preview=True, end_date='2030-01-08').status_code, 409)

    def test_closed_holiday_stop_and_locked_target_rejected(self):
        self.prepare()
        for table, sql in (
            ('global_closures', "INSERT INTO global_closures(service_date,reason) VALUES('2030-01-07','已設定國定假日')"),
            ('delivery_exceptions', "INSERT INTO delivery_exceptions(service_date,branch_id,exception_type) VALUES('2030-01-07',1,'STOP')"),
            ('daily_reports', "INSERT INTO daily_reports(service_date,status) VALUES('2030-01-07','LOCKED')")):
            with self.connection() as c: c.execute(sql)
            before = self.snapshot()
            self.assertEqual(self.request(preview=True).status_code, 409)
            self.assertEqual(before, self.snapshot())
            # Only this test's synthetic temporary fixture is reset.
            with self.connection() as c: c.execute('DELETE FROM '+table+" WHERE service_date='2030-01-07'")

    def test_sum_requires_preview_and_explicit_consent(self):
        self.prepare(); target = self.delivery('2030-01-07', qty=8)
        before = self.snapshot()
        self.assertEqual(self.request(conflict_policy='sum').status_code, 409)
        self.assertEqual(before, self.snapshot())
        preview = self.request(preview=True).json()
        self.assertTrue(preview['has_conflicts'])
        self.assertEqual(preview['plan'][0]['total'], 13)
        self.assertEqual(self.request(plan_token=preview['plan_token']).status_code, 409)
        result = self.request(plan_token=preview['plan_token'], conflict_policy='sum')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((self.row(target)['document_original'], self.row(target)['document_final']), (8,13))
        self.assertEqual(self.request(plan_token=preview['plan_token'], conflict_policy='sum').status_code, 409)
        self.assertEqual(self.row(target)['document_final'], 13)

    def test_changed_entire_row_or_target_date_invalidates_preview(self):
        source = self.prepare()
        token = self.request(preview=True).json()['plan_token']
        # No row_version change: full-row fingerprint must still detect it.
        with self.connection() as c: c.execute('UPDATE deliveries SET document_original=6 WHERE id=?', (source,))
        before = self.snapshot()
        self.assertEqual(self.request(plan_token=token).status_code, 409)
        self.assertEqual(before, self.snapshot())
        token = self.request(preview=True).json()['plan_token']
        self.assertEqual(self.request(plan_token=token, manual_target_date='2030-01-08').status_code,409)

    def test_target_added_or_edited_after_preview_rejected(self):
        self.prepare(); token = self.request(preview=True).json()['plan_token']
        target = self.delivery('2030-01-07', qty=2)
        self.assertEqual(self.request(plan_token=token, conflict_policy='sum').status_code,409)
        token = self.request(preview=True).json()['plan_token']
        with self.connection() as c: c.execute('UPDATE deliveries SET document_final=3 WHERE id=?',(target,))
        self.assertEqual(self.request(plan_token=token, conflict_policy='sum').status_code,409)

    def test_driver_operations_signatures_books_block_without_mutation(self):
        source = self.prepare()
        for field, value in (('outbound_original',0), ('outbound_final',2), ('inbound_final',0),
                             ('branch_signature',fixture.PNG), ('driver_confirmed_at','synthetic'),
                             ('status','COMPLETED'), ('receipt_at','synthetic')):
            with self.subTest(field=field):
                with self.connection() as c: c.execute('UPDATE deliveries SET '+field+'=? WHERE id=?',(value,source))
                before = self.snapshot()
                self.assertEqual(self.request(preview=True).status_code,409)
                self.assertEqual(before, self.snapshot())
                with self.connection() as c: c.execute('UPDATE deliveries SET '+field+'=? WHERE id=?',('WAITING_DRIVER' if field=='status' else None,source))

    def test_started_target_route_and_books_rejected(self):
        self.prepare(); target = self.delivery('2030-01-07', qty=2)
        with self.connection() as c: c.execute('UPDATE deliveries SET outbound_original=0 WHERE id=?',(target,))
        before = self.snapshot()
        self.assertEqual(self.request(preview=True,conflict_policy='sum').status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_items_assignments_and_signed_route_block(self):
        source=self.prepare()
        with self.connection() as c:
            c.execute("INSERT INTO delivery_items(delivery_id,item_type,quantity) VALUES(?,'BOOK',2)",(source,))
        before=self.snapshot(); self.assertEqual(self.request(preview=True).status_code,409)
        self.assertEqual(before,self.snapshot())
        with self.connection() as c:
            c.execute('DELETE FROM delivery_items WHERE delivery_id=?',(source,))
            c.execute('INSERT INTO delivery_driver_assignments(delivery_id,driver_id) VALUES(?,1)',(source,))
        before=self.snapshot(); self.assertEqual(self.request(preview=True).status_code,409)
        self.assertEqual(before,self.snapshot())
        with self.connection() as c:
            c.execute('DELETE FROM delivery_driver_assignments WHERE delivery_id=?',(source,))
            c.execute('UPDATE daily_routes SET secretary_signature=?',(fixture.PNG,))
        before=self.snapshot(); self.assertEqual(self.request(preview=True).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_secretary_allowed_other_roles_denied(self):
        self.prepare()
        self.assertEqual(self.client.post('/api/schedule-exceptions/range',headers=self.headers,json={}).status_code,401)
        with self.connection() as c:
            c.execute("UPDATE users SET role='SECRETARY' WHERE username='test-admin'")
        self.assertEqual(self.confirm().status_code,200)

    def test_multi_day_quantity_conserved(self):
        self.prepare(); self.delivery('2030-01-07',qty=7)
        result=self.confirm(end_date='2030-01-07',manual_target_date='2030-01-08',conflict_policy='sum')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['plan'][0]['total'],12)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT SUM(document_final) AS n FROM deliveries WHERE status<>'VOID'").fetchone()['n'],12)

    def test_atomic_rollback_including_add_exception_and_audit(self):
        self.prepare(); preview=self.request(preview=True).json()
        before=self.snapshot()
        with patch.object(main,'rebuild_service_date',side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError): self.request(plan_token=preview['plan_token'])
        self.assertEqual(before,self.snapshot())

    def test_concurrent_confirmation_moves_once(self):
        self.prepare(); token=self.request(preview=True).json()['plan_token']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.request(plan_token=token),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM audit_logs WHERE action='POSTPONE_DOCUMENT_SOURCE'").fetchone()['n'],1)
            self.assertEqual(c.execute("SELECT SUM(document_final) AS n FROM deliveries WHERE status<>'VOID'").fetchone()['n'],5)

    def test_automatic_still_uses_fixed_weekdays(self):
        self.prepare()
        response=self.request(manual_target_date=None)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['plan'][0]['target_date'],'2030-01-11')

    def test_accounts_history_and_signatures_outside_range_unchanged(self):
        self.prepare(); self.delivery('2030-01-03',outbound_original=9,branch_signature=fixture.PNG,status='COMPLETED')
        tables=('users','sessions','drivers','driver_devices','driver_sessions','driver_activation_tokens','branch_sessions','branch_signers')
        def protected():
            with self.connection() as c:
                return {**{t:[dict(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall()] for t in tables},
                        'history':[dict(r) for r in c.execute("SELECT * FROM deliveries WHERE service_date<'2030-01-04'").fetchall()]}
        before=protected(); result=self.confirm()
        self.assertEqual(result.status_code,200,result.text); self.assertEqual(before,protected())
        response=self.client.get('/api/driver-activation/v195-preserved-qr')
        self.assertEqual(response.status_code,200,response.text)
        response=self.client.post('/api/driver/unlock',json={'device_key':'v195-preserved-device','pin':'8642'})
        self.assertEqual(response.status_code,200,response.text)
        with self.connection() as c:
            self.assertTrue(c.execute('SELECT 1 FROM driver_sessions WHERE token_hash=?',(main.thash('v195-preserved-session'),)).fetchone())

    def test_cancel_source_closure_keeps_manual_target(self):
        self.prepare(); self.assertEqual(self.confirm().status_code,200)
        with self.connection() as c: eid=c.execute("SELECT id FROM global_closures WHERE service_date='2030-01-04'").fetchone()['id']
        response=self.client.delete('/api/schedule-exceptions/G'+str(eid),headers=self.admin)
        self.assertEqual(response.status_code,200,response.text)
        with self.connection() as c:
            main.rebuild_service_date(c,'2030-01-07',commit=False)
            self.assertEqual(main.ensure_prefill_delivery(c,'2030-01-07',1)['document_final'],5)


class SQLiteManualPostponementTests(ManualPostponementCases, unittest.TestCase):
    setUp = fixture.V194Tests.setUp
    connection = fixture.V194Tests.connection


if __name__ == '__main__': unittest.main()
