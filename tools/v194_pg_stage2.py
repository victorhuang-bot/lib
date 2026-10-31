"""Explicit PostgreSQL 18.6 functional acceptance; default mode is offline plan.

Never run with write/cleanup grants until the separate review is approved.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from tools import v194_pg_security as security
from tools.v194_pg_ownership import Ledger, ScopedRestorer, ORDER, read_rows
from tools.v194_pg_tracking import TrackedAdapter

CASES = {
    1:'Schema and migrations with committed business history',
    2:'Independent PostgreSQL transactions and isolation',
    3:'Actual business failure rollback',
    4:'Typhoon calendar and document postponement',
    5:'Sum policy and sequential/concurrent duplicate requests',
    6:'Delivery/signature/approval/report lock protection',
    7:'Branch signer CRUD, concurrency and real dropdown',
    8:'Cross-branch and role isolation',
    9:'Original, late and corrected signature snapshots',
    10:'Four K13/K13S identity states and historical relations',
}


def check(condition, label): security.require(condition,label)


def ink(label):
    from PIL import Image, ImageDraw
    digest=hashlib.sha256(label.encode()).digest()
    image=Image.new('RGBA',(100,40),(0,0,0,0))
    ImageDraw.Draw(image).line((3,30,20+digest[0]%30,5,90,15+digest[1]%20),fill='black',width=3)
    stream=io.BytesIO(); image.save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


def database_state(connection):
    tables=[r['tablename'] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    check(len(tables)==26,'Unexpected public table inventory')
    catalog={
        'tables':tables,
        'columns':connection.execute("SELECT table_name,column_name,ordinal_position,udt_name,is_nullable,column_default,is_identity FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,3").fetchall(),
        'constraints':connection.execute("SELECT cl.relname,c.contype,c.conname,pg_get_constraintdef(c.oid),c.convalidated FROM pg_constraint c JOIN pg_class cl ON cl.oid=c.conrelid JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='public' ORDER BY 1,2,3").fetchall(),
        'indexes':connection.execute("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2").fetchall(),
        'schemas':connection.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY 1").fetchall(),
    }
    statements=[sql.SQL("SELECT {} AS table_name,count(*) AS n,coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) AS records FROM public.{} t").format(sql.Literal(t),sql.Identifier(t)) for t in tables]
    data=connection.execute(sql.SQL(' UNION ALL ').join(statements)).fetchall()
    digest=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
    return {'catalog_sha256':digest(catalog),'data_sha256':digest(data),
            'counts':{r['table_name']:r['n'] for r in data},'tables':len(tables),'indexes':len(catalog['indexes']),
            'schemas':catalog['schemas']}


class WorkerTagApplication:
    def __init__(self, app, context): self.app=app; self.context=context
    async def __call__(self, scope, receive, send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        tag=dict(scope.get('headers',[])).get(b'x-v194-worker',b'').decode('ascii') or None
        token=self.context.set(tag)
        try: await self.app(scope,receive,send)
        finally:self.context.reset(token)


class WorkerClient:
    def __init__(self,runtime):self.runtime=runtime
    def request(self,method,path,**kwargs):
        r=self.runtime;client=getattr(r._local,'client',r._client)
        headers=dict(kwargs.pop('headers',{}))
        tag=getattr(r._local,'tag',None)
        if tag:headers['X-V194-Worker']=tag
        return client.request(method,path,headers=headers,**kwargs)
    def __getattr__(self,name):
        if name in ('get','post','patch','put','delete'):
            return lambda path,**kwargs:self.request(name.upper(),path,**kwargs)
        raise AttributeError(name)
    def close(self):self.runtime._client.close()


class Runtime:
    def __init__(self, directory, *, writes=False, cleanup=False, browser=False):
        if not writes or not cleanup:
            raise security.Blocked('Separate PostgreSQL write AND scoped-restoration grants are required')
        self.directory=Path(directory); self.browser=browser; self.cleanup_authorized=cleanup; self.schema_enabled=False
        self.run_id='V194_FUNC_'+uuid.uuid4().hex
        self.ledger=None; self.workers=[]; self.used_backends=set(); self._pid_lock=threading.Lock()
        self.worker_context=ContextVar('v194_worker',default=None);self._local=threading.local();self.parallel_context=None
        self.main=security.guarded_application(security.target())
        self.raw_db=self.main.db; self.main.db=self.db
        self.observer=security.verified_connect(autocommit=True,row_factory=dict_row)
        self.observer.execute('SET default_transaction_read_only=on')
        self.coordinator=security.verified_connect(autocommit=True,row_factory=dict_row)
        check(self.coordinator.execute('SELECT pg_try_advisory_lock(194200) AS held').fetchone()['held'],'Another acceptance run holds the test lease')
        self.baseline=database_state(self.observer)
        check(self.baseline['indexes']==43,'Unexpected index inventory')
        check(all(n==0 for t,n in self.baseline['counts'].items() if t!='schema_migrations'),'Pre-existing business data; no cleanup allowed')
        check(self.observer.execute('SELECT version FROM schema_migrations').fetchall()==[{'version':'v194-safe-schema-1'}],'Migration version mismatch')
        check(self.baseline['schemas']==[{'nspname':'public'}],'Unknown schema exists')
        self.clock=datetime(2030,1,4,8,tzinfo=ZoneInfo('Asia/Taipei'))
        self.main.today=lambda:self.clock.date().isoformat()
        self.main.now=lambda:self.clock.isoformat(timespec='seconds')
        self.main.future_iso=lambda **kwargs:(self.clock+timedelta(**kwargs)).isoformat(timespec='seconds')
        self.mail=Mock(return_value={'success':True,'message_id':'synthetic-mock'})
        self.main.gmail_send_email=self.mail
        from app.services import gmail_service
        gmail_service.send_email=self.mail
        gmail_service._get_credentials=Mock(side_effect=security.Blocked('OAuth/network mail is prohibited'))
        self.main.publish=MockPublish()
        from fastapi.testclient import TestClient
        self.application=WorkerTagApplication(self.main.app,self.worker_context)
        self._client=TestClient(self.application);self.client=WorkerClient(self)

    def db(self):
        check(self.ledger is not None,'Case ledger must exist before application DB access')
        adapter=self.raw_db()
        with self._pid_lock: self.used_backends.add(adapter.con.info.backend_pid)
        return TrackedAdapter(adapter,self.ledger,self.main,lambda:self.schema_enabled,self.business_lock)

    @contextmanager
    def connection(self):
        c=self.db()
        try: yield c
        finally:c.close()

    def mutate(self, statement, params=()):
        with self.connection() as c:
            cursor=c.execute(statement,params); result=cursor.lastrowid
            c.commit(); return result

    def rows(self,table,where='',params=()):
        check(table in ORDER,'Unknown snapshot table')
        return self.observer.execute('SELECT * FROM public.'+table+(' WHERE '+where if where else '')+' ORDER BY 1',params).fetchall()

    def update(self,table,key,**fields):
        from tools.v194_pg_ownership import KEYS
        check(table in ORDER,'Unapproved fixture update')
        column=KEYS.get(table,'id')
        self.mutate('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+column+'=?',tuple(fields.values())+(key,))

    def request(self,method,path,role='admin',body=None,expected=200):
        result=self.client.request(method,path,headers=self.headers.get(role,{}),json=body)
        check(result.status_code==expected,'Unexpected API status: '+method+' '+path.split('?')[0])
        return result.json()

    def fixture(self,number):
        self.clock=datetime(2030,1,4,8,tzinfo=ZoneInfo('Asia/Taipei'))
        self.prefix=self.run_id+'_T'+str(number)
        self.ledger=Ledger(self.directory/('case-'+str(number)+'-ownership.json'),self.prefix)
        self.ledger.baseline_catalog=self.baseline['catalog_sha256'];self.ledger.baseline_state=self.baseline;self.ledger.save()
        self.ledger.dates={(self.clock.date()+timedelta(days=d)).isoformat() for d in range(-370,370)}
        self.main._prefill_cache_clear()
        self.main.publish.events.clear()
        self.headers={}; self.tokens={}; self.users=[]; self.drivers=[]; self.routes=[]; self.branches=[]
        self.schema_enabled=False
        with self.connection() as c:
            for index,role in enumerate(('ADMIN','SECRETARY')):
                uid=c.execute('INSERT INTO users(username,password_hash,role,is_active) VALUES(?,?,?,1)',(self.prefix+'_'+role,self.main.hash_secret(uuid.uuid4().hex),role)).lastrowid
                token=uuid.uuid4().hex
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(self.main.thash(token),uid,self.main.future_iso(hours=12)))
                self.users.append(uid); self.headers[role.lower()]={'Authorization':'Bearer '+token}
            for index in range(2):
                driver=c.execute('INSERT INTO drivers(name,active,pin_hash) VALUES(?,1,?)',(self.prefix+'_D'+str(index),self.main.hash_secret('2468'))).lastrowid
                route=c.execute('INSERT INTO routes(code,name,active) VALUES(?,?,1)',(self.prefix+'_R'+str(index),self.prefix+'_R'+str(index))).lastrowid
                c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',('DEFAULT_ROUTE_DRIVER_'+str(route),str(driver)))
                qr=uuid.uuid4().hex
                branch=c.execute("INSERT INTO branches(code,name,route_id,stop_order,active,pin_hash,access_token_hash,delivery_weekdays) VALUES(?,?,?,1,1,?,?,'1,2,3,4,5')",(self.prefix+'_B'+str(index),self.prefix+'_B'+str(index),route,self.main.hash_secret('2468'),self.main.thash(qr))).lastrowid
                for table,field,ident,role in (('driver_sessions','driver_id',driver,'driver'+str(index)),('branch_sessions','branch_id',branch,'branch'+str(index))):
                    token=uuid.uuid4().hex
                    c.execute('INSERT INTO '+table+'(token_hash,'+field+',expires_at) VALUES(?,?,?)',(self.main.thash(token),ident,self.main.future_iso(hours=12)))
                    self.tokens[role]=token; self.headers[role]={'Authorization':'Bearer '+token}
                self.drivers.append(driver); self.routes.append(route); self.branches.append(branch)
                self.tokens['qr'+str(index)]=qr
            c.commit()

    def delivery(self,day='2030-01-04',branch=0,qty=5,status='WAITING_DRIVER',**fields):
        with self.connection() as c:
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,?,?) ON CONFLICT(service_date,route_id) DO NOTHING',(day,self.routes[branch],self.drivers[branch]))
            rid=c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=?',(day,self.routes[branch])).fetchone()['id']
            did=c.execute('INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_original,document_final) VALUES(?,?,?,?,?,?)',(day,rid,self.branches[branch],status,qty,qty)).lastrowid
            if fields:c.execute('UPDATE deliveries SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(fields.values())+(did,))
            c.execute('UPDATE branch_sessions SET delivery_id=? WHERE token_hash=?',(did,self.main.thash(self.tokens['branch'+str(branch)])))
            c.commit(); return did,rid

    def person(self,branch=0,suffix='P'):
        return self.request('POST','/api/branch-session/signers','branch'+str(branch),{'name':self.prefix+'_'+suffix})

    def save(self,**changes):
        body={'exception_type':'STOP','branch_id':self.branches[0],'start_date':'2030-01-04','end_date':'2030-01-04','reason':self.prefix}
        body.update(changes)
        return self.client.post('/api/schedule-exceptions/range',headers=self.headers['admin'],json=body)

    def sign(self,person,branch=0,**changes):
        body={'signer_id':person['id'],'signer':person['name'],'signature':ink(self.prefix),'document':5,'document_return':2,'outbound':3,'inbound':4}
        body.update(changes)
        return self.client.post('/api/branch-session/sign',headers=self.headers['branch'+str(branch)],json=body)

    def business_lock(self,connection):
        tag=self.worker_context.get();context=self.parallel_context
        if not tag or context is None:return
        with self._pid_lock:
            if tag in context['pids']:return
            context['pids'][tag]=connection.info.backend_pid
        # Barrier on the ACTUAL application mutation connection before its lock.
        context['barrier'].wait(timeout=10)

    def parallel(self,functions):
        from fastapi.testclient import TestClient
        context={'barrier':threading.Barrier(len(functions)),'pids':{}}
        self.parallel_context=context
        def work(index,fn):
            self._local.client=TestClient(self.application)
            self._local.tag='worker-'+str(index)
            try:return fn()
            finally:
                self._local.client.close()
                del self._local.client;del self._local.tag
        try:
            with ThreadPoolExecutor(max_workers=len(functions)) as executor:
                futures=[executor.submit(work,i,fn) for i,fn in enumerate(functions)]
                results=[future.result(timeout=45) for future in futures]
            pids=list(context['pids'].values())
            check(len(pids)==len(functions) and len(set(pids))==len(functions),'Actual parallel mutation backends were not independent')
            return results,pids
        finally:self.parallel_context=None

    def restore(self):
        # Caller must first stop all worker requests; this path is never run offline.
        if self.ledger is None:return
        if self.ledger.blocked or self.ledger.uncertain:
            raise security.Blocked('Unsafe ownership state; restoration prohibited')
        with security.verified_connect(autocommit=True,row_factory=dict_row) as c:
            writer_pid=security.evidence_for(c)['backend_pid']
            ScopedRestorer(self.ledger).restore(c,authorized=self.cleanup_authorized,validate_catalog=lambda con:check(database_state(con)['catalog_sha256']==self.baseline['catalog_sha256'],'Catalog changed; cleanup prohibited'))
        try:self.restoration_evidence=verify_restored(self.baseline,writer_pid)
        except BaseException:
            self.ledger.blocked=True
            self.ledger.history.append({'operation':'independent_verification_blocked'})
            self.ledger.save()
            raise security.Blocked('Independent restoration verification unavailable; PASS prohibited') from None
        self.ledger.history.append({'operation':'independent_verification_passed'})
        self.ledger.save()
        check(self.mail.call_count==0,'A real workflow attempted to send email')

    def close(self):
        try:
            if hasattr(self,'client'):self.client.close()
        finally:
            try:
                if hasattr(self,'main') and self.main._PG_POOL is not None:self.main._PG_POOL.close()
            finally:
                for name in ('observer','coordinator'):
                    connection=getattr(self,name,None)
                    if connection is not None:connection.close()



class MockPublish:
    def __init__(self):self.events=[]
    async def __call__(self,event):self.events.append(dict(event))


def case_1(r):
    did,_=r.delivery(status='STOP_COMPLETED',signer_name=r.prefix,branch_signature=ink(r.prefix),branch_signed_at=r.main.now(),receipt_at=r.main.now())
    r.person(); before=database_state(r.observer)
    r.schema_enabled=True
    try:
        r.main.init_db(); r.main.init_db()
    finally:r.schema_enabled=False
    check(database_state(r.observer)==before,'Migration modified committed historical business data')
    return {'history_preserved':True,'migration_runs':2}


def case_2(r):
    did,_=r.delivery(); observer=r.observer
    with r.connection() as a:
        a.execute('UPDATE deliveries SET document_final=? WHERE id=?',(9,did))
        check(observer.execute('SELECT document_final FROM deliveries WHERE id=%s',(did,)).fetchone()['document_final']==5,'Dirty read observed')
        a.rollback()
        check(observer.execute('SELECT document_final FROM deliveries WHERE id=%s',(did,)).fetchone()['document_final']==5,'Rollback not visible')
        with security.verified_connect(autocommit=False,row_factory=dict_row) as b:
            b.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            check(b.execute('SELECT document_final FROM deliveries WHERE id=%s',(did,)).fetchone()['document_final']==5,'Initial repeatable-read snapshot incorrect')
            a.execute('UPDATE deliveries SET document_final=? WHERE id=?',(11,did));a.commit()
            check(b.execute('SELECT document_final FROM deliveries WHERE id=%s',(did,)).fetchone()['document_final']==5,'Repeatable-read snapshot changed')
            b.rollback()
            check(b.execute('SELECT document_final FROM deliveries WHERE id=%s',(did,)).fetchone()['document_final']==11,'Fresh snapshot missed commit')
            b.rollback()
            pids=[a.con.info.backend_pid,b.info.backend_pid,observer.info.backend_pid]
            check(len(set(pids))==3,'Isolation test reused a backend')
    return {'independent_backend_pids':pids,'dirty_reads':False,'repeatable_read':True}


def case_3(r):
    r.delivery(); before=database_state(r.observer); original=r.main.rebuild_service_date
    def fault(c,day,commit=True):
        original(c,day,commit=commit)
        if day=='2030-01-05':c.execute('SELECT 1/0')
    with patch.object(r.main,'rebuild_service_date',side_effect=fault):
        try:r.save(end_date='2030-01-05')
        except psycopg.errors.DivisionByZero:pass
        else:raise security.CheckFailure('Expected PostgreSQL fault did not propagate')
    check(database_state(r.observer)==before,'Business rollback left partial updates or audit')
    return {'sqlstate':'22012','full_business_snapshot_restored':True}


def case_4(r):
    r.delivery(qty=5);r.delivery(branch=1,qty=7)
    before=database_state(r.observer); preview=r.save(exception_type='TYPHOON',end_date='2030-01-07',preview=True)
    check(preview.status_code==200 and database_state(r.observer)==before,'Preview wrote data')
    result=r.save(exception_type='TYPHOON',end_date='2030-01-07')
    check(result.status_code==200,'Typhoon request failed')
    check({x['target_date'] for x in result.json()['plan']}=={'2030-01-08'},'Typhoon postponed to incorrect day')
    active=r.rows('deliveries',"status<>'VOID'")
    check(sum(x['document_final'] or 0 for x in active)==12,'Postponement lost or duplicated quantity')
    sources=r.rows('deliveries',"status='VOID'")
    check(sorted(x['document_original'] for x in sources)==[5,7] and all(x['document_final'] is None for x in sources),'Source originals not preserved')
    r.delivery(day='2030-01-11',qty=9)
    r.mutate('INSERT INTO global_closures(service_date,reason) VALUES(?,?)',('2030-01-14',r.prefix))
    r.mutate("INSERT INTO delivery_exceptions(service_date,branch_id,exception_type,reason) VALUES(?,?,'STOP',?)",('2030-01-15',r.branches[0],r.prefix))
    skipped=r.save(start_date='2030-01-11',end_date='2030-01-11')
    check(skipped.status_code==200 and skipped.json()['plan'][0]['target_date']=='2030-01-16','Closure/STOP weekday skipping failed')
    for _ in range(2):check(r.save(start_date='2030-01-12',end_date='2030-01-12',exception_type='ADD').status_code==200,'Weekend ADD failed')
    check(len(r.rows('deliveries',"service_date='2030-01-12' AND branch_id=%s",(r.branches[0],)))==1,'Weekend ADD duplicated delivery')
    return {'target_date':'2030-01-08','total':12,'preview_read_only':True,'closure_stop_target':'2030-01-16','weekend_add_idempotent':True}


def case_5(r):
    r.delivery(qty=5);target,_=r.delivery(day='2030-01-07',qty=8)
    before=database_state(r.observer)
    check(r.save().status_code==409 and database_state(r.observer)==before,'Default collision did not reject atomically')
    results,pids=r.parallel([lambda:r.save(conflict_policy='sum'),lambda:r.save(conflict_policy='sum')])
    check(all(x.status_code==200 for x in results),'Concurrent duplicate requests failed')
    check(r.save(conflict_policy='sum').status_code==200,'Sequential duplicate request failed')
    row=r.rows('deliveries','id=%s',(target,))[0]
    check((row['document_original'],row['document_final'])==(8,13),'Duplicate sum or original overwrite')
    check(len(r.rows('audit_logs',"action='POSTPONE_DOCUMENT_SOURCE'"))==1,'Source moved twice')
    stale,_=r.delivery(day='2030-01-11',qty=5)
    preview=r.save(start_date='2030-01-11',end_date='2030-01-11',preview=True)
    check(preview.status_code==200,'Stale-preview fixture failed')
    r.update('deliveries',stale,document_final=9,row_version=2)
    before=database_state(r.observer)
    check(r.save(start_date='2030-01-11',end_date='2030-01-11',plan_token=preview.json()['plan_token']).status_code==409,'Stale preview accepted')
    check(database_state(r.observer)==before,'Stale preview changed data')
    r.delivery(day='2030-01-18',qty=0)
    zero=r.save(start_date='2030-01-18',end_date='2030-01-18')
    check(zero.status_code==200 and zero.json()['plan'][0]['total']==0,'Zero quantity lost its meaning')
    return {'total':13,'original':8,'independent_worker_backends':pids,'source_transfers':1,'stale_preview_rejected':True,'zero_quantity':True}


def case_6(r):
    did,rid=r.delivery()
    r.request('PATCH','/api/driver/deliveries/'+str(did)+'/outbound','driver0',{'qty':3})
    person=r.person()
    check(r.sign(person).status_code==200,'Legal branch sign failed')
    r.request('POST','/api/driver/deliveries/'+str(did)+'/confirm','driver0',{})
    before=database_state(r.observer)
    check(r.save().status_code==409 and database_state(r.observer)==before,'Completed source was changed')
    r.request('POST','/api/driver/routes/'+str(rid)+'/segment-sign','driver0',{'signature':ink(r.prefix+'_driver')})
    path='/api/routes/'+str(rid)+'/secretary-sign'
    signatures=[ink(r.prefix+'_secretaryA'),ink(r.prefix+'_secretaryB')]
    results,pids=r.parallel([lambda:r.client.post(path,headers=r.headers['secretary'],json={'signature':signatures[0]}),lambda:r.client.post(path,headers=r.headers['secretary'],json={'signature':signatures[1]})])
    check(sorted(x.status_code for x in results)==[200,409],'Concurrent secretary sign overwrote or rejected both')
    sig=signatures[next(i for i,x in enumerate(results) if x.status_code==200)]
    before=database_state(r.observer);r.clock+=timedelta(seconds=2)
    r.request('POST',path,'secretary',{'signature':sig})
    r.request('POST',path,'secretary',{'signature':ink('different')},409)
    check(database_state(r.observer)==before,'Secretary retry overwrote signature/time/audit')
    r.request('POST','/api/secretary/final-sign','secretary',{'signature':ink('final')})
    before=database_state(r.observer)
    r.request('POST',path,'secretary',{'signature':sig},409)
    with r.connection() as c:r.main.ensure_today(c)
    check(database_state(r.observer)==before,'Locked rebuild or sign changed data')
    export=r.client.get('/api/reports/daily.xlsx?service_date=2030-01-04',headers=r.headers['admin'])
    check(export.status_code==200 and export.content[:2]==b'PK','Signed-day report export failed')
    protected=({'outbound_original':0},{'status':'WAITING_BRANCH'},{'branch_signature':ink('protected')},
               {'status':'ADMIN_CLOSED'},{'driver_confirmed_at':r.main.now()},{'receipt_at':r.main.now()},{'inbound_final':0})
    for index,fields in enumerate(protected):
        day=(datetime(2030,1,10)+timedelta(days=index)).date().isoformat()
        status=fields.get('status','WAITING_DRIVER')
        r.delivery(day=day,status=status,**{k:v for k,v in fields.items() if k!='status'})
        before=database_state(r.observer)
        check(r.save(start_date=day,end_date=day).status_code==409 and database_state(r.observer)==before,'Started/signed/closed source not protected')
    return {'completed_protected':True,'signature_immutable':True,'locked_day_immutable':True,'protected_states':len(protected),'actual_mutation_backends':pids,'report_export':True}


def case_7(r):
    person=r.person();sid=person['id'];path='/api/branch-session/signers/'+str(sid)
    r.request('PATCH',path,'branch0',{'name':r.prefix+'_renamed'})
    r.request('PATCH',path,'branch0',{'active':False})
    check(r.request('GET','/api/branch-session/signers','branch0')==[],'Inactive person still selectable')
    all_rows=r.request('GET','/api/branch-session/signers?include_inactive=true','branch0')
    check(len(all_rows)==1 and all_rows[0]['active']==0,'Inactive roster unavailable')
    name=r.prefix+'_concurrent'
    def create():return r.client.post('/api/branch-session/signers',headers=r.headers['branch0'],json={'name':name})
    results,pids=r.parallel([create,create])
    check(sorted(x.status_code for x in results)==[200,409],'Concurrent duplicate signer creation not unique')
    r.delivery(status='WAITING_BRANCH',outbound_original=3,outbound_final=3)
    browser=run_dropdown(r)
    return {'crud':True,'independent_worker_backends':pids,'dropdown':browser}


def case_8(r):
    foreign=r.person(branch=1);did,_=r.delivery(status='WAITING_BRANCH',outbound_original=3,outbound_final=3)
    before=database_state(r.observer)
    check(r.request('GET','/api/branch-session/signers','branch0')==[],'Cross-branch roster leak')
    r.request('PATCH','/api/branch-session/signers/'+str(foreign['id']),'branch0',{'name':r.prefix+'_tamper'},404)
    r.request('POST','/api/branch-session/signers','branch0',{'name':r.prefix,'branch_id':r.branches[1]},400)
    check(r.sign(foreign).status_code==409,'Foreign signer was accepted')
    for role in ('admin','secretary','driver0','missing'):
        r.request('GET','/api/branch-session/signers',role,expected=401)
    check(database_state(r.observer)==before,'Rejected cross-branch request changed data')
    return {'cross_branch_read_write_denied':True,'other_roles_denied':True}


def case_9(r):
    person=r.person();did,_=r.delivery(status='WAITING_BRANCH',outbound_original=3,outbound_final=3)
    check(r.sign(person).status_code==200,'Normal signing failed')
    original=r.rows('deliveries','id=%s',(did,))[0]
    r.request('PATCH','/api/branch-session/signers/'+str(person['id']),'branch0',{'name':r.prefix+'_newname','active':False})
    check(r.rows('deliveries','id=%s',(did,))[0]==original,'Roster update rewrote historical delivery')
    r.clock+=timedelta(seconds=3)
    check(r.sign(person).status_code==200,'Signed retry not idempotent')
    check(r.rows('deliveries','id=%s',(did,))[0]==original,'Signed retry rewrote history')
    late_person=r.person(suffix='late');late,_=r.delivery(day='2030-01-03',status='LATE_BRANCH_PENDING',outbound_original=3,outbound_final=3,driver_confirmed_at=r.main.now())
    r.mutate("INSERT INTO daily_reports(service_date,status) VALUES(?,'LOCKED')",('2030-01-03',))
    check(r.sign(late_person).status_code==200,'Historical late-sign lock exception failed')
    late_row=r.rows('deliveries','id=%s',(late,))[0]
    check(late_row['status']=='STOP_COMPLETED' and late_row['branch_signed_at'],'Late signature not persisted')
    # Real correction endpoint preserves the original snapshot.
    r.update('deliveries',did,status='WAITING_BRANCH_CORRECTION')
    r.update('branch_sessions',r.main.thash(r.tokens['branch0']),delivery_id=did)
    r.mutate("INSERT INTO corrections(delivery_id,requested_by_driver_id,requested_at,fields_json,status) VALUES(?,?,?,'[]','PENDING')",(did,r.drivers[0],r.main.now()))
    payload={'signer_id':late_person['id'],'signer':late_person['name'],'signature':ink('correction'),'reason':r.prefix,'document_return':7,'inbound':None}
    r.request('POST','/api/branch-session/correct','branch0',payload)
    corrected=r.rows('deliveries','id=%s',(did,))[0]
    for key in ('signer_name','branch_signature','branch_signed_at','receipt_at','document_final','outbound_final'):
        check(corrected[key]==original[key],'Correction overwrote original signing snapshot')
    check(corrected['correction_signature']==payload['signature'] and corrected['document_return_final']==7,'Correction snapshot incorrect')
    return {'original_immutable':True,'late_sign':True,'correction_preserves_original':True}


def case_10(r):
    for codes in ((),('K13',),('K13S',),('K13','K13S')):
        with r.connection() as c:
            c.rollback()
            try:
                with c.con.transaction(force_rollback=True):
                    r.main.lock_schedule_writes(c)
                    created={}
                    for code in codes:
                        bid=c.execute('INSERT INTO branches(code,name,route_id,active,pin_hash,access_token_hash) VALUES(?,?,?,1,?,?)',(code,r.prefix+'_'+code,r.routes[0],'SYNTHETIC-HASH','SYNTHETIC-'+code)).lastrowid
                        created[code]=dict(c.execute('SELECT * FROM branches WHERE id=?',(bid,)).fetchone())
                        c.execute("INSERT INTO deliveries(service_date,branch_id,status,signer_name,branch_signature,branch_signed_at) VALUES(?,?,'STOP_COMPLETED',?,?,?)",('2030-01-02',bid,r.prefix,ink(code),r.main.now()))
                    before=[dict(x) for x in c.execute("SELECT * FROM deliveries WHERE service_date='2030-01-02' ORDER BY id")]
                    r.main.repair_shilin_identity(c);r.main.repair_shilin_identity(c)
                    after=[dict(x) for x in c.execute("SELECT * FROM deliveries WHERE service_date='2030-01-02' ORDER BY id")]
                    check(before==after,'K13 repair changed historical deliveries')
                    if codes==('K13S',):
                        row=dict(c.execute("SELECT * FROM branches WHERE code='K13'").fetchone())
                        check(row=={**created['K13S'],'code':'K13','name':'士林'},'K13S identity/credentials changed unexpectedly')
                    elif len(codes)==2:
                        old=dict(c.execute("SELECT * FROM branches WHERE code='K13S'").fetchone())
                        check(old=={**created['K13S'],'active':0},'K13 conflict changed legacy credentials/history')
                        check(dict(c.execute("SELECT * FROM branches WHERE code='K13'").fetchone())==created['K13'],'Canonical identity changed during conflict')
                    elif codes:
                        check(dict(c.execute("SELECT * FROM branches WHERE code='K13'").fetchone())==created['K13'],'Canonical K13 changed')
                    actions=[x['action'] for x in c.execute("SELECT action FROM audit_logs WHERE action IN ('REPAIR_SHILIN_CODE','SHILIN_IDENTITY_CONFLICT')")]
                    expected=['REPAIR_SHILIN_CODE'] if codes==('K13S',) else (['SHILIN_IDENTITY_CONFLICT'] if len(codes)==2 else [])
                    check(actions==expected,'K13 repair audit was missing or duplicated')
            finally:r.ledger.rollback(c.transaction)
    return {'states':4,'historical_relations_preserved':True,'force_rollback':True}


def run_dropdown(runtime):
    if not runtime.browser:raise security.Blocked('Real localhost browser dropdown check requires --browser and installed browser helper')
    import uvicorn
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    runtime.main.APP_BASE_URL=base
    server=uvicorn.Server(uvicorn.Config(runtime.application,log_level='critical',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    import time
    deadline=time.monotonic()+10
    while not server.started:
        check(thread.is_alive() and time.monotonic()<deadline,'Local test server did not start')
        time.sleep(.02)
    config={'base':base,'qr':runtime.tokens['qr0'],'session':runtime.tokens['branch0'],'name':runtime.prefix+'_browser'}
    env={'PATH':os.environ.get('PATH',''),'V194_BROWSER_CONFIG':json.dumps(config),'CHROME_PATH':runtime.chrome_path}
    try:
        process=subprocess.run(['node',str(Path(__file__).with_name('v194_pg_stage2_dropdown.mjs'))],env=env,capture_output=True,text=True,timeout=60)
        if process.returncode!=0:raise security.Blocked('Real dropdown browser unavailable or failed; details withheld')
        result=json.loads(process.stdout);check(result.get('result')=='PASS','Dropdown assertion failed')
        return result
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close()
        check(not thread.is_alive(),'Local browser test server did not stop')


def run(args):
    if not args.authorize_test_db_writes or not args.authorize_owned_cleanup:
        raise security.Blocked('Both new write and bounded-restoration grants are mandatory')
    if not args.browser or shutil.which('node') is None or not (Path.home()/'.codex/skills/design-taste-frontend/scripts/lib/cdp.mjs').is_file():
        raise security.Blocked('Full ten-case run requires the real localhost browser tooling before any connection')
    chrome_paths=[Path('/Applications')/name/'Contents/MacOS'/executable for name,executable in (('Google Chrome.app','Google Chrome'),('Chromium.app','Chromium'),('Microsoft Edge.app','Microsoft Edge'))]
    chrome=next((p for p in chrome_paths if p.is_file()),None)
    version=subprocess.run(['node','--version'],capture_output=True,text=True,check=True).stdout.strip()
    if chrome is None or int(version.lstrip('v').split('.')[0])<22:raise security.Blocked('Verified local Chrome and Node 22+ are required before database access')
    directory=Path(tempfile.mkdtemp(prefix='movingmatch-v194-functional-'))
    report={'result':'RUNNING','cases':[],'postgresql_execution':True,'next_stage_started':False}
    runtime=None
    try:
        runtime=Runtime.__new__(Runtime)
        Runtime.__init__(runtime,directory,writes=True,cleanup=True,browser=args.browser)
        runtime.chrome_path=str(chrome)
        report['baseline']=runtime.baseline
        for number in range(1,11):
            entry={'case':number,'name':CASES[number],'result':'RUNNING'};report['cases'].append(entry)
            (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            try:
                runtime.fixture(number)
                entry['evidence']=globals()['case_'+str(number)](runtime)
                entry['result']='PASS'
            except BaseException as error:
                entry['result']='BLOCKED' if isinstance(error,security.Blocked) else 'FAIL'
                entry['error_type']=type(error).__name__
                raise
            finally:
                if runtime.ledger is not None and not runtime.ledger.blocked and not runtime.ledger.uncertain:
                    runtime.restore();entry['restoration']='PASS'
                    entry['post_restoration']=database_state(runtime.observer)
                else:
                    entry['restoration']='BLOCKED; journal preserved; no automatic deletion'
        report['result']='PASS'
    except BaseException as error:
        report['result']='BLOCKED' if isinstance(error,security.Blocked) else 'FAIL'
        report['error_type']=type(error).__name__
    finally:
        with security.REGISTRY_LOCK:
            report['verified_physical_connections']=list(security.VALIDATED.values())
            report['physical_validation_failures']=len(security.VALIDATION_ERRORS)
        if runtime is not None:runtime.close()
        (directory/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'result':report['result'],'report_file':str(directory/'report.json'),'next_stage_started':False}))
    return 0 if report['result']=='PASS' else 1


def verify_restored(baseline, writer_pid):
    def proof_check(condition,label):
        if not condition:raise security.Blocked(label)
    proof_check(baseline is not None,'Original full snapshot unavailable; verification BLOCKED')
    with security.verified_connect(autocommit=True,row_factory=dict_row) as reader:
        proof=security.evidence_for(reader)
        proof_check(proof['backend_pid']!=writer_pid,'Independent restoration backend required')
        reader.execute('SET default_transaction_read_only=on')
        reader.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            proof_check(reader.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on','Server read-only evidence missing')
            final=database_state(reader)
            proof_check(final==baseline,'Restoration data/schema/index snapshot mismatch')
            proof_check(reader.execute('SELECT version FROM public.schema_migrations').fetchall()==[{'version':'v194-safe-schema-1'}],'Restoration migration mismatch')
            proof_check(all(n==0 for t,n in final['counts'].items() if t!='schema_migrations'),'Synthetic rows remain')
            return {'result':'PASS','independent_backend':True,'server_read_only':True,'snapshot_matches':True}
        finally:reader.execute('ROLLBACK')


def journal_action(path, *, restore=False):
    ledger=Ledger.load(path)
    if restore:
        if ledger.blocked or ledger.uncertain or ledger.pending:
            raise security.Blocked('In-memory ownership attestation unavailable; journal cannot authorize restoration')
        with security.verified_connect(autocommit=True,row_factory=dict_row) as writer:
            writer_pid=security.evidence_for(writer)['backend_pid']
            ScopedRestorer(ledger).restore(writer,authorized=True,validate_catalog=lambda c:check(database_state(c)['catalog_sha256']==ledger.baseline_catalog,'Recovery catalog changed'))
        # Writer is closed before creating the independent, verified reader.
        proof=verify_restored(ledger.baseline_state,writer_pid)
        print(json.dumps({'result':'PASS','mode':'scoped-restoration','verification':proof}))
        return
    print(json.dumps({'result':'BLOCKED','mode':'opaque journal inspection',
                      'pending_transactions':len(ledger.pending),'deletions':0,
                      'reason':'Exact ownership evidence was intentionally not persisted'}))


def cli(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--authorize-test-db-writes',action='store_true')
    parser.add_argument('--authorize-owned-cleanup',action='store_true')
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--inspect-journal')
    parser.add_argument('--restore-journal')
    parser.add_argument('--authorize-test-db-read',action='store_true')
    args=parser.parse_args(argv)
    if args.inspect_journal or args.restore_journal:
        if args.run or (args.inspect_journal and args.restore_journal):raise security.Blocked('Choose exactly one recovery mode')
        if args.restore_journal:
            if not args.authorize_test_db_writes or not args.authorize_owned_cleanup:raise security.Blocked('New bounded-restoration grant required')
            journal_action(args.restore_journal,restore=True)
        else:
            if not args.authorize_test_db_read or args.authorize_test_db_writes or args.authorize_owned_cleanup:raise security.Blocked('Separate read-only recovery grant required')
            journal_action(args.inspect_journal)
        return 0
    if not args.run:
        print(json.dumps({'mode':'offline plan','database_connected':False,'cases':CASES,'execution_granted':False}))
        return 0
    return run(args)


if __name__=='__main__':
    try:sys.exit(cli())
    except BaseException as error:
        if isinstance(error,SystemExit):raise
        print(json.dumps({'result':'BLOCKED','error_type':type(error).__name__,'details':'withheld'}));sys.exit(1)
