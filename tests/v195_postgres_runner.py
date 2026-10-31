"""Real PostgreSQL API regression on a NEW temporary local cluster only.

Usage: python tests/v195_postgres_runner.py --pg-bin /path/to/postgres/bin
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
import test_v195_manual_postponement as cases
from fastapi.testclient import TestClient
from app import main
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool


class PostgreSQLManualTests(cases.ManualPostponementCases,unittest.TestCase):
    socket_dir=None
    baseline_checks=0
    version=None

    def setUp(self):
        name='v195_'+uuid.uuid4().hex
        base=make_conninfo(host=str(self.socket_dir),port=55495,user='v195test',dbname='postgres')
        with psycopg.connect(base,autocommit=True) as admin:
            # This socket directory belongs to the cluster started below. No
            # connection settings are inherited from environment or a file.
            self.assertEqual(admin.execute('SELECT inet_server_addr()').fetchone()[0],None)
            type(self).version=admin.execute('SHOW server_version').fetchone()[0]
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        uri=make_conninfo(host=str(self.socket_dir),port=55495,user='v195test',dbname=name)
        pool=ConnectionPool(uri,min_size=0,max_size=5,kwargs={'row_factory':main.dict_row})
        self.addCleanup(pool.close)
        for field,value in (('USE_POSTGRES',True),('DATABASE_URL',uri),('_PG_POOL',pool)):
            p=patch.object(main,field,value);p.start();self.addCleanup(p.stop)
        main.init_db()
        with self.connection() as c:
            self.assertEqual(c.execute('SELECT current_database() AS name').fetchone()['name'],name)
            tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            self.assertEqual(len(tables),26)
            for row in tables:
                if row['tablename']!='schema_migrations':
                    self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM '+row['tablename']).fetchone()['n'],0)
            type(self).baseline_checks+=1
            c.execute("INSERT INTO users(id,username,role) VALUES(1,'test-admin','ADMIN')")
            c.execute("INSERT INTO drivers(id,name,active) VALUES(1,'合成司機',1)")
            c.execute("INSERT INTO routes(id,code,name,active) VALUES(1,'SYNTHETIC','合成路線',1)")
            for bid in (1,2):
                c.execute("INSERT INTO branches(id,code,name,route_id,active,delivery_weekdays) VALUES(?,?,?,1,1,'5')",
                          (bid,'SYNTHETIC_'+str(bid),'合成分館'+str(bid)))
            c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,1,?)',
                      (main.thash('admin'),'2099-01-01T00:00:00+00:00'))
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.admin={'Authorization':'Bearer admin'}
        self.headers={'Authorization':'Bearer not-a-user'}

    @contextmanager
    def connection(self):
        c=main.db()
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin',required=True,type=Path)
    args=parser.parse_args()
    binary=args.pg_bin.resolve()
    with tempfile.TemporaryDirectory(prefix='mmv195pg-',dir='/tmp') as directory:
        root=Path(directory);data=root/'data';sock=root/'socket';sock.mkdir(mode=0o700)
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-A','trust','-U','v195test','--no-locale','-E','UTF8'],
                       check=True,stdout=subprocess.DEVNULL)
        started=False
        try:
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),
                            '-o',f"-h '' -k {sock} -p 55495",'-w','start'],check=True,stdout=subprocess.DEVNULL)
            started=True;PostgreSQLManualTests.socket_dir=sock
            suite=unittest.defaultTestLoader.loadTestsFromTestCase(PostgreSQLManualTests)
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            report={'engine':'PostgreSQL','version':PostgreSQLManualTests.version,
                    'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
                    'zero_business_baselines':PostgreSQLManualTests.baseline_checks,
                    'target':'new_temporary_local_unix_socket_cluster','production_connections':0,
                    'existing_test_database_connections':0,'persistent_database_created':False,
                    'cases':[name for name in unittest.defaultTestLoader.getTestCaseNames(PostgreSQLManualTests)]}
        finally:
            if started or (data/'postmaster.pid').exists():
                subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
        report['temporary_cluster_stopped']=True
    report['temporary_cluster_removed']=True
    print(json.dumps(report,ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__':raise SystemExit(run())
