"""Secretary signature immutability and atomicity on isolated SQLite fixtures."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import unittest

import test_v194 as fixture
from app import main


class SecretarySigningSafetyTests(unittest.TestCase):
    setUp = fixture.V194Tests.setUp
    connection = fixture.V194Tests.connection

    def prepare(self):
        self.day = '2030-01-04'
        self.stamp = main.now()
        clock = patch.object(main, 'today', return_value=self.day)
        clock.start(); self.addCleanup(clock.stop)
        self.secretaries = []
        with self.connection() as c:
            for number in (1, 2):
                token = 'synthetic-secretary-'+str(number)
                cur = c.execute('INSERT INTO users(username,role) VALUES(?,?)', (token, 'SECRETARY'))
                uid = cur.lastrowid
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                          (main.thash(token), uid, main.future_iso(hours=1)))
                self.secretaries.append((uid, {'Authorization':'Bearer '+token}))
            c.execute("INSERT INTO daily_routes(id,service_date,route_id,driver_id,status) VALUES(990301,?,1,1,'DRIVER_SIGNED')", (self.day,))
            c.execute("INSERT INTO deliveries(id,service_date,daily_route_id,branch_id,status,document_final,signer_name,branch_signature,branch_signed_at) VALUES(990301,?,990301,1,'STOP_COMPLETED',7,'SYNTHETIC-BRANCH','SYNTHETIC-INK',?)", (self.day,self.stamp))

    def sign(self, signature='SYNTHETIC-SECRETARY-A', actor=0):
        return self.client.post('/api/routes/990301/secretary-sign',
                                headers=self.secretaries[actor][1], json={'signature':signature})

    def state(self):
        with self.connection() as c:
            return {
                table:[tuple(r) for r in c.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('daily_routes','deliveries','daily_reports','audit_logs')
            }

    def test_first_sign_records_one_signature_time_and_actor(self):
        self.prepare()
        with patch.object(main,'now',return_value=self.stamp): result=self.sign()
        self.assertEqual(result.status_code,200,result.text)
        with self.connection() as c:
            route=c.execute('SELECT * FROM daily_routes WHERE id=990301').fetchone()
            self.assertEqual(route['secretary_signature'],'SYNTHETIC-SECRETARY-A')
            self.assertEqual(route['secretary_signed_at'],self.stamp)
            audit=c.execute("SELECT actor_id,created_at FROM audit_logs WHERE action='SECRETARY_ROUTE_SIGN' AND entity_id='990301'").fetchall()
            self.assertEqual([tuple(r) for r in audit],[(str(self.secretaries[0][0]),self.stamp)])

    def test_identical_retry_preserves_all_rows_actor_and_time_without_publish(self):
        self.prepare(); self.assertEqual(self.sign().status_code,200)
        before=self.state()
        later=(datetime.fromisoformat(self.stamp)+timedelta(seconds=2)).isoformat()
        with patch.object(main,'now',return_value=later), patch.object(main,'publish',new_callable=AsyncMock) as publish:
            result=self.sign(actor=1)
            self.assertEqual(result.status_code,200,result.text)
            self.assertTrue(result.json()['already_signed'])
            publish.assert_not_awaited()
        self.assertEqual(before,self.state())

    def test_different_retry_is_rejected_without_overwrite(self):
        self.prepare(); self.assertEqual(self.sign().status_code,200)
        before=self.state(); self.assertEqual(self.sign('SYNTHETIC-OTHER',1).status_code,409)
        self.assertEqual(before,self.state())

    def test_locked_service_day_rejects_identical_and_different_retries(self):
        self.prepare(); self.sign()
        with self.connection() as c:
            c.execute("INSERT INTO daily_reports(service_date,status,locked_at) VALUES(?,'LOCKED',?)",(self.day,self.stamp))
        before=self.state()
        for ink in ('SYNTHETIC-SECRETARY-A','SYNTHETIC-OTHER'):
            self.assertEqual(self.sign(ink).status_code,409)
        self.assertEqual(before,self.state())

    def test_lock_uses_route_service_date_not_today(self):
        self.prepare()
        with self.connection() as c:
            c.execute("INSERT INTO daily_reports(service_date,status) VALUES(?,'LOCKED')",(self.day,))
        with patch.object(main,'today',return_value='2030-01-05'):
            self.assertEqual(self.sign().status_code,409)

    def test_concurrent_different_signatures_have_one_winner_and_one_audit(self):
        self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda i:self.sign('SYNTHETIC-'+str(i),i),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        winner=next(i for i,r in enumerate(results) if r.status_code==200)
        with self.connection() as c:
            self.assertEqual(c.execute('SELECT secretary_signature FROM daily_routes WHERE id=990301').fetchone()[0],'SYNTHETIC-'+str(winner))
            rows=c.execute("SELECT actor_id FROM audit_logs WHERE action='SECRETARY_ROUTE_SIGN' AND entity_id='990301'").fetchall()
            self.assertEqual([r[0] for r in rows],[str(self.secretaries[winner][0])])

    def test_concurrent_identical_signatures_do_not_duplicate_audit(self):
        self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:self.sign(),range(2)))
        self.assertEqual([r.status_code for r in results],[200,200])
        with self.connection() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM audit_logs WHERE action='SECRETARY_ROUTE_SIGN' AND entity_id='990301'").fetchone()[0],1)

    def test_other_roles_and_unauthenticated_requests_are_denied(self):
        self.prepare(); before=self.state()
        for headers,expected in ((self.admin,403),(self.headers,401),({},401)):
            result=self.client.post('/api/routes/990301/secretary-sign',headers=headers,json={'signature':'SYNTHETIC'})
            self.assertEqual(result.status_code,expected)
        self.assertEqual(before,self.state())

    def test_incomplete_legacy_signature_is_not_repaired_or_overwritten(self):
        self.prepare()
        for signature,stamp in (('SYNTHETIC',None),(None,self.stamp),('',self.stamp)):
            with self.connection() as c:
                c.execute('UPDATE daily_routes SET secretary_signature=?,secretary_signed_at=? WHERE id=990301',(signature,stamp))
            before=self.state(); self.assertEqual(self.sign().status_code,409); self.assertEqual(before,self.state())

    def test_audit_failure_rolls_back_signature(self):
        self.prepare(); before=self.state()
        with patch.object(main,'audit',side_effect=RuntimeError('synthetic audit fault')):
            with self.assertRaises(RuntimeError): self.sign()
        self.assertEqual(before,self.state())

    def test_final_report_first_lock_then_retries_cannot_overwrite(self):
        self.prepare(); self.sign()
        request=lambda ink:self.client.post('/api/secretary/final-sign',headers=self.secretaries[0][1],json={'signature':ink})
        self.assertEqual(request('SYNTHETIC-FINAL').status_code,200)
        before=self.state()
        self.assertEqual(request('SYNTHETIC-FINAL').status_code,409)
        self.assertEqual(request('SYNTHETIC-REPLACE').status_code,409)
        self.assertEqual(self.sign().status_code,409)
        self.assertEqual(before,self.state())

    def test_final_report_legacy_signature_is_preserved(self):
        self.prepare(); self.sign()
        with self.connection() as c:
            c.execute("INSERT INTO daily_reports(service_date,status,secretary_signature) VALUES(?,'OPEN','SYNTHETIC-LEGACY')",(self.day,))
        before=self.state()
        result=self.client.post('/api/secretary/final-sign',headers=self.secretaries[0][1],json={'signature':'SYNTHETIC-NEW'})
        self.assertEqual(result.status_code,409); self.assertEqual(before,self.state())

    def test_concurrent_final_signatures_have_one_original_actor(self):
        self.prepare(); self.sign()
        def submit(index):
            return self.client.post('/api/secretary/final-sign',headers=self.secretaries[index][1],json={'signature':'SYNTHETIC-FINAL-'+str(index)})
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit,range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        winner=next(i for i,r in enumerate(results) if r.status_code==200)
        with self.connection() as c:
            report=c.execute('SELECT * FROM daily_reports WHERE service_date=?',(self.day,)).fetchone()
            self.assertEqual(report['secretary_signature'],'SYNTHETIC-FINAL-'+str(winner))
            audits=c.execute("SELECT actor_id FROM audit_logs WHERE action='SECRETARY_FINAL_SIGN' AND entity_id=?",(self.day,)).fetchall()
            self.assertEqual([r[0] for r in audits],[str(self.secretaries[winner][0])])

    def test_final_audit_failure_rolls_back_report_lock(self):
        self.prepare(); self.sign(); before=self.state()
        with patch.object(main,'audit',side_effect=RuntimeError('synthetic final audit fault')):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/secretary/final-sign',headers=self.secretaries[0][1],json={'signature':'SYNTHETIC-FINAL'})
        self.assertEqual(before,self.state())

    def test_unsigned_driver_or_missing_quantity_cannot_sign(self):
        self.prepare()
        with self.connection() as c:c.execute("UPDATE daily_routes SET status='ACTIVE' WHERE id=990301")
        before=self.state(); self.assertEqual(self.sign().status_code,409); self.assertEqual(before,self.state())
        with self.connection() as c:
            c.execute("UPDATE daily_routes SET status='DRIVER_SIGNED' WHERE id=990301")
            c.execute('UPDATE deliveries SET document_final=NULL WHERE id=990301')
        before=self.state(); self.assertEqual(self.sign().status_code,409); self.assertEqual(before,self.state())


if __name__=='__main__': unittest.main()
