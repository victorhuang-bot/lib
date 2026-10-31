"""V19.4 remaining workflows; synthetic SQLite copies and blocked network only."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_v194 as fixture
from app import main


class RemainingTests(unittest.TestCase):
    setUp = fixture.V194Tests.setUp
    connection = fixture.V194Tests.connection

    def setup_calendar(self):
        clock = patch.object(main, 'today', return_value='2030-01-04')  # Friday
        clock.start(); self.addCleanup(clock.stop)
        with self.connection() as c:
            c.execute("UPDATE branches SET active=0")
            c.execute("UPDATE branches SET active=1,route_id=1,delivery_weekdays='1,2,3,4,5' WHERE id IN (1,2)")
        return '2030-01-04'

    def delivery(self, day, bid=1, qty=5, **fields):
        with self.connection() as c:
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,1,1) ON CONFLICT(service_date,route_id) DO NOTHING', (day,))
            rid=c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=1',(day,)).fetchone()['id']
            c.execute("INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_original,document_final) VALUES(?,?,?,'WAITING_DRIVER',?,?)", (day,rid,bid,qty,qty))
            did=c.execute('SELECT id FROM deliveries WHERE service_date=? AND branch_id=?',(day,bid)).fetchone()['id']
            for key,value in fields.items():
                c.execute(f'UPDATE deliveries SET {key}=? WHERE id=?',(value,did))
        return did

    def save(self, start='2030-01-04', **changes):
        body={'exception_type':'STOP','branch_id':1,'start_date':start,'end_date':start,'reason':'隔離測試'}
        body.update(changes)
        return self.client.post('/api/schedule-exceptions/range',headers=self.admin,json=body)

    def row(self, did):
        with self.connection() as c: return dict(c.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone())

    def snapshot(self):
        with self.connection() as c:
            return {t:[tuple(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY 1')] for t in
                    ('deliveries','daily_routes','global_closures','delivery_exceptions','audit_logs')}

    def test_preview_is_read_only_and_friday_moves_to_monday(self):
        self.setup_calendar(); did=self.delivery('2030-01-04')
        before=self.snapshot(); r=self.save(preview=True)
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(before,self.snapshot())
        self.assertEqual(r.json()['plan'][0]['target_date'],'2030-01-07')
        result=self.save(plan_token=r.json()['plan_token'])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.row(did)['status'],'VOID')
        self.assertEqual(self.row(did)['document_original'],5)
        self.assertIsNone(self.row(did)['document_final'])
        with self.connection() as c:
            target=c.execute("SELECT * FROM deliveries WHERE service_date='2030-01-07' AND branch_id=1").fetchone()
            self.assertEqual(target['document_final'],5)
            self.assertTrue(c.execute("SELECT 1 FROM audit_logs WHERE action='POSTPONE_DOCUMENT_SOURCE'").fetchone())

    def test_skip_existing_closure_stop_and_weekday(self):
        self.setup_calendar(); self.delivery('2030-01-04')
        with self.connection() as c:
            c.execute("INSERT INTO global_closures(service_date) VALUES('2030-01-07')")
            c.execute("INSERT INTO delivery_exceptions(service_date,branch_id,exception_type) VALUES('2030-01-08',1,'STOP')")
        r=self.save(); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['plan'][0]['target_date'],'2030-01-09')

    def test_typhoon_all_branches_and_full_range(self):
        self.setup_calendar(); self.delivery('2030-01-04'); self.delivery('2030-01-04',2,7)
        r=self.save(exception_type='TYPHOON',end_date='2030-01-07')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual({x['target_date'] for x in r.json()['plan']},{'2030-01-08'})
        with self.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM global_closures').fetchone()[0],4)
            self.assertEqual(c.execute("SELECT SUM(document_final) FROM deliveries WHERE service_date='2030-01-08'").fetchone()[0],12)

    def test_default_collision_blocks_and_explicit_sum_preserves_original(self):
        self.setup_calendar(); self.delivery('2030-01-04',qty=5); target=self.delivery('2030-01-07',qty=8)
        before=self.snapshot(); r=self.save()
        self.assertEqual(r.status_code,409,r.text); self.assertEqual(before,self.snapshot())
        r=self.save(conflict_policy='sum'); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(target)['document_original'],8)
        self.assertEqual(self.row(target)['document_final'],13)
        self.assertEqual(self.save(conflict_policy='sum').status_code,200)
        self.assertEqual(self.row(target)['document_final'],13)

    def test_multiple_sources_same_target_require_explicit_sum(self):
        self.setup_calendar(); self.delivery('2030-01-04',qty=5); self.delivery('2030-01-07',qty=7)
        self.assertEqual(self.save(end_date='2030-01-07').status_code,409)
        r=self.save(end_date='2030-01-07',conflict_policy='sum')
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['plan'][0]['total'],12)

    def test_stale_preview_is_rejected(self):
        self.setup_calendar(); source=self.delivery('2030-01-04')
        token=self.save(preview=True).json()['plan_token']
        with self.connection() as c: c.execute('UPDATE deliveries SET document_final=9,row_version=row_version+1 WHERE id=?',(source,))
        self.assertEqual(self.save(plan_token=token,conflict_policy='sum').status_code,409)
        self.assertEqual(self.row(source)['status'],'WAITING_DRIVER')

    def test_concurrent_duplicate_requests_move_once(self):
        self.setup_calendar(); self.delivery('2030-01-04')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.save(),range(2)))
        self.assertEqual([r.status_code for r in results],[200,200])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='POSTPONE_DOCUMENT_SOURCE'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT SUM(document_final) FROM deliveries WHERE status<>'VOID' AND service_date>='2030-01-04'").fetchone()[0],5)

    def test_zero_is_a_real_prefill_but_zero_outbound_is_started(self):
        self.setup_calendar(); source=self.delivery('2030-01-04',qty=0)
        r=self.save(); self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(source)['status'],'VOID'); self.assertEqual(r.json()['plan'][0]['total'],0)

    def test_started_signed_and_locked_sources_reject_atomically(self):
        for fields in ({'outbound_original':0},{'outbound_final':0},{'branch_signature':fixture.PNG},
                       {'status':'WAITING_BRANCH'},{'inbound_final':0},{'driver_confirmed_at':'x'}):
            with self.subTest(fields=fields):
                self.setup_calendar(); did=self.delivery('2030-01-04',**fields)
                before=self.snapshot(); r=self.save()
                self.assertEqual(r.status_code,409,r.text); self.assertEqual(before,self.snapshot())
                with self.connection() as c: c.execute('DELETE FROM deliveries WHERE id=?',(did,))

    def test_locked_target_and_signed_route_reject(self):
        self.setup_calendar(); did=self.delivery('2030-01-04')
        with self.connection() as c: c.execute("INSERT INTO daily_reports(service_date,status) VALUES('2030-01-07','LOCKED')")
        self.assertEqual(self.save().status_code,409)
        with self.connection() as c:
            c.execute("DELETE FROM daily_reports WHERE service_date='2030-01-07'")
            c.execute("UPDATE daily_routes SET driver_signature=? WHERE id=?",(fixture.PNG,self.row(did)['daily_route_id']))
        self.assertEqual(self.save().status_code,409)

    def test_target_already_started_rejects_even_sum(self):
        self.setup_calendar(); self.delivery('2030-01-04'); self.delivery('2030-01-07',outbound_original=0)
        before=self.snapshot(); self.assertEqual(self.save(conflict_policy='sum').status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_error_rolls_back_all_days_and_target_quantities(self):
        self.setup_calendar(); self.delivery('2030-01-04')
        before=self.snapshot(); original=main.rebuild_service_date
        def failing(c,day,commit=True):
            original(c,day,commit=commit)
            if day=='2030-01-05': raise RuntimeError('simulated failure after earlier day rebuilt')
        with patch.object(main,'rebuild_service_date',side_effect=failing):
            with self.assertRaises(RuntimeError): self.save(end_date='2030-01-05')
        self.assertEqual(before,self.snapshot())

    def test_cancel_does_not_move_documents_back_or_duplicate(self):
        self.setup_calendar(); did=self.delivery('2030-01-04'); self.assertEqual(self.save().status_code,200)
        with self.connection() as c: eid=c.execute("SELECT id FROM delivery_exceptions WHERE branch_id=1").fetchone()[0]
        r=self.client.delete('/api/schedule-exceptions/'+str(eid),headers=self.admin)
        self.assertEqual(r.status_code,200,r.text); self.assertFalse(r.json()['documents_moved_back'])
        self.assertIsNone(self.row(did)['document_final']); self.assertIsNone(self.row(did)['document_original'])
        self.assertEqual(self.row(did)['status'],'WAITING_SECRETARY')
        self.assertEqual(self.save().status_code,200)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT document_final FROM deliveries WHERE service_date='2030-01-07' AND branch_id=1").fetchone()[0],5)

    def test_void_excluded_from_reports_and_original_fallback_totals(self):
        self.setup_calendar(); self.delivery('2030-01-04'); self.save()
        r=self.client.get('/api/dashboard/monthly-route-cumulative?month=2030-01',headers=self.admin)
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['grand_total']['document'],5)
        with self.connection() as c:
            rows=main.report_rows(c,'2030-01-04','2030-01-07')
            self.assertEqual(sum(x['公文送出'] or 0 for x in rows),5)
            code=c.execute('SELECT code FROM branches WHERE id=1').fetchone()[0]
            self.assertNotIn(code,[x['分館代碼'] for x in rows if x['日期']=='2030-01-04'])

    def test_past_date_permissions_and_add_on_closure(self):
        self.setup_calendar()
        self.assertEqual(self.save(start='2030-01-03').status_code,409)
        self.assertEqual(self.client.post('/api/schedule-exceptions/range',headers=self.headers,json={}).status_code,401)
        with self.connection() as c: c.execute("INSERT INTO global_closures(service_date) VALUES('2030-01-07')")
        self.assertEqual(self.save(start='2030-01-07',exception_type='ADD').status_code,409)

    def test_signatures_scoped_read_only_original_and_corrected(self):
        self.setup_calendar(); original=fixture.PNG; corrected=fixture.PNG+'AA'
        did=self.delivery('2030-01-02',status='STOP_COMPLETED',branch_signature=original,
                          correction_signature=corrected,signer_name='隔離簽收人',correction_signer_name='隔離更正人')
        before=self.snapshot()
        r=self.client.get(f'/api/branch-session/history/{did}/signatures',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['branch_signature'],original)
        self.assertEqual(r.json()['correction_signature'],corrected)
        self.assertEqual(self.client.get(f'/api/branch-session/history/{did}/signatures',headers=self.other).status_code,404)
        self.assertEqual(self.client.get(f'/api/branch-session/history/{did}/signatures').status_code,401)
        history=self.client.get('/api/branch-session/history?month=2030-01',headers=self.headers).json()['records']
        self.assertEqual(history[0]['has_signatures'],1); self.assertNotIn('branch_signature',history[0])
        self.assertEqual(before,self.snapshot())

    def test_old_void_inactive_signature_access_denied(self):
        self.setup_calendar()
        old=self.delivery('2028-12-01',branch_signature=fixture.PNG)
        void=self.delivery('2030-01-02',status='VOID',branch_signature=fixture.PNG)
        for did in (old,void):
            self.assertEqual(self.client.get(f'/api/branch-session/history/{did}/signatures',headers=self.headers).status_code,404)
        with self.connection() as c: c.execute('UPDATE branches SET active=0 WHERE id=1')
        self.assertEqual(self.client.get(f'/api/branch-session/history/{void}/signatures',headers=self.headers).status_code,403)

    def test_k13_rename_preserves_identity_credentials_and_history(self):
        self.setup_calendar()
        with self.connection() as c:
            c.execute("UPDATE branches SET code='ISOLATED_K13' WHERE code='K13'")
            c.execute("UPDATE branches SET code='K13S',name='士林替' WHERE id=1")
        did=self.delivery('2030-01-02',branch_signature=fixture.PNG)
        with self.connection() as c:
            before=dict(c.execute('SELECT * FROM branches WHERE id=1').fetchone())
            main.repair_shilin_identity(c); main.repair_shilin_identity(c)
            after=dict(c.execute('SELECT * FROM branches WHERE id=1').fetchone())
            self.assertEqual(after,{**before,'code':'K13','name':'士林'})
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='REPAIR_SHILIN_CODE'").fetchone()[0],1)
        self.assertEqual(self.row(did)['branch_id'],1); self.assertEqual(self.row(did)['branch_signature'],fixture.PNG)

    def test_k13_conflict_disables_legacy_without_merging_or_deleting(self):
        self.setup_calendar()
        with self.connection() as c:
            canonical=c.execute("SELECT id FROM branches WHERE code='K13'").fetchone()[0]
            c.execute("UPDATE branches SET code='K13S',name='士林替' WHERE id=1")
            before=[tuple(r) for r in c.execute('SELECT * FROM deliveries ORDER BY id')]
            main.repair_shilin_identity(c); main.repair_shilin_identity(c)
            legacy=c.execute('SELECT * FROM branches WHERE id=1').fetchone()
            self.assertEqual(legacy['active'],0); self.assertEqual(legacy['code'],'K13S')
            self.assertEqual(c.execute("SELECT id FROM branches WHERE code='K13'").fetchone()[0],canonical)
            self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM deliveries ORDER BY id')])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit_logs WHERE action='SHILIN_IDENTITY_CONFLICT'").fetchone()[0],1)

    def test_unstarted_add_day_is_created_and_duplicate_is_idempotent(self):
        self.setup_calendar()
        r=self.save(start='2030-01-05',exception_type='ADD')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.save(start='2030-01-05',exception_type='ADD').status_code,200)
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM deliveries WHERE service_date='2030-01-05' AND branch_id=1").fetchone()[0],1)

    def test_stale_document_writes_cannot_restore_void_source(self):
        self.setup_calendar(); did=self.delivery('2030-01-04'); self.assertEqual(self.save().status_code,200)
        r=self.client.patch(f'/api/secretary/deliveries/{did}/document',headers=self.admin,json={'qty':99})
        self.assertEqual(r.status_code,409,r.text)
        r=self.client.post('/api/secretary/documents/batch',headers=self.admin,json={'service_date':'2030-01-04','items':[{'id':did,'qty':99}]})
        self.assertEqual(r.status_code,409,r.text)
        r=self.client.post('/api/secretary/documents/zero-all?service_date=2030-01-04',headers=self.admin)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(did)['status'],'VOID');self.assertIsNone(self.row(did)['document_final'])
        self.assertEqual(self.row(did)['document_original'],5)
        with self.assertRaises(main.HTTPException): main._prefill_save_db(1,'ADMIN','2030-01-04',1,99)

    def test_manual_void_target_and_items_are_never_overwritten(self):
        self.setup_calendar(); did=self.delivery('2030-01-04')
        target=self.delivery('2030-01-07',status='VOID')
        self.assertEqual(self.save(conflict_policy='sum').status_code,409)
        with self.connection() as c:
            c.execute('DELETE FROM deliveries WHERE id=?',(target,))
            c.execute("INSERT INTO delivery_items(delivery_id,item_type,quantity) VALUES(?,'OTHER',1)",(did,))
        before=self.snapshot();self.assertEqual(self.save().status_code,409);self.assertEqual(before,self.snapshot())

    def test_inactive_target_branch_cannot_lose_prefill(self):
        self.setup_calendar(); did=self.delivery('2030-01-04')
        with self.connection() as c:c.execute('UPDATE branches SET active=0 WHERE id=1')
        before=self.snapshot();self.assertEqual(self.save(exception_type='TYPHOON').status_code,409)
        self.assertEqual(before,self.snapshot());self.assertEqual(self.row(did)['document_final'],5)

    def test_roster_startup_repairs_k13_before_seeding(self):
        self.setup_calendar()
        with self.connection() as c:
            canonical=c.execute("SELECT id FROM branches WHERE code='K13'").fetchone()[0]
            c.execute("UPDATE branches SET code='K13S',name='士林替' WHERE id=?",(canonical,))
            before=dict(c.execute('SELECT * FROM branches WHERE id=?',(canonical,)).fetchone())
            main.migrate_official_routes_and_branches(c)
            after=dict(c.execute('SELECT * FROM branches WHERE id=?',(canonical,)).fetchone())
            self.assertEqual(after['code'],'K13');self.assertEqual(after['name'],'士林')
            for key in ('id','pin_hash','access_token_hash','qr_created_at','address','phone'):
                self.assertEqual(after[key],before[key])
            self.assertIsNone(c.execute("SELECT id FROM branches WHERE code='K13S'").fetchone())
            main.migrate_official_routes_and_branches(c)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM branches WHERE code='K13'").fetchone()[0],1)

    def test_postgres_uses_transaction_scoped_shared_lock(self):
        from unittest.mock import MagicMock
        c=MagicMock()
        with patch.object(main,'USE_POSTGRES',True): main.lock_schedule_writes(c)
        c.execute.assert_called_once_with('SELECT pg_advisory_xact_lock(194020)')
        c.commit.assert_not_called()
