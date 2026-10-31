"""Durable test-row ownership and explicitly authorized, bounded restoration.

No database access at import. No cleanup is performed by constructing a Ledger.
"""
import hashlib
import hmac
import secrets
import uuid
import json
import os
from pathlib import Path
import threading

import psycopg
from psycopg import sql
from tools.v194_pg_security import Blocked, CheckFailure, require, evidence_for

KEYS = {
    'sessions':'token_hash', 'branch_sessions':'token_hash', 'driver_sessions':'token_hash',
    'app_settings':'key',
}
ORDER = (
    'branch_sessions','driver_sessions','sessions','password_resets',
    'delivery_items','corrections','delivery_driver_assignments','route_segment_signatures',
    'route_handoffs','deliveries','daily_routes','daily_reports','delivery_exceptions',
    'global_closures','branch_signers','driver_activation_tokens','driver_devices',
    'branches','drivers','routes','users','app_settings','audit_logs',
)
TABLES = frozenset(ORDER)
DIGEST = '__v194_digest'
READ_ONLY_TABLES=('email_logs','email_recipients','schema_migrations')


def key_of(table, row):
    require(table in TABLES, 'Table is not a permitted business fixture')
    return row[KEYS.get(table,'id')]


def record_of(table, row):
    return {'table':table,'key':key_of(table,row),'digest':row[DIGEST]}


def read_rows(connection, table):
    require(table in TABLES, 'Unapproved inventory table')
    result=connection.execute(sql.SQL('SELECT t.*,md5(to_jsonb(t)::text) AS {} FROM public.{} t').format(sql.Identifier(DIGEST),sql.Identifier(table)))
    rows=result.fetchall()
    return [dict(row) for row in rows]


class Ledger:
    def __init__(self, path, run_id, limits=None):
        require(run_id.startswith('V194_FUNC_'), 'Invalid synthetic run identifier')
        self.path=Path(path); self.run_id=run_id; self.lock=threading.RLock()
        self.run_uuid=str(uuid.uuid4()); self._run_binding=(self.run_uuid,run_id); self._proof_key=secrets.token_bytes(32); self._proofs={}
        self.pid=os.getpid(); self.baseline_state=None; self.baseline_catalog=None; self.owned={t:{} for t in ORDER}; self.pending={}
        self.history=[]; self.uncertain=False; self.blocked=False; self.dates=set()
        self.limits={t:64 for t in ORDER}
        self.limits.update({'users':2,'drivers':2,'routes':2,'branches':4,'branch_signers':8,'sessions':2,'driver_sessions':2,'branch_sessions':2,'app_settings':2,'daily_routes':40,'deliveries':32,'global_closures':8,'daily_reports':4,'password_resets':0,'driver_devices':0,'driver_activation_tokens':0,'route_handoffs':0,'delivery_driver_assignments':0})
        self.limits.update(limits or {})
        self.save()

    @classmethod
    def load(cls, path):
        payload=json.loads(Path(path).read_text())
        require(payload.get('database')=='movingmatch_v194_test','Journal database mismatch')
        require(str(payload.get('run_id','')).startswith('V194_FUNC_'),'Unknown journal provenance')
        require(set(payload.get('owned',{}))==TABLES,'Journal table inventory mismatch')
        require(isinstance(payload.get('baseline_catalog'),str) and len(payload['baseline_catalog'])==64,'Journal catalog baseline unavailable')
        obj=cls.__new__(cls);obj.path=Path(path);obj.run_id=payload['run_id']
        obj.lock=threading.RLock();obj.pid=os.getpid();obj.baseline_catalog=payload['baseline_catalog']
        obj.baseline_state=payload.get('baseline_state');obj.owned=payload['owned'];obj.pending=payload.get('pending',{})
        obj.run_uuid=payload.get('run_uuid');obj._run_binding=None;obj._proof_key=None;obj._proofs={}
        obj.history=payload.get('history',[]);obj.uncertain=payload.get('uncertain',True)
        obj.blocked=payload.get('blocked',True);obj.dates=set();obj.limits={t:500 for t in ORDER}
        # Authentication keys/fingerprints and attestation keys never survive restart.
        # A disk journal is diagnostic evidence, never authority to delete.
        obj.blocked=True
        return obj

    def save(self):
        require(os.getpid()==self.pid, 'Ledger cannot be shared through an unguarded fork')
        payload={'run_uuid':self.run_uuid,'run_id':self.run_id,'owned':self.owned,'pending':self.pending,
                 'history':self.history,'uncertain':self.uncertain,'blocked':self.blocked,
                 'database':'movingmatch_v194_test','baseline_catalog':self.baseline_catalog,'baseline_state':self.baseline_state}
        # Persist no row keys or fingerprints: session PKs are credential hashes,
        # and full-row digests can depend on passwords/PINs. Keep all exact
        # evidence only in memory. Durable opaque receipts prove work was pending;
        # they deliberately cannot authorize deletion after a process restart.
        def redact(value):
            if isinstance(value,list):return [redact(v) for v in value]
            if isinstance(value,dict):
                if 'table' in value and 'key' in value:
                    return {'table':value['table'],'receipt':value.get('receipt','unavailable'),'operation':value.get('operation','owned')}
                return {k:redact(v) for k,v in value.items()}
            return value
        payload=redact(payload)
        payload['owned']={t:{r.get('receipt','unavailable'):redact(r) for r in rows.values()} for t,rows in self.owned.items()}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.writing')
        with temporary.open('w') as stream:
            os.chmod(temporary,0o600)
            json.dump(payload,stream,sort_keys=True)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,self.path)
        descriptor=os.open(self.path.parent,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

    def view(self, transaction):
        result={t:dict(rows) for t,rows in self.owned.items()}
        for change in self.pending.get(str(transaction),[]):
            table=change['table']; key=str(change['key'])
            if change['operation']=='delete': result[table].pop(key,None)
            else: result[table][key]={k:v for k,v in change.items() if k!='operation'}
        return result

    def keys(self, table, transaction):
        with self.lock: return [r['key'] for r in self.view(transaction)[table].values()]

    def assert_inventory(self, table, rows, transaction):
        with self.lock:
            expected=self.view(transaction)[table]
            actual={str(key_of(table,r)):r[DIGEST] for r in rows}
            if actual!={k:r['digest'] for k,r in expected.items()}:
                self.blocked=True; self.save()
                raise Blocked('Unknown or externally changed rows; no automatic cleanup')

    def accepts_new(self, table, row, transaction):
        owned=self.view(transaction)
        has=lambda parent,value:str(value) in owned[parent]
        if table in ('users','routes','drivers','branches'):
            field='username' if table=='users' else ('code' if table=='routes' else 'name')
            return str(row.get(field,'')).startswith(self.run_id)
        if table=='branch_signers': return has('branches',row.get('branch_id')) and str(row.get('name','')).startswith(self.run_id)
        parents={
            'sessions':('users','user_id'), 'password_resets':('users','user_id'),
            'branch_sessions':('branches','branch_id'), 'driver_sessions':('drivers','driver_id'),
            'driver_devices':('drivers','driver_id'), 'driver_activation_tokens':('drivers','driver_id'),
            'daily_routes':('routes','route_id'), 'deliveries':('branches','branch_id'),
            'delivery_items':('deliveries','delivery_id'), 'corrections':('deliveries','delivery_id'),
            'delivery_driver_assignments':('deliveries','delivery_id'),
            'route_handoffs':('daily_routes','daily_route_id'),
            'route_segment_signatures':('daily_routes','daily_route_id'),
            'delivery_exceptions':('branches','branch_id'),
        }
        if table in parents:
            parent,column=parents[table]
            return has(parent,row.get(column))
        if table=='global_closures': return str(row.get('reason','')).startswith(self.run_id)
        if table=='daily_reports':
            return row.get('service_date') in self.dates
        if table=='app_settings':
            key=str(row.get('key',''))
            return key.startswith('DEFAULT_ROUTE_DRIVER_') and has('routes',key.removeprefix('DEFAULT_ROUTE_DRIVER_')) and has('drivers',row.get('value'))
        if table=='audit_logs':
            entity={'DELIVERY':'deliveries','DAILY_ROUTE':'daily_routes','BRANCH':'branches','BRANCH_SIGNER':'branch_signers'}.get(row.get('entity_type'))
            if entity: return has(entity,row.get('entity_id'))
            return row.get('entity_type')=='DAY' and (has('users',row.get('actor_id')) or str(row.get('reason','')).startswith(self.run_id))
        return False

    def stage(self, transaction, table, operation, rows):
        with self.lock:
            require(not self.blocked and not self.uncertain, 'Ledger is not safe for mutation')
            for row in rows:
                key=str(key_of(table,row)); existing=key in self.view(transaction)[table]
                if operation!='insert' and not existing:
                    self.blocked=True; self.save(); raise Blocked('Mutation targeted an unowned row')
                if operation=='insert' and not existing and not self.accepts_new(table,row,transaction):
                    self.blocked=True; self.save(); raise Blocked('New row has no proven fixture ancestry')
                receipt=uuid.uuid4().hex
                self.pending.setdefault(str(transaction),[]).append({**record_of(table,row),'receipt':receipt,'operation':operation})
                self._proofs.setdefault((table,key),set()).add(self.attest(table,row))
                require(len(self.view(transaction)[table])<=self.limits[table], 'Per-table synthetic row limit exceeded')
            self.save()

    def attest(self, table, row):
        require(self._run_binding==(self.run_uuid,self.run_id) and self._proof_key is not None,'Run identity is not authenticated')
        payload=json.dumps([self.run_uuid,self.run_id,table,row],sort_keys=True,default=str).encode()
        return hmac.new(self._proof_key,payload,hashlib.sha256).digest()

    def verify_ownership(self, inventory):
        require(self._run_binding==(self.run_uuid,self.run_id),'Run UUID/binding mismatch')
        maps={t:{str(key_of(t,r)):r for r in rows} for t,rows in inventory.items()}
        links={
            'sessions':{'user_id':'users'},'password_resets':{'user_id':'users'},
            'branch_sessions':{'branch_id':'branches','delivery_id':'deliveries'},
            'driver_sessions':{'driver_id':'drivers','device_id':'driver_devices'},
            'driver_devices':{'driver_id':'drivers'},'driver_activation_tokens':{'driver_id':'drivers','created_by':'users'},
            'branches':{'route_id':'routes'},'daily_routes':{'route_id':'routes','driver_id':'drivers'},
            'deliveries':{'branch_id':'branches','daily_route_id':'daily_routes','voided_by':'users','admin_closed_by':'users'},
            'branch_signers':{'branch_id':'branches'},'delivery_items':{'delivery_id':'deliveries'},
            'corrections':{'delivery_id':'deliveries','requested_by_driver_id':'drivers'},
            'delivery_driver_assignments':{'delivery_id':'deliveries','driver_id':'drivers','handoff_id':'route_handoffs'},
            'route_handoffs':{'daily_route_id':'daily_routes','from_driver_id':'drivers','to_driver_id':'drivers','start_delivery_id':'deliveries','created_by':'users'},
            'route_segment_signatures':{'daily_route_id':'daily_routes','driver_id':'drivers'},
            'delivery_exceptions':{'branch_id':'branches','created_by':'users'},'global_closures':{'created_by':'users'},
        }
        for table,rows in inventory.items():
            self.assert_inventory(table,rows,'cleanup')
            for row in rows:
                key=str(key_of(table,row))
                require(any(hmac.compare_digest(proof,self.attest(table,row)) for proof in self._proofs.get((table,key),set())),'Row attestation mismatch')
                require(self.accepts_new(table,row,'cleanup'),'Run marker or ownership ancestry mismatch')
                for column,parent in links.get(table,{}).items():
                    if row.get(column) is not None:
                        require(str(row[column]) in maps[parent],'Foreign relation has no same-run owner')
                if table=='deliveries' and row.get('daily_route_id') is not None:
                    route=maps['daily_routes'][str(row['daily_route_id'])]
                    branch=maps['branches'][str(row['branch_id'])]
                    require(route['service_date']==row['service_date'] and route['route_id']==branch['route_id'],'Delivery relation inconsistent')
                if table=='daily_reports':
                    require(any(r.get('service_date')==row.get('service_date') for r in inventory['daily_routes']),'Report has no same-run route')

    def rollback(self, transaction):
        with self.lock:
            if self.uncertain:
                self.save()  # Preserve prepared IDs after an ambiguous COMMIT.
                return
            changes=self.pending.pop(str(transaction),[])
            if changes: self.history.append({'operation':'rollback','changes':changes})
            self.save()

    def commit(self, transaction, commit):
        with self.lock:
            require(not self.blocked and not self.uncertain, 'Commit blocked by ownership uncertainty')
            self.save()  # Durable IDs/digests before COMMIT, including API-created rows.
            try: commit()
            except BaseException:
                self.uncertain=True; self.save()
                raise Blocked('Commit outcome uncertain; read-only recovery required') from None
            self.owned=self.view(transaction)
            self.history.extend(self.pending.pop(str(transaction),[]))
            self.save()


class ScopedRestorer:
    def __init__(self, ledger): self.ledger=ledger

    def restore(self, connection, *, authorized=False, validate_catalog=None):
        if not authorized: raise Blocked('Explicit owned-row cleanup grant is required')
        if not callable(validate_catalog):raise Blocked('A catalog baseline validator is mandatory')
        evidence_for(connection)
        ledger=self.ledger
        with ledger.lock:
            if ledger.blocked or ledger.uncertain or ledger.pending:
                raise Blocked('Uncertain ownership/transaction outcome; cleanup prohibited')
            try:
                with connection.transaction():
                    connection.execute("SET LOCAL lock_timeout='5s'")
                    connection.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
                        sql.SQL(',').join(sql.SQL('public.{}').format(sql.Identifier(t)) for t in ORDER+READ_ONLY_TABLES)))
                    validate_catalog(connection)
                    for table in ('email_logs','email_recipients'):
                        require(connection.execute(sql.SQL('SELECT count(*) AS n FROM public.{}').format(sql.Identifier(table))).fetchone()['n']==0,'Unknown email data; no cleanup allowed')
                    require(connection.execute('SELECT version FROM public.schema_migrations').fetchall()==[{'version':'v194-safe-schema-1'}],'Migration version mismatch')
                    # Validate ALL rows before the first DELETE. Unknown rows abort everything.
                    try:ledger.verify_ownership({table:read_rows(connection,table) for table in ORDER})
                    except CheckFailure:
                        ledger.blocked=True;ledger.save()
                        raise Blocked('Run ownership could not be re-established; no deletion') from None
                    for table in ORDER:
                        key=KEYS.get(table,'id')
                        for item in ledger.owned[table].values():
                            statement=sql.SQL('DELETE FROM public.{} AS t WHERE t.{}=%s AND md5(to_jsonb(t)::text)=%s RETURNING t.{}').format(
                                sql.Identifier(table),sql.Identifier(key),sql.Identifier(key))
                            result=connection.execute(statement,(item['key'],item['digest']))
                            require(result.rowcount==1, 'Cleanup row changed; rollback entire cleanup')
                    for table in ORDER:
                        require(not read_rows(connection,table), 'Cleanup verification found remaining rows')
            except (psycopg.OperationalError,psycopg.InterfaceError):
                ledger.uncertain=True;ledger.save()
                raise Blocked('Restoration outcome uncertain; journal retained for read-only recovery') from None
            ledger.history.append({'operation':'restored','rows':sum(map(len,ledger.owned.values()))})
            ledger.owned={t:{} for t in ORDER}; ledger.save()
