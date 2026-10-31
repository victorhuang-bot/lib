"""Offline safety regression. SQLite fixtures only; all sockets blocked.

PostgreSQL dialect/transaction tests below use recording doubles, not a server.
"""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_v194 as fixture
from app import main
from app.schema import BASE_SCHEMA, SchemaTransaction, initialize_schema, validate_schema


class InitializationSafetyTests(unittest.TestCase):
    setUp = fixture.V194Tests.setUp
    connection = fixture.V194Tests.connection

    def snapshot(self):
        with self.connection() as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {t: sorted([tuple(r) for r in c.execute('SELECT * FROM '+t)], key=repr) for t in tables}

    def history(self):
        with self.connection() as c:
            c.execute("INSERT INTO deliveries(id,service_date,branch_id,status,document_original,document_final,outbound_final,inbound_final,signer_name,branch_signature,branch_signed_at,receipt_at,correction_signer_name,correction_signature,corrected_at,driver_confirmed_at) VALUES(990123,?,1,'STOP_COMPLETED',5,7,8,9,'SYNTHETIC-NAME','SYNTHETIC-SIGNATURE','2030-01-01T10:00:00+08:00','2030-01-01T10:00:00+08:00','SYNTHETIC-CORRECTION','SYNTHETIC-CORRECTION-SIGNATURE','2030-01-01T11:00:00+08:00','2030-01-01T12:00:00+08:00')", (main.today(),))
            c.execute("INSERT INTO corrections(delivery_id,status) VALUES(990123,'RESOLVED')")
            c.execute("INSERT INTO branch_sessions(token_hash,branch_id,delivery_id,expires_at) VALUES('synthetic-history-session',1,990123,'2099-01-01')")

    def test_production_restart_preserves_every_existing_row_without_old_markers(self):
        # Missing legacy markers must never grant permission to mutate data.
        with self.connection() as c:
            c.execute("DELETE FROM app_settings WHERE key IN ('V4_THREE_BRANCH_TEST_RESET','DEMO_BRANCH_LIMIT_INITIALIZED_V5','OFFICIAL_ROSTER_V183_PG','FORCE_TODAY_DEFAULT_DRIVERS_V1834')")
        self.history()
        before = self.snapshot()
        with patch.object(main, 'APP_ENV', 'production'):
            for _ in range(3):
                main.validate_startup_schema()
        self.assertEqual(before, self.snapshot())

    def test_startup_schema_check_executes_selects_only(self):
        statements=[]
        def connect():
            c=self.connect()
            c.set_trace_callback(statements.append)
            return c
        with patch.object(main,'db',side_effect=connect), patch.object(main,'APP_ENV','production'):
            main.validate_startup_schema()
        self.assertTrue(statements)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in statements))

    def test_repeated_schema_initialization_preserves_history_and_manual_settings(self):
        self.history()
        with self.connection() as c:
            c.execute("UPDATE branches SET name='SYNTHETIC-MANUAL',route_id=6,active=0 WHERE id=1")
            c.execute("UPDATE drivers SET active=0")
        before = self.snapshot()
        for _ in range(3): main.init_db()
        self.assertEqual(before, self.snapshot())

    def test_signed_identity_signature_and_times_survive_day_rebuild(self):
        self.history()
        before = self.snapshot()
        with self.connection() as c:
            c.execute('UPDATE branches SET active=0 WHERE id=1')
        # A no-longer-scheduled signed delivery must remain byte-for-byte intact.
        with self.connection() as c:
            original = tuple(c.execute('SELECT * FROM deliveries WHERE id=990123').fetchone())
            for _ in range(3): main.ensure_today(c)
            self.assertEqual(original, tuple(c.execute('SELECT * FROM deliveries WHERE id=990123').fetchone()))
        self.assertEqual(before['corrections'], self.snapshot()['corrections'])
        self.assertEqual(before['branch_sessions'], self.snapshot()['branch_sessions'])

    def test_day_builder_preserves_prefilled_completed_void_and_correction_data(self):
        with self.connection() as c:
            main.ensure_today(c)
            c.execute("UPDATE deliveries SET status='STOP_COMPLETED',document_final=19,branch_signature='SYNTHETIC',branch_signed_at='2030-01-01',driver_confirmed_at='2030-01-01' WHERE branch_id=1")
            c.execute("UPDATE deliveries SET document_final=23,correction_signature='SYNTHETIC-CORRECTION' WHERE branch_id=2")
            c.execute('UPDATE branches SET active=0')
        before = self.snapshot()
        with self.connection() as c:
            for _ in range(3): main.ensure_today(c)
        self.assertEqual(before, self.snapshot())

    def test_all_reset_calls_rejected_in_production_even_with_flags(self):
        before = self.snapshot()
        with patch.object(main, 'APP_ENV', 'production'), patch.dict(os.environ, {'ALLOW_TEST_RESET':'true','APP_ENV':'test','CONFIRM_RESET':'movingmatch_v194_test'}):
            for fn in (main.reset_test_day_once, main.apply_demo_branch_limit):
                with self.assertRaises(RuntimeError): fn(Mock())
            with self.assertRaises(RuntimeError): main.seed_demo_data()
        self.assertEqual(before, self.snapshot())

    def test_reset_is_also_disabled_in_test_mode(self):
        with patch.object(main, 'APP_ENV', 'test'):
            for fn in (main.reset_test_day_once, main.apply_demo_branch_limit):
                with self.assertRaises(RuntimeError): fn(Mock())

    def test_empty_sqlite_schema_has_no_business_data(self):
        path = Path(self.tmp.name) / 'empty.db'
        def connect():
            c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return c
        with patch.object(main, 'db', side_effect=connect): main.init_db()
        with closing(connect()) as c:
            validate_schema(c)
            for table in ('users','branches','drivers','routes','deliveries','audit_logs','branch_signers'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],1)

    def test_v194_migration_repeated_on_existing_schema_preserves_data(self):
        with self.connection() as c:
            c.execute("INSERT INTO branch_signers(branch_id,name,created_at,updated_at) VALUES(1,'SYNTHETIC-PERSON','t','t')")
        before = self.snapshot()
        for _ in range(2): main.init_db()
        self.assertEqual(before,self.snapshot())

    def test_old_sqlite_schema_upgrades_without_replacing_delivery_rows(self):
        with closing(sqlite3.connect(':memory:')) as c:
            c.row_factory=sqlite3.Row
            old=BASE_SCHEMA
            for column in ('document_return_final INTEGER,','receipt_at TEXT,','voided_at TEXT,','voided_by INTEGER,','void_reason TEXT,','admin_closed_at TEXT,','admin_closed_by INTEGER,','admin_close_reason TEXT,'):
                old=old.replace(column,'')
            SchemaTransaction(c,False).executescript(old)
            c.execute("INSERT INTO deliveries(id,status,signer_name,branch_signature,branch_signed_at) VALUES(1,'STOP_COMPLETED','SYNTHETIC','SYNTHETIC','2000-01-01')")
            c.commit()
            migrations=(main.migrate_branch_signers,main.migrate_corrections_for_repeat_requests,
                        main.migrate_email_logs_gmail_api,main.migrate_route_secretary_signatures,
                        main.migrate_document_return_final,main.migrate_v1929)
            initialize_schema(c,False,migrations)
            validate_schema(c)
            row=c.execute('SELECT status,signer_name,branch_signature,branch_signed_at FROM deliveries WHERE id=1').fetchone()
            self.assertEqual(tuple(row),('STOP_COMPLETED','SYNTHETIC','SYNTHETIC','2000-01-01'))

    def test_closure_stop_add_and_weekdays_remain_idempotent(self):
        with self.connection() as c:
            c.execute('UPDATE branches SET active=0')
            c.execute("UPDATE branches SET active=1,route_id=1,delivery_weekdays='1' WHERE id IN (1,2)")
            c.execute("INSERT INTO global_closures(service_date) VALUES('2031-01-06')")
            c.execute("INSERT INTO delivery_exceptions(service_date,branch_id,exception_type) VALUES('2031-01-13',1,'STOP')")
            c.execute("INSERT INTO delivery_exceptions(service_date,branch_id,exception_type) VALUES('2031-01-07',1,'ADD')")
            for day in ('2031-01-06','2031-01-13','2031-01-07'):
                for _ in range(2): main.rebuild_service_date(c,day)
            rows=[tuple(r) for r in c.execute("SELECT service_date,branch_id FROM deliveries WHERE service_date BETWEEN '2031-01-06' AND '2031-01-13' ORDER BY 1,2")]
            self.assertEqual(rows,[('2031-01-07',1),('2031-01-13',2)])

    def test_failure_rolls_back_ddl_and_migration_record_on_empty_database(self):
        c=sqlite3.connect(':memory:')
        self.addCleanup(c.close)
        def fail(tx):
            tx.execute('CREATE TABLE synthetic_partial(id INTEGER)')
            tx.execute('INSERT INTO synthetic_partial VALUES(1)')
            tx.commit()  # Legacy helper must not escape the transaction.
            raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError): initialize_schema(c,False,[fail])
        self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[])

    def test_failure_rolls_back_changes_to_existing_database(self):
        before=self.snapshot()
        with self.connection() as c:
            def fail(tx):
                tx.execute("UPDATE branches SET name='SYNTHETIC-FAILURE'")
                tx.commit()
                raise RuntimeError('synthetic failure')
            with self.assertRaises(RuntimeError): initialize_schema(c,False,[fail])
        self.assertEqual(before,self.snapshot())

    def test_k13_variants_are_not_modified_by_startup_or_schema_initialization(self):
        for codes in ((),('K13',),('K13S',),('K13','K13S')):
            with self.subTest(codes=codes):
                with self.connection() as c:
                    c.execute("UPDATE branches SET code='SYNTHETIC-'||id WHERE code IN ('K13','K13S')")
                    for code in codes:
                        c.execute('INSERT INTO branches(code,name,active) VALUES(?,?,1)',(code,'SYNTHETIC-IDENTITY'))
                        bid=c.execute('SELECT id FROM branches WHERE code=?',(code,)).fetchone()[0]
                        c.execute("INSERT INTO deliveries(service_date,branch_id,status,signer_name,branch_signature) VALUES('2000-01-01',?,'STOP_COMPLETED','SYNTHETIC','SYNTHETIC')",(bid,))
                before=self.snapshot()
                main.validate_startup_schema(); main.init_db()
                self.assertEqual(before,self.snapshot())

    def test_locked_report_and_signed_route_do_not_gain_deliveries(self):
        with self.connection() as c:
            c.execute('UPDATE branches SET active=0')
            c.execute("UPDATE branches SET active=1,route_id=1,delivery_weekdays='1,2,3,4,5' WHERE id=1")
        for route_field in ('driver_signed_at','driver_signature','secretary_signed_at','secretary_signature'):
            day='2031-01-06'
            with self.connection() as c:
                c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,1,1) ON CONFLICT(service_date,route_id) DO NOTHING',(day,))
                c.execute('UPDATE daily_routes SET '+route_field+'=? WHERE service_date=?',('SYNTHETIC',day))
                main.rebuild_service_date(c,day)
                self.assertEqual(c.execute('SELECT count(*) FROM deliveries WHERE service_date=?',(day,)).fetchone()[0],0)
        with self.connection() as c:
            c.execute("INSERT INTO daily_reports(service_date,status) VALUES('2031-01-07','LOCKED')")
        before=self.snapshot()
        with self.connection() as c: main.rebuild_service_date(c,'2031-01-07')
        self.assertEqual(before,self.snapshot())

    def test_four_roles_today_endpoints_and_startup_compatibility(self):
        with self.connection() as c:
            main.ensure_today(c)
            c.execute("INSERT INTO users(username,role) VALUES('SYNTHETIC-SECRETARY','SECRETARY')")
            uid=c.execute("SELECT id FROM users WHERE username='SYNTHETIC-SECRETARY'").fetchone()[0]
            c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(main.thash('synthetic-secretary'),uid,main.future_iso(hours=1)))
            did=c.execute('SELECT id FROM drivers LIMIT 1').fetchone()[0]
            c.execute('UPDATE daily_routes SET driver_id=? WHERE service_date=?',(did,main.today()))
            c.execute('INSERT INTO driver_sessions(token_hash,driver_id,expires_at) VALUES(?,?,?)',(main.thash('synthetic-driver'),did,main.future_iso(hours=1)))
        from fastapi.testclient import TestClient
        with TestClient(main.app) as client:
            for token,endpoint in (('admin','/api/dashboard/today'),('synthetic-secretary','/api/dashboard/today'),('synthetic-driver','/api/driver/today'),('branch-one','/api/branch-session/today')):
                result=client.get(endpoint,headers={'Authorization':'Bearer '+token})
                self.assertEqual(result.status_code,200,(endpoint,result.text[:200]))


class SchemaProcessAndDialectTests(unittest.TestCase):
    def test_two_independent_processes_initialize_same_local_database(self):
        with tempfile.TemporaryDirectory(prefix='movingmatch-schema-process-') as temp:
            path=Path(temp)/'app.db'
            command=[sys.executable,'-m','app.schema_init','--sqlite',str(path)]
            # Local SQLite fixtures use test mode; production must require PG.
            # Never forward credentials to test subprocesses.
            env={'PATH':os.environ.get('PATH',''),'APP_ENV':'test','PYTHONDONTWRITEBYTECODE':'1'}
            processes=[subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
            try:
                for process in processes:
                    stdout,stderr=process.communicate(timeout=30)
                    self.assertEqual(process.returncode,0,stderr)
                    self.assertIn('PASS:',stdout)
            finally:
                for process in processes:
                    if process.poll() is None: process.kill(); process.wait()
            with closing(sqlite3.connect(path)) as c:
                validate_schema(c)
                self.assertEqual(c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)

    def test_import_does_not_create_database_or_connect(self):
        with tempfile.TemporaryDirectory(prefix='movingmatch-import-') as temp:
            code="import socket; socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('network forbidden')); from app import main"
            env={'PATH':os.environ.get('PATH',''),'DATA_DIR':temp,'APP_ENV':'production','DATABASE_URL':'postgresql://invalid.invalid/never_connect','PYTHONDONTWRITEBYTECODE':'1'}
            result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((Path(temp)/'app.db').exists())

    def test_postgres_dialect_identity_and_transaction_lock_without_server(self):
        connection=Mock()
        with patch('app.schema.validate_schema'), patch.object(main,'USE_POSTGRES',True):
            initialize_schema(connection,True,[main.migrate_branch_signers])
        sql=[call.args[0] for call in connection.execute.call_args_list]
        self.assertIn('SELECT pg_advisory_xact_lock(194000)',sql)
        self.assertTrue(any('GENERATED BY DEFAULT AS IDENTITY' in statement for statement in sql))
        self.assertFalse(any('id INTEGER PRIMARY KEY' in statement for statement in sql))
        self.assertFalse(any(statement.startswith(('DELETE','UPDATE','TRUNCATE','SELECT setval')) for statement in sql))
        connection.commit.assert_called_once()
        connection.rollback.assert_not_called()

    def test_postgres_failure_requests_rollback_without_commit_or_server(self):
        connection=Mock()
        def fail(tx):
            tx.commit()
            raise RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError): initialize_schema(connection,True,[fail])
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
