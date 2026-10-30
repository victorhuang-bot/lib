"""Test-only PostgreSQL adapter: capture RETURNING without changing commits.

Mutations are restricted to owned rows; no SQL is executed at import.
"""
import re
from tools.v194_pg_ownership import TABLES, KEYS, DIGEST, read_rows
from tools.v194_pg_security import Blocked, require, healthy


class BufferedCursor:
    def __init__(self, rows, rowcount, lastrowid=None):
        self.rows=list(rows); self.rowcount=rowcount; self.position=0; self._id=lastrowid
    def fetchone(self):
        if self.position>=len(self.rows): return None
        row=self.rows[self.position]; self.position+=1; return row
    def fetchall(self):
        rows=self.rows[self.position:]; self.position=len(self.rows); return rows
    @property
    def lastrowid(self): return self._id
    def __iter__(self):
        while (row:=self.fetchone()) is not None: yield row


def restrict_existing(statement, table, operation, keys):
    """Add an ownership predicate, including ON CONFLICT UPDATE races."""
    column=KEYS.get(table,'id')
    predicate=('"'+table+'"."'+column+'" IN ('+','.join('%s' for _ in keys)+')') if keys else 'FALSE'
    if operation in ('update','delete'):
        match=re.search(r'\bWHERE\b',statement,re.I)
        if not match: raise Blocked('Unconditional mutation prohibited')
        statement=statement[:match.start()]+' WHERE ('+statement[match.end():]+') AND ('+predicate+')'
        return statement,tuple(keys)
    conflict=re.search(r'\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\s+SET\b',statement,re.I|re.S)
    if conflict:
        tail=statement[conflict.end():]
        where=re.search(r'\bWHERE\b',tail,re.I)
        if where:
            statement=statement[:conflict.end()]+tail[:where.start()]+' WHERE ('+tail[where.end():]+') AND ('+predicate+')'
        else: statement+=' WHERE '+predicate
        return statement,tuple(keys)
    return statement,()


class TrackedAdapter:
    def __init__(self, raw, ledger, main, schema_allowed=lambda:False, on_business_lock=None):
        self.raw=raw; self.con=raw.con; self.ledger=ledger; self.main=main
        self.transaction=str(self.con.info.backend_pid); self.schema_allowed=schema_allowed; self.on_business_lock=on_business_lock

    def execute(self, statement, params=()):
        healthy()
        text=statement.strip().rstrip(';')
        if ';' in text or re.search(r'\b(DROP|TRUNCATE)\b',text,re.I):
            raise Blocked('Destructive or multiple SQL statements prohibited')
        if re.search(r'\b(setval|nextval)\s*\(',text,re.I): raise Blocked('Explicit sequence mutation prohibited')
        match=re.match(r'(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:public\.)?([a-z_][a-z_0-9]*)\b',text,re.I)
        if not match:
            if 'pg_advisory_xact_lock(194020)' in text and self.on_business_lock:
                self.on_business_lock(self.con)
            command=text.split(None,1)[0].upper()
            if command in ('CREATE','ALTER'):
                require(self.schema_allowed(),'Schema SQL not enabled for this case')
                create=re.match(r'CREATE TABLE IF NOT EXISTS ([a-z_][a-z_0-9]*)\b',text,re.I)
                alter=re.match(r'ALTER TABLE ([a-z_][a-z_0-9]*) ADD COLUMN IF NOT EXISTS\b',text,re.I)
                index=re.match(r'CREATE INDEX IF NOT EXISTS (idx_delivery_items_delivery_id|idx_email_logs_delivery_identifier) ON (delivery_items|email_logs)\b',text,re.I)
                allowed=TABLES|{'email_logs','email_recipients','schema_migrations'}
                require(bool((create and create[1] in allowed) or (alter and alter[1] in allowed) or index),'Unapproved schema operation')
            elif command not in ('SELECT','SET','BEGIN'):
                raise Blocked('Unapproved SQL command')
            if command=='SET' and re.search(r'(search_path|role|session_authorization)',text,re.I):
                raise Blocked('Session identity/schema change prohibited')
            return self.raw.execute(text,params)
        operation={'INSERT INTO':'insert','UPDATE':'update','DELETE FROM':'delete'}[' '.join(match[1].upper().split())]
        table=match[2].lower()
        if table=='schema_migrations':
            require(self.schema_allowed() and operation=='insert' and bool(re.fullmatch(r"INSERT\s+INTO\s+schema_migrations\(version\)\s+VALUES\('v194-safe-schema-1'\)\s+ON\s+CONFLICT\(version\)\s+DO\s+NOTHING",text,re.I)), 'Migration metadata mutation prohibited')
            return self.raw.execute(text,params)
        require(table in TABLES,'Mutation outside permitted business tables')
        returning=re.search(r'\bRETURNING\b(.*)$',text,re.I|re.S)
        projection=returning[1].strip() if returning else None
        if returning: text=text[:returning.start()].rstrip()
        require(projection in (None,'id','*'),'Unsupported RETURNING projection; stop instead of guessing')
        # Detect unknown/changed rows before SQL, not just after it committed.
        self.ledger.assert_inventory(table,read_rows(self.con,table),self.transaction)
        keys=self.ledger.keys(table,self.transaction)
        text,guard_params=restrict_existing(text,table,operation,keys)
        text+=' RETURNING "'+table+'".*,md5(to_jsonb("'+table+'")::text) AS "'+DIGEST+'"'
        cursor=self.con.execute(self.raw._q(text),tuple(params)+guard_params)
        rows=[dict(row) for row in cursor.fetchall()]
        self.ledger.stage(self.transaction,table,operation,rows)
        clean=[{k:v for k,v in row.items() if k!=DIGEST} for row in rows]
        if projection=='*': exposed=clean
        elif projection=='id' or (operation=='insert' and table not in KEYS): exposed=[{'id':r['id']} for r in clean]
        else: exposed=[]
        return BufferedCursor(exposed,len(rows),clean[0].get('id') if clean else None)

    def executescript(self, script):
        from app.schema import SchemaTransaction
        SchemaTransaction(self,True).executescript(script)

    def commit(self):
        # No commit is mocked or suppressed. Hold the journal lock across ACK.
        with self.ledger.lock:
            for table in TABLES:
                self.ledger.assert_inventory(table,read_rows(self.con,table),self.transaction)
            for table in ('email_logs','email_recipients'):
                require(self.con.execute('SELECT count(*) AS n FROM public.'+table).fetchone()['n']==0,'Unexpected mail data before commit')
            self.ledger.commit(self.transaction,self.con.commit)

    def rollback(self):
        self.con.rollback(); self.ledger.rollback(self.transaction)

    def close(self):
        try:
            if self.con is not None: self.rollback()
        finally:
            self.raw.close(); self.con=None
