"""Production fail-closed probes, each in a fresh process with synthetic config.

SQLite, sockets, seed and initialization must never run in production probes.
Only the separate local SQLite fixture tests may open temporary SQLite files.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent
MARKERS={'production':{'APP_ENV':'production'},'normalized':{'APP_ENV':' Production '},
         'prod_alias':{'APP_ENV':'prod'},'render':{'RENDER':'true'},
         'render_service':{'RENDER_SERVICE_ID':'SYNTHETIC_SERVICE'}}
CASES=[]
for marker,config in MARKERS.items():
    for missing,url in (('missing',None),('empty',''),('blank','   ')):
        CASES.append({'name':'missing_url_'+marker+'_'+missing,'kind':'missing_url','marker':config,'url':url,'expected_pg':0,'import_reject':True})
for marker in ('production','render'):
    for label,drivers in (('psycopg',['psycopg']),('pool',['psycopg_pool']),('both',['psycopg','psycopg_pool'])):
        CASES.append({'name':'missing_driver_'+marker+'_'+label,'kind':'driver','marker':MARKERS[marker],'missing_drivers':drivers,'expected_pg':0,'import_reject':True})
for entry in ('db','startup'):
    CASES.append({'name':'connection_failure_'+entry,'kind':'failure','marker':MARKERS['production'],'entry':entry,'expected_pg':3})
for name in ('psycopg','ConnectionPool'):
    CASES.append({'name':'runtime_driver_loss_'+name,'kind':'driver','marker':MARKERS['production'],'runtime_missing':name,'expected_pg':0})
CASES.append({'name':'configured_postgres_selected','kind':'configured','marker':MARKERS['production'],'expected_pg':1})
CASES.append({'name':'invalid_nonempty_url_no_sqlite','kind':'failure','marker':MARKERS['production'],'url':'sqlite:///SYNTHETIC_INVALID_CONFIG','expected_pg':3})
CASES.append({'name':'pool_logging_redacts_driver_details','kind':'logging','marker':MARKERS['production'],'expected_pg':0})


class ProductionDatabaseSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checkout=ROOT

    def probe(self,cfg):
        with tempfile.TemporaryDirectory(prefix='v194-production-probe-') as directory:
            env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1',
                 'PYTHONPYCACHEPREFIX':str(Path(directory)/'cache'),'PYTHONPATH':str(self.checkout),
                 'MOVINGMATCH_PROBE_CHECKOUT':str(self.checkout),'DATA_DIR':directory,'APP_ENV':'development'}
            env.update(cfg['marker'])
            url=cfg.get('url','postgresql://synthetic:synthetic@invalid.invalid/synthetic_production')
            if url is not None:env['DATABASE_URL']=url
            result=subprocess.run([sys.executable,'-B',str(ROOT/'tests/production_database_worker.py'),json.dumps(cfg)],
                cwd=self.checkout,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,'PROBE_PROCESS_FAILED; stderr suppressed to avoid connection details')
            data=json.loads(result.stdout)
            for field in ('sqlite_calls','network_calls','seed_calls','init_calls'):
                self.assertEqual(data[field],0,field+' must remain zero')
            self.assertFalse(data['db_file_created'])
            self.assertEqual(data['pg_attempts'],cfg['expected_pg'])
            self.assertEqual(data['rejected'],cfg['kind'] not in ('configured','logging'))
            if cfg.get('import_reject'):self.assertTrue(data['import_rejected'])
            if cfg['kind']=='configured':self.assertTrue(data['selected_pg_object'])
            if cfg['kind']=='logging':
                self.assertTrue(data['pool_log_redacted'])
                self.assertEqual(data['pool_log_records'],3)
            if cfg['kind']=='failure':
                self.assertTrue(data['public_error_is_fixed'], 'Public error must be a fixed safe message')
                self.assertFalse(data['error_contains_sensitive_detail'], 'Exception must redact injected connection details')
                self.assertFalse(data['traceback_contains_sensitive_detail'], 'Formatted traceback must redact connection details')
                self.assertTrue(data['exception_context_suppressed'])
            destination=os.environ.get('MOVINGMATCH_PROBE_RESULTS')
            if destination:
                with Path(destination).open('a') as stream:stream.write(json.dumps(data)+'\n')


def factory(cfg):
    def test(self):self.probe(cfg)
    return test
for cfg in CASES:setattr(ProductionDatabaseSafetyTests,'test_'+cfg['name'],factory(cfg))


if __name__=='__main__':unittest.main()
