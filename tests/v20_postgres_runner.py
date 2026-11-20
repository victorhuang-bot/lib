"""Real PostgreSQL API regression on a NEW temporary local cluster only.

Usage: python tests/v20_postgres_runner.py --pg-bin /path/to/postgres/bin
Never accepts DATABASE_URL or remote hosts. Requires PostgreSQL binaries and
the project's normal dev dependencies. Stops its own server before removing
its newly created temporary directory; never connects to existing instances.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
# app.main must import only against fixture-owned temporary SQLite first.
for key in ('DATABASE_URL','RENDER','RENDER_SERVICE_ID'):
    os.environ.pop(key,None)
for key in list(os.environ):
    if key.startswith('PG'):
        os.environ.pop(key,None)
os.environ['APP_ENV']='test'
import test_v20_empty_boxes as cases
from test_branch_document_correction import DocumentCorrectionCases
from fastapi.testclient import TestClient
from app import main
import psycopg
from v20_schema_checks import assert_postgres_schema
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool


class PostgreSQLEmptyBoxTests(cases.EmptyBoxCases,unittest.TestCase):
    cluster_data=None
    socket_dir=None
    baseline_checks=0
    version=None

    def test_uat_initialization_is_atomic_additive_and_preserves_results(self):
        from app import uat_init
        from app.uat_serve import verify
        from types import SimpleNamespace
        from psycopg import sql
        name='uat_'+uuid.uuid4().hex
        base=make_conninfo(host=str(self.socket_dir),port=55495,user='v20test',dbname='postgres')
        with psycopg.connect(base,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        uri=make_conninfo(host=str(self.socket_dir),port=55495,user='v20test',dbname=name)
        with psycopg.connect(uri,row_factory=main.dict_row) as raw, \
             patch.object(uat_init,'DATABASE',name), patch.object(uat_init,'ROLE','v20test'):
            # This connection is the newly created socket-only local test DB.
            # Only the TLS attribute is doubled; remote guard is never bypassed by CLI.
            conn=SimpleNamespace(execute=raw.execute,rollback=raw.rollback,
                                 pgconn=SimpleNamespace(ssl_in_use=True))
            with patch('app.v20_schema_checks.assert_postgres_schema',side_effect=RuntimeError('synthetic validation failure')):
                with self.assertRaises(RuntimeError):uat_init.initialize(conn,{})
            raw.rollback()
            self.assertEqual(raw.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall(),[])
            credentials={}
            report=uat_init.initialize(conn,credentials)
            self.assertEqual(len(report['baseline_tables']),26)
            self.assertEqual(len(report['v20_tables']),9)
            self.assertEqual(len(credentials),8)
            self.assertEqual(len(set(credentials.values())),8)
            self.assertTrue(all(len(p)>=32 for p in credentials.values()))
            verify(conn)
            raw.commit()
            branch=raw.execute("SELECT id FROM branches WHERE code='UAT-B1'").fetchone()['id']
            raw.execute("INSERT INTO empty_box_requests(id,branch_id,quantity,status,created_at,actor,note) VALUES('uat-result',%s,10,'OPEN','2030-01-01','合成驗收人','保留結果')",(branch,))
            raw.execute("UPDATE branches SET name='合成人工修改' WHERE id=%s",(branch,))
            raw.commit()
            tables=report['baseline_tables']+report['v20_tables']
            def snapshot():
                return {t:sorted((repr(r) for r in raw.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(t))).fetchall())) for t in tables}
            before=snapshot()
            for _ in range(2):
                extra={}
                uat_init.initialize(conn,extra)
                self.assertEqual(extra,{})
                self.assertEqual(snapshot(),before)
                raw.commit()
            raw.execute('DELETE FROM app_settings WHERE key=%s',(uat_init.MARKER,))
            with self.assertRaises(ValueError):uat_init.initialize(conn,{})
            raw.rollback()
            verify(conn)
            # Schema validation rejects an incorrect FK rather than accepting just names.
            raw.execute('ALTER TABLE empty_box_movements DROP CONSTRAINT empty_box_movements_branch_id_fkey')
            with self.assertRaises(AssertionError):verify(conn)
            raw.rollback()
            self.assertEqual(snapshot(),before)

    def setUp(self):
        name='v20_'+uuid.uuid4().hex
        base=make_conninfo(host=str(self.socket_dir),port=55495,user='v20test',dbname='postgres')
        with psycopg.connect(base,autocommit=True) as admin:
            # This socket directory belongs to the cluster started below. No
            # connection settings are inherited from environment or a file.
            self.assertEqual(admin.execute('SELECT inet_server_addr()').fetchone()[0],None)
            self.assertEqual(Path(admin.execute('SHOW data_directory').fetchone()[0]).resolve(),self.cluster_data.resolve())
            self.assertEqual(admin.execute('SHOW listen_addresses').fetchone()[0],'')
            type(self).version=admin.execute('SHOW server_version').fetchone()[0]
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        uri=make_conninfo(host=str(self.socket_dir),port=55495,user='v20test',dbname=name)
        pool=ConnectionPool(uri,min_size=0,max_size=5,kwargs={'row_factory':main.dict_row})
        self.addCleanup(pool.close)
        for field,value in (('USE_POSTGRES',True),('DATABASE_URL',uri),('_PG_POOL',pool)):
            p=patch.object(main,field,value);p.start();self.addCleanup(p.stop)
        main.init_db()
        with self.connection() as c:
            self.assertEqual(c.execute('SELECT current_database() AS name').fetchone()['name'],name)
            tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            assert_postgres_schema(self,c)
            for row in tables:
                if row['tablename']!='schema_migrations':
                    self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM '+row['tablename']).fetchone()['n'],0)
            type(self).baseline_checks+=1
            c.execute("INSERT INTO users(id,username,role) VALUES(1,'test-admin','ADMIN')")
            c.execute("INSERT INTO drivers(id,name,active) VALUES(1,'合成司機',1)")
            c.execute("INSERT INTO routes(id,code,name,active) VALUES(1,'SYNTHETIC','合成路線',1)")
            c.execute("INSERT INTO routes(id,code,name,active) VALUES(2,'SECOND','合成次路線',1)")
            for bid in (1,2,3):
                c.execute("INSERT INTO branches(id,code,name,route_id,active,delivery_weekdays) VALUES(?,?,?,1,1,'5')",
                          (bid,'SYNTHETIC_'+str(bid),'合成分館'+str(bid)))
            c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,1,?)',
                      (main.thash('admin'),'2099-01-01T00:00:00+00:00'))
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.admin={'Authorization':'Bearer admin'}
        self.headers={'Authorization':'Bearer branch-one'}
        self.other={'Authorization':'Bearer branch-two'}
        with self.connection() as c:
            for bid,token in ((1,'branch-one'),(2,'branch-two')):
                c.execute('INSERT INTO branch_sessions(token_hash,branch_id,expires_at) VALUES(?,?,?)',(main.thash(token),bid,'2099-01-01T00:00:00+00:00'))
            # Explicit fixture IDs do not advance PG sequences.
            for table in ('users','routes','branches','drivers'):
                c.execute("SELECT setval(pg_get_serial_sequence(?, 'id'), (SELECT MAX(id) FROM "+table+"))",(table,))

    @contextmanager
    def connection(self):
        c=main.db()
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()


class PostgreSQLDocumentCorrectionTests(DocumentCorrectionCases, unittest.TestCase):
    setUp = PostgreSQLEmptyBoxTests.setUp
    connection = PostgreSQLEmptyBoxTests.connection
    baseline_checks = 0
    version = None


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',required=True,type=Path)
    args=parser.parse_args()
    binary=args.pg_bin.resolve()
    with tempfile.TemporaryDirectory(prefix='mmv20pg-',dir='/tmp') as directory:
        root=Path(directory);data=root/'data';sock=root/'socket';sock.mkdir(mode=0o700)
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-A','trust','-U','v20test','--no-locale','-E','UTF8'],
                       check=True,stdout=subprocess.DEVNULL)
        started=False
        try:
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),
                            '-o',f"-h '' -k {sock} -p 55495",'-w','start'],check=True,stdout=subprocess.DEVNULL)
            started=True
            case_types=(PostgreSQLEmptyBoxTests,PostgreSQLDocumentCorrectionTests)
            suite=unittest.TestSuite()
            for case_type in case_types:
                case_type.socket_dir=sock
                case_type.cluster_data=data
                suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case_type))
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            report={'engine':'PostgreSQL','version':PostgreSQLEmptyBoxTests.version,
                    'tests_run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
                    'zero_business_baselines':sum(case.baseline_checks for case in case_types),
                    'target':'new_temporary_local_unix_socket_cluster','production_connections':0,
                    'existing_test_database_connections':0,'persistent_database_created':False,
                    'cases':[name for case in case_types for name in unittest.defaultTestLoader.getTestCaseNames(case)]}
        finally:
            if started or (data/'postmaster.pid').exists():
                subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
        report['temporary_cluster_stopped']=True
    report['temporary_cluster_removed']=True
    print(json.dumps(report,ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(run())
