"""Offline doubles only. Never connects PostgreSQL or executes a real cleanup."""
import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import re
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import psycopg
from tools import v194_pg_security as security
from tools.v194_pg_ownership import Ledger, ScopedRestorer, ORDER, DIGEST
from tools.v194_pg_tracking import BufferedCursor, TrackedAdapter, restrict_existing
from tools import v194_pg_stage2 as runner


def render(statement):return statement if isinstance(statement,str) else statement.as_string()


class FakeDatabase:
    """Python dictionaries only. SQL strings are recorded, never sent anywhere."""
    def __init__(self):
        self.data={t:[] for t in ORDER};self.statements=[];self.commits=0;self.rollbacks=0
        self.info=SimpleNamespace(backend_pid=42)
        self.bad_delete=False
    def execute(self, statement, params=()):
        text=render(statement);self.statements.append((text,params))
        if text.startswith('SELECT version'):return BufferedCursor([{'version':'v194-safe-schema-1'}],1)
        if 'SELECT count(*) AS n' in text:return BufferedCursor([{'n':0}],1)
        selected=re.search(r'FROM public\."?([a-z_]+)"? t',text)
        if text.startswith('SELECT') and selected:
            rows=copy.deepcopy(self.data[selected[1]])
            return BufferedCursor(rows,len(rows))
        deleted=re.search(r'DELETE FROM public\."?([a-z_]+)"? AS t WHERE',text)
        if deleted:
            if self.bad_delete:return BufferedCursor([],0)
            table=deleted[1];column='key' if table=='app_settings' else ('token_hash' if table.endswith('sessions') else 'id')
            matches=[r for r in self.data[table] if r[column]==params[0] and r[DIGEST]==params[1]]
            self.data[table]=[r for r in self.data[table] if r not in matches]
            return BufferedCursor(matches,len(matches))
        return BufferedCursor([],0)
    @contextmanager
    def transaction(self):
        original=copy.deepcopy(self.data)
        try:yield
        except BaseException:
            self.data=original;self.rollbacks+=1;raise
        else:self.commits+=1


class PgToolSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='v194-tools-offline-');self.addCleanup(self.temp.cleanup)
        self.prefix='V194_FUNC_OFFLINE_T1'
        self.ledger=Ledger(Path(self.temp.name)/'ownership.json',self.prefix)
        self.ledger.baseline_catalog='a'*64;self.ledger.save()
        self.db=FakeDatabase()
        self.guard=patch.object(psycopg.Connection,'connect',side_effect=AssertionError('Offline tests prohibit real PostgreSQL'))
        self.guard.start();self.addCleanup(self.guard.stop)
        network=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline tests prohibit network'))
        network.start();self.addCleanup(network.stop)
        security.VALIDATED.clear();security.VALIDATION_ERRORS.clear()

    def user(self,ident=1):return {'id':ident,'username':self.prefix+'_U',DIGEST:'a'*32}
    def own(self,table,row):
        self.ledger.stage('42',table,'insert',[row]);self.ledger.commit('42',lambda:None)
        self.db.data[table].append(dict(row))

    def restore(self):
        with patch('tools.v194_pg_ownership.evidence_for',return_value={'backend_pid':42}):
            ScopedRestorer(self.ledger).restore(self.db,authorized=True,validate_catalog=lambda c:None)

    def test_default_runner_is_offline_plan(self):
        with patch.object(runner,'Runtime') as runtime,patch.object(security,'target') as target,patch('sys.stdout',new_callable=io.StringIO) as stdout:
            self.assertEqual(runner.cli([]),0)
            self.assertFalse(json.loads(stdout.getvalue())['database_connected'])
            runtime.assert_not_called();target.assert_not_called()

    def test_write_run_without_both_grants_is_blocked(self):
        for flags in (['--run'],['--run','--authorize-test-db-writes'],['--run','--authorize-owned-cleanup']):
            with self.subTest(flags=flags),patch.object(runner,'Runtime') as runtime:
                with self.assertRaises(security.Blocked):runner.cli(flags)
                runtime.assert_not_called()

    def test_ten_case_implementations_exist(self):
        self.assertEqual(set(runner.CASES),set(range(1,11)))
        for number in runner.CASES:self.assertTrue(callable(getattr(runner,'case_'+str(number))))

    def test_new_root_without_unique_marker_is_blocked(self):
        row=self.user();row['username']='unowned'
        with self.assertRaises(security.Blocked):self.ledger.stage('42','users','insert',[row])
        self.assertTrue(self.ledger.blocked)

    def test_child_requires_owned_parent(self):
        row={'id':3,'branch_id':999,'name':self.prefix,DIGEST:'b'*32}
        with self.assertRaises(security.Blocked):self.ledger.stage('42','branch_signers','insert',[row])

    def test_tracking_does_not_commit_until_real_callback(self):
        self.ledger.stage('42','users','insert',[self.user()])
        check=Mock()
        disk=json.loads(self.ledger.path.read_text())
        self.assertEqual(disk['owned']['users'],{})
        self.assertEqual(len(disk['pending']['42']),1)
        self.ledger.commit('42',check)
        check.assert_called_once()
        self.assertEqual(self.ledger.owned['users']['1']['key'],1)

    def test_rollback_discards_prepared_row_without_claiming_it(self):
        self.ledger.stage('42','users','insert',[self.user()])
        self.ledger.rollback('42')
        self.assertFalse(self.ledger.pending);self.assertFalse(self.ledger.owned['users'])
        self.assertEqual(self.ledger.history[-1]['operation'],'rollback')

    def test_ambiguous_commit_preserves_journal_and_blocks_cleanup(self):
        self.ledger.stage('42','users','insert',[self.user()])
        with self.assertRaises(security.Blocked):self.ledger.commit('42',Mock(side_effect=OSError('synthetic ACK loss')))
        self.ledger.rollback('42')
        self.assertTrue(self.ledger.uncertain);self.assertIn('42',self.ledger.pending)
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_journal_can_be_loaded_without_connecting(self):
        self.own('users',self.user())
        loaded=Ledger.load(self.ledger.path)
        self.assertTrue(loaded.blocked)
        self.assertNotIn('digest',loaded.path.read_text())
        self.assertEqual(loaded.baseline_catalog,'a'*64)

    def test_restore_needs_explicit_cleanup_grant(self):
        with self.assertRaises(security.Blocked):ScopedRestorer(self.ledger).restore(self.db)
        self.assertEqual(self.db.statements,[])

    def test_restore_needs_catalog_verifier(self):
        with self.assertRaises(security.Blocked):ScopedRestorer(self.ledger).restore(self.db,authorized=True)
        self.assertEqual(self.db.statements,[])

    def test_unknown_row_blocks_before_any_delete(self):
        self.own('users',self.user());self.db.data['drivers'].append({'id':999,'name':'unknown',DIGEST:'b'*32})
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))
        self.assertEqual(len(self.db.data['users']),1)

    def test_changed_fingerprint_blocks_cleanup(self):
        self.own('users',self.user());self.db.data['users'][0][DIGEST]='c'*32
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_fake_restoration_is_pk_and_fingerprint_scoped(self):
        self.own('users',self.user());self.restore()
        deletes=[(s,p) for s,p in self.db.statements if s.startswith('DELETE')]
        self.assertEqual(len(deletes),1)
        self.assertIn('WHERE t."id"=%s AND md5(to_jsonb(t)::text)=%s',deletes[0][0])
        self.assertEqual(deletes[0][1],(1,'a'*32))
        self.assertFalse(any('DELETE FROM public."schema_migrations"' in s for s,_ in self.db.statements))
        self.assertFalse(self.ledger.owned['users']);self.assertEqual(self.db.commits,1)

    def test_cleanup_rowcount_mismatch_rolls_back_whole_fake_transaction(self):
        self.own('users',self.user());self.db.bad_delete=True
        with self.assertRaises(security.CheckFailure):self.restore()
        self.assertEqual(len(self.db.data['users']),1);self.assertEqual(self.db.commits,0);self.assertEqual(self.db.rollbacks,1)

    def test_fk_order_deletes_signer_before_branch(self):
        branch={'id':4,'name':self.prefix+'_B',DIGEST:'d'*32}
        self.own('branches',branch)
        self.own('branch_signers',{'id':5,'branch_id':4,'name':self.prefix+'_P',DIGEST:'e'*32})
        self.restore()
        deletes=[s for s,_ in self.db.statements if s.startswith('DELETE')]
        self.assertIn('"branch_signers"',deletes[0]);self.assertIn('"branches"',deletes[1])

    def test_pending_transaction_blocks_restoration(self):
        self.ledger.stage('42','users','insert',[self.user()])
        with self.assertRaises(security.Blocked):self.restore()
        self.assertEqual(self.db.statements,[])

    def test_update_and_delete_get_owned_key_predicate(self):
        for operation,text in (('update','UPDATE users SET role=? WHERE id=? OR username=?'),('delete','DELETE FROM users WHERE id=?')):
            statement,params=restrict_existing(text,'users',operation,[1,2])
            self.assertIn(') AND ("users"."id" IN (%s,%s))',statement)
            self.assertEqual(params,(1,2))

    def test_unconditional_mutation_rejected(self):
        for operation,text in (('update','UPDATE users SET role=?'),('delete','DELETE FROM users')):
            with self.assertRaises(security.Blocked):restrict_existing(text,'users',operation,[1])

    def test_upsert_conflict_cannot_touch_unowned_rows(self):
        statement,params=restrict_existing('INSERT INTO daily_reports(service_date) VALUES(?) ON CONFLICT(service_date) DO UPDATE SET status=excluded.status','daily_reports','insert',[])
        self.assertTrue(statement.endswith('WHERE FALSE'));self.assertEqual(params,())

    def test_upsert_existing_guard_is_preserved(self):
        statement,params=restrict_existing("INSERT INTO daily_reports(service_date) VALUES(?) ON CONFLICT(service_date) DO UPDATE SET status=excluded.status WHERE daily_reports.status<>'LOCKED'",'daily_reports','insert',[9])
        self.assertIn("WHERE ( daily_reports.status<>'LOCKED') AND",statement)
        self.assertEqual(params,(9,))

    def test_buffered_cursor_preserves_returned_id_and_iteration(self):
        cursor=BufferedCursor([{'id':1},{'id':2}],2,1)
        self.assertEqual(cursor.lastrowid,1);self.assertEqual(cursor.fetchone(),{'id':1})
        self.assertEqual(list(cursor),[{'id':2}]);self.assertIsNone(cursor.fetchone());self.assertEqual(cursor.rowcount,2)

    def test_tracker_rejects_destructive_and_multiple_statements(self):
        raw=SimpleNamespace(con=self.db)
        tracked=TrackedAdapter(raw,self.ledger,None)
        for statement in ('DROP TABLE users','TRUNCATE users','SELECT 1; DELETE FROM users','SELECT setval(1,2)'):
            with self.assertRaises(security.Blocked):tracked.execute(statement)
        self.assertEqual(self.db.statements,[])

    def test_schema_sql_is_disabled_by_default(self):
        raw=SimpleNamespace(con=self.db)
        tracked=TrackedAdapter(raw,self.ledger,None)
        with self.assertRaises(security.CheckFailure):tracked.execute('CREATE TABLE IF NOT EXISTS users(id BIGINT)')
        self.assertEqual(self.db.statements,[])

    def test_tracker_cannot_delete_migration_version(self):
        raw=SimpleNamespace(con=self.db)
        tracked=TrackedAdapter(raw,self.ledger,None,lambda:True)
        with self.assertRaises(security.CheckFailure):tracked.execute('DELETE FROM schema_migrations WHERE version=?',('v194-safe-schema-1',))
        self.assertEqual(self.db.statements,[])

    def test_signature_images_are_valid_and_distinct(self):
        self.assertTrue(runner.ink('A').startswith('data:image/png;base64,'))
        self.assertNotEqual(runner.ink('A'),runner.ink('B'))

    def test_runtime_cannot_be_constructed_without_both_grants(self):
        with patch.object(security,'guarded_application') as app:
            with self.assertRaises(security.Blocked):runner.Runtime(self.temp.name,writes=True,cleanup=False)
            app.assert_not_called()

    def test_browser_requirement_blocks_before_connection(self):
        with patch.object(runner,'Runtime') as runtime:
            with self.assertRaises(security.Blocked):
                runner.cli(['--run','--authorize-test-db-writes','--authorize-owned-cleanup'])
            runtime.assert_not_called()

    def test_dedicated_target_rejects_routing_and_tls_overrides(self):
        base='postgresql://synthetic:offline@'+security.EXPECTED_HOST+'/'+security.EXPECTED_DB
        context=Mock();context.get_ca_certs.return_value=[{'synthetic':True}]
        with patch.dict('os.environ',{},clear=True),patch.object(security.ssl,'create_default_context',return_value=context):
            import os
            for suffix in ('?sslmode=require','?sslmode=verify-full&sslmode=disable','?hostaddr=127.0.0.1'):
                os.environ['MOVINGMATCH_V194_TEST_DATABASE_URL']=base+suffix
                with self.assertRaises(security.CheckFailure):security.target()
            os.environ['MOVINGMATCH_V194_TEST_DATABASE_URL']=base
            params=security.conninfo_to_dict(security.target())
            self.assertEqual(params['sslmode'],'verify-full')
            self.assertEqual(params['sslrootcert'],security.CA_FILE)
            os.environ['MOVINGMATCH_V194_TEST_DATABASE_URL']='postgresql://'+security.EXPECTED_HOST+'/'+security.EXPECTED_DB
            with self.assertRaises(security.CheckFailure):security.target()

    def test_each_physical_connection_is_verified_before_return(self):
        connections=[Mock(),Mock()]
        with patch.object(security,'target',return_value='normalized synthetic target'),patch.object(psycopg.Connection,'connect',side_effect=connections) as connect,patch.object(security,'verify_new_connection',side_effect=[{'backend_pid':101},{'backend_pid':102}]) as verify:
            for connection in connections:
                self.assertIs(security.VerifiedConnection.connect('normalized synthetic target'),connection)
            self.assertEqual(verify.call_count,2)
            self.assertEqual(len(security.VALIDATED),2)
            for call in connect.call_args_list:
                self.assertEqual(call.kwargs['sslmode'],'verify-full')
                self.assertEqual(call.kwargs['sslrootcert'],security.CA_FILE)

    def test_failed_physical_verification_closes_and_latches(self):
        connection=Mock()
        with patch.object(security,'target',return_value='normalized synthetic target'),patch.object(psycopg.Connection,'connect',return_value=connection),patch.object(security,'verify_new_connection',side_effect=security.CheckFailure('synthetic identity mismatch')):
            with self.assertRaises(security.Blocked):security.VerifiedConnection.connect('normalized synthetic target')
            connection.close.assert_called_once()
            with self.assertRaises(security.Blocked):security.healthy()
            self.assertFalse(security.VALIDATED)

    def test_identity_validator_checks_effective_target_version_tls_and_closes_read_transaction(self):
        expected={'host':security.EXPECTED_HOST,'dbname':security.EXPECTED_DB,'sslmode':'verify-full','sslrootcert':security.CA_FILE}
        def connection(params=None,version=180006,db=security.EXPECTED_DB,tls=True):
            c=Mock()
            c.info.get_parameters.return_value=params or dict(expected)
            c.info.server_version=version;c.info.transaction_status=psycopg.pq.TransactionStatus.IDLE
            c.execute.side_effect=[BufferedCursor([{'db':db,'schema':'public','pid':101,'version':'PostgreSQL 18.6'}],1),BufferedCursor([{'ssl':tls,'version':'TLSv1.3','cipher':'synthetic-cipher'}],1)]
            return c
        good=connection()
        self.assertTrue(security.verify_new_connection(good)['ca_hostname_verified'])
        good.rollback.assert_called_once()
        for kwargs in ({'params':{**expected,'sslmode':'require'}},{'params':{**expected,'host':'unapproved.invalid'}},{'version':170000},{'db':'unapproved'},{'tls':False}):
            with self.subTest(kwargs=kwargs),self.assertRaises(security.CheckFailure):security.verify_new_connection(connection(**kwargs))

    def test_tracking_insert_captures_returning_and_real_commit(self):
        raw=SimpleNamespace(con=self.db,_q=lambda s:s)
        tracked=TrackedAdapter(raw,self.ledger,None)
        original=self.db.execute
        def execute(statement,params=()):
            if str(statement).startswith('INSERT INTO users'):
                row=self.user();self.db.data['users'].append(row)
                return BufferedCursor([row],1)
            return original(statement,params)
        self.db.execute=execute
        self.db.commit=Mock()
        cursor=tracked.execute('INSERT INTO users(username) VALUES(?)',(self.prefix+'_U',))
        self.assertEqual(cursor.lastrowid,1)
        self.assertEqual(cursor.fetchone(),{'id':1})
        self.assertIn('42',self.ledger.pending)
        self.db.commit.assert_not_called()
        tracked.commit()
        self.db.commit.assert_called_once()
        self.assertFalse(self.ledger.pending)
        self.assertEqual(self.ledger.keys('users','42'),[1])

    def test_forged_run_uuid_blocks_before_delete(self):
        self.own('users',self.user());self.ledger.run_uuid='forged'
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_forged_primary_key_and_manifest_cannot_authorize_delete(self):
        self.own('users',self.user())
        self.db.data['users'][0]['id']=999
        record=self.ledger.owned['users'].pop('1');record['key']=999
        self.ledger.owned['users']['999']=record
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_forged_marker_even_with_matching_fake_fingerprint_is_denied(self):
        self.own('users',self.user());self.db.data['users'][0]['username']='unowned'
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_forged_secondary_relation_is_denied(self):
        self.own('branches',{'id':4,'name':self.prefix+'_B','route_id':999,DIGEST:'d'*32})
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_forged_matching_manifest_digest_is_not_proof(self):
        self.own('users',self.user());self.db.data['users'][0][DIGEST]='c'*32
        self.ledger.owned['users']['1']['digest']='c'*32
        with self.assertRaises(security.Blocked):self.restore()
        self.assertFalse(any(s.startswith('DELETE') for s,_ in self.db.statements))

    def test_credentials_and_full_row_digests_never_persist(self):
        self.ledger.baseline_catalog='b'*64
        self.own('users',{**self.user(),'password_hash':'SYNTHETIC_PASSWORD_DERIVATIVE'})
        row={'token_hash':'SYNTHETIC_SESSION_DERIVATIVE','user_id':1,'expires_at':'2030-01-04',DIGEST:'f'*32}
        self.ledger.stage('42','sessions','insert',[row]);self.ledger.commit('42',lambda:None)
        text=self.ledger.path.read_text()
        for secret in ('SYNTHETIC_PASSWORD_DERIVATIVE','SYNTHETIC_SESSION_DERIVATIVE','a'*32,'f'*32,'token_hash','password_hash'):
            self.assertNotIn(secret,text)
        self.assertIn('receipt',text)
        with self.assertRaises(security.Blocked):
            ScopedRestorer(Ledger.load(self.ledger.path)).restore(self.db,authorized=True,validate_catalog=lambda c:None)

    def test_independent_reader_requires_distinct_backend(self):
        reader=Mock();reader.__enter__=Mock(return_value=reader);reader.__exit__=Mock(return_value=False)
        with patch.object(security,'verified_connect',return_value=reader),patch.object(security,'evidence_for',return_value={'backend_pid':42}):
            with self.assertRaises(security.CheckFailure):runner.verify_restored({'counts':{}},42)

    def test_independent_reader_enforces_read_only_and_snapshot(self):
        reader=Mock();reader.__enter__=Mock(return_value=reader);reader.__exit__=Mock(return_value=False)
        def execute(sql):
            if sql=='SHOW transaction_read_only':return BufferedCursor([{'transaction_read_only':'on'}],1)
            if sql.startswith('SELECT version'):return BufferedCursor([{'version':'v194-safe-schema-1'}],1)
            return BufferedCursor([],0)
        reader.execute.side_effect=execute
        baseline={'counts':{'users':0,'schema_migrations':1},'catalog_sha256':'catalog','data_sha256':'empty'}
        with patch.object(security,'verified_connect',return_value=reader),patch.object(security,'evidence_for',return_value={'backend_pid':43}),patch.object(runner,'database_state',return_value=baseline):
            self.assertEqual(runner.verify_restored(baseline,42)['result'],'PASS')
            self.assertIn('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY',[c.args[0] for c in reader.execute.call_args_list])
        with patch.object(security,'verified_connect',return_value=reader),patch.object(security,'evidence_for',return_value={'backend_pid':43}),patch.object(runner,'database_state',return_value={'changed':True}):
            with self.assertRaises(security.CheckFailure):runner.verify_restored(baseline,42)

    def test_frontend_already_signed_and_http_conflict(self):
        import subprocess
        page=Path('app/static/index.html').read_text()
        function=page[page.index('async function submitOfficeRouteSign('):page.index('function escapeDriverText(')]
        common=Path('app/static/common.js').read_text()
        script=common+'\n'+function+"""
        (async()=>{
          const assert=require('node:assert/strict');
          global.sessionStorage={getItem(){return null}};
          global.document={getElementById(){return {toDataURL(){return 'synthetic'}}}};
          global.confirm=()=>true;global.routeOfficeSignatureHost={innerHTML:''};
          global.loadSignStatus=async()=>{};let messages=[];global.alert=x=>messages.push(x);
          for(const [data,expected] of [[{},'簽核完成'],[{already_signed:true},'此路線已完成簽核，原簽名及時間均已保留']]){
            global.fetch=async()=>({ok:true,status:200,text:async()=>JSON.stringify(data)});
            await submitOfficeRouteSign(1);assert.equal(messages.pop(),expected);
          }
          global.fetch=async()=>({ok:false,status:409,text:async()=>JSON.stringify({detail:'簽名衝突'})});
          await submitOfficeRouteSign(1);assert.equal(messages.pop(),'簽名衝突');
          assert.equal(messages.length,0);
        })().catch(()=>process.exit(1));
        """
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,'Frontend offline assertions failed')

    def test_writer_is_closed_before_independent_verification(self):
        events=[]
        writer=Mock();writer.__enter__=Mock(return_value=writer)
        writer.__exit__=Mock(side_effect=lambda *a:events.append('writer_closed'))
        runtime=SimpleNamespace(ledger=self.ledger,cleanup_authorized=True,baseline={'counts':{}},mail=Mock(call_count=0))
        def verify(baseline,pid):
            self.assertEqual(events,['writer_closed']);self.assertEqual(pid,42)
            events.append('reader_verified');return {'result':'PASS'}
        with patch.object(security,'verified_connect',return_value=writer),patch.object(security,'evidence_for',return_value={'backend_pid':42}),patch.object(runner,'ScopedRestorer') as restorer,patch.object(runner,'verify_restored',side_effect=verify):
            runner.Runtime.restore(runtime)
            restorer.return_value.restore.assert_called_once()
        self.assertEqual(events,['writer_closed','reader_verified'])

    def test_restart_restore_is_blocked_without_connecting(self):
        self.own('users',self.user())
        with patch.object(security,'verified_connect') as connect:
            with self.assertRaises(security.Blocked):runner.journal_action(self.ledger.path,restore=True)
            connect.assert_not_called()

    def test_server_read_only_evidence_is_mandatory(self):
        reader=Mock();reader.__enter__=Mock(return_value=reader);reader.__exit__=Mock(return_value=False)
        reader.execute.return_value=BufferedCursor([{'transaction_read_only':'off'}],1)
        with patch.object(security,'verified_connect',return_value=reader),patch.object(security,'evidence_for',return_value={'backend_pid':43}),patch.object(runner,'database_state') as snapshot:
            with self.assertRaises(security.CheckFailure):runner.verify_restored({'counts':{}},42)
            snapshot.assert_not_called()


if __name__=='__main__':unittest.main()
