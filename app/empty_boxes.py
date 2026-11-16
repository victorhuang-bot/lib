"""V20 empty-box ledger. No import-time I/O; all writes share schedule's lock.

UUID keys work with SQLite/PostgreSQL without changing existing identity tables.
Stocktake snapshots are immutable; balances are reconciled explicitly at stocktake.
"""
import os
import uuid
from datetime import date, timedelta
from contextlib import contextmanager
from fastapi import HTTPException, Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS empty_box_balances(
 branch_id INTEGER PRIMARY KEY REFERENCES branches(id),
 unused INTEGER NOT NULL CHECK(unused>=0), loaded INTEGER NOT NULL CHECK(loaded>=0),
 damaged INTEGER NOT NULL CHECK(damaged>=0), version INTEGER NOT NULL CHECK(version>0));
CREATE TABLE IF NOT EXISTS empty_box_stocktakes(
 id TEXT PRIMARY KEY NOT NULL, branch_id INTEGER NOT NULL REFERENCES branches(id),
 unused INTEGER NOT NULL CHECK(unused>=0), loaded INTEGER NOT NULL CHECK(loaded>=0),
 damaged INTEGER NOT NULL CHECK(damaged>=0), actor TEXT NOT NULL, created_at TEXT NOT NULL,
 note TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), UNIQUE(branch_id,version));
CREATE TABLE IF NOT EXISTS empty_box_requests(
 id TEXT PRIMARY KEY NOT NULL, branch_id INTEGER NOT NULL REFERENCES branches(id),
 quantity INTEGER NOT NULL CHECK(quantity>0), fulfilled INTEGER NOT NULL DEFAULT 0 CHECK(fulfilled>=0 AND fulfilled<=quantity),
 status TEXT NOT NULL CHECK(status IN ('OPEN','COMPLETED','CANCELLED')),
 created_at TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS empty_box_transfers(
 id TEXT PRIMARY KEY NOT NULL, request_id TEXT NOT NULL REFERENCES empty_box_requests(id),
 branch_id INTEGER NOT NULL REFERENCES branches(id), quantity INTEGER NOT NULL CHECK(quantity>0),
 planned_date TEXT NOT NULL, route_id INTEGER NOT NULL REFERENCES routes(id),
 actual_date TEXT, status TEXT NOT NULL CHECK(status IN ('PLANNED','IN_TRANSIT','COMPLETED','CANCELLED')),
 created_at TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS empty_box_transfer_sources(
 id TEXT PRIMARY KEY NOT NULL, transfer_id TEXT NOT NULL REFERENCES empty_box_transfers(id),
 branch_id INTEGER NOT NULL REFERENCES branches(id), quantity INTEGER NOT NULL CHECK(quantity>0),
 planned_date TEXT NOT NULL, route_id INTEGER NOT NULL REFERENCES routes(id), actual_date TEXT,
 UNIQUE(transfer_id,branch_id));
CREATE TABLE IF NOT EXISTS empty_box_handoffs(
 id TEXT PRIMARY KEY NOT NULL, event_key TEXT NOT NULL UNIQUE, transfer_id TEXT NOT NULL REFERENCES empty_box_transfers(id),
 source_id TEXT UNIQUE REFERENCES empty_box_transfer_sources(id), branch_id INTEGER NOT NULL REFERENCES branches(id),
 kind TEXT NOT NULL CHECK(kind IN ('PICKUP','RECEIVE')), quantity INTEGER NOT NULL CHECK(quantity>=0),
 actual_date TEXT NOT NULL, confirmed_at TEXT NOT NULL, actor TEXT NOT NULL, signature TEXT NOT NULL,
 reason TEXT NOT NULL, delivery_id INTEGER REFERENCES deliveries(id),
 CHECK((kind='PICKUP' AND source_id IS NOT NULL) OR (kind='RECEIVE' AND source_id IS NULL)));
CREATE TABLE IF NOT EXISTS empty_box_movements(
 event_key TEXT PRIMARY KEY NOT NULL REFERENCES empty_box_handoffs(event_key), branch_id INTEGER NOT NULL REFERENCES branches(id),
 delta INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS empty_box_reminders(
 id TEXT PRIMARY KEY NOT NULL, branch_id INTEGER NOT NULL REFERENCES branches(id),
 actor TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS empty_box_commands(
 event_key TEXT PRIMARY KEY NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL, result_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS eb_stocktake_branch ON empty_box_stocktakes(branch_id,created_at);
CREATE INDEX IF NOT EXISTS eb_request_branch ON empty_box_requests(branch_id,status);
CREATE INDEX IF NOT EXISTS eb_transfer_request ON empty_box_transfers(request_id,status);
CREATE INDEX IF NOT EXISTS eb_source_branch ON empty_box_transfer_sources(branch_id,planned_date);
CREATE INDEX IF NOT EXISTS eb_handoff_branch ON empty_box_handoffs(branch_id,confirmed_at);
CREATE INDEX IF NOT EXISTS eb_movement_branch ON empty_box_movements(branch_id,created_at);
CREATE INDEX IF NOT EXISTS eb_reminder_branch ON empty_box_reminders(branch_id,created_at);
"""


def migrate(c):
    # Explicit invocation only; existing tables/rows are never changed.
    c.executescript(SCHEMA)


def validate_schema(c):
    # Read-only feature preflight. A deployment never applies migration implicitly.
    import re
    for table,definition in re.findall(r'CREATE TABLE IF NOT EXISTS (empty_box_\w+)\((.*?)\);',SCHEMA,re.S):
        # Selecting the full row asks PostgreSQL/SQLite to resolve the table without reading data.
        c.execute('SELECT * FROM '+table+' WHERE 1=0')


def enabled():
    return os.getenv('MOVINGMATCH_V20_ENABLED', '') == '1'


def integer(value, minimum=0):
    if type(value) is not int or value < minimum or value > 1000000:
        raise HTTPException(400, '箱數必須是有效整數')
    return value


def day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(400, '日期格式須為 YYYY-MM-DD')
    return value


def text(value, required=False):
    if not isinstance(value, str) or len(value) > 1000 or (required and not value.strip()):
        raise HTTPException(400, '請填寫有效文字')
    return value.strip()


def uid():
    return uuid.uuid4().hex


class Ledger:
    def __init__(self, application):
        self.m = application

    @contextmanager
    def transaction(self, write=False):
        if not enabled():
            raise HTTPException(503, 'V20 尚未啟用')
        c = self.m.db()
        try:
            if write:
                self.m.lock_schedule_writes(c)
            yield c
            if write:
                c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def branch(self, c, bid):
        b = c.execute('SELECT * FROM branches WHERE id=? AND active=1', (bid,)).fetchone()
        if not b or not b['route_id']:
            raise HTTPException(409, '分館已停用或尚無路線')
        return b

    def reserved(self, c, bid, exclude=None):
        return c.execute("""SELECT COALESCE(SUM(s.quantity),0) n FROM empty_box_transfer_sources s
          JOIN empty_box_transfers t ON t.id=s.transfer_id
          WHERE s.branch_id=? AND t.status IN ('PLANNED','IN_TRANSIT') AND s.actual_date IS NULL
          AND (CAST(? AS TEXT) IS NULL OR s.id<>?)""", (bid, exclude, exclude)).fetchone()['n']

    def balance(self, c, bid):
        row = c.execute('SELECT * FROM empty_box_balances WHERE branch_id=?', (bid,)).fetchone()
        return dict(row) if row else None

    def command(self, c, actor, operation, payload, create):
        import json
        key = text(payload.get('event_key'), True)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        key = f'{actor}:{key}'
        old = c.execute('SELECT * FROM empty_box_commands WHERE event_key=?', (key,)).fetchone()
        if old:
            if old['operation'] != operation or old['payload'] != canonical:
                raise HTTPException(409, '重複請求內容不一致')
            return {'ok': True, 'id': old['result_id'], 'already_saved': True}
        ident = create()
        c.execute('INSERT INTO empty_box_commands VALUES(?,?,?,?)', (key, operation, canonical, ident))
        self.m.audit(c, 'V20', actor, operation, operation, 'EMPTY_BOX', ident, after=payload)
        return {'ok': True, 'id': ident}

    def stocktake(self, c, bid, p):
        self.branch(c, bid)
        unused, loaded, damaged = [integer(p.get(k)) for k in ('unused','loaded','damaged')]
        actor = text(p.get('actor'), True)
        old = self.balance(c, bid)
        version = integer(p.get('version'))
        if version != (old['version'] if old else 0):
            raise HTTPException(409, '庫存已更新，請重新載入盤點')
        if unused < self.reserved(c, bid):
            raise HTTPException(409, '盤點數量不足已保留調度，請先取消或重新安排')
        ident = uid()
        c.execute('INSERT INTO empty_box_stocktakes VALUES(?,?,?,?,?,?,?,?,?)',
                  (ident,bid,unused,loaded,damaged,actor,self.m.now(),text(p.get('note','')),version+1))
        c.execute('''INSERT INTO empty_box_balances VALUES(?,?,?,?,?) ON CONFLICT(branch_id)
          DO UPDATE SET unused=excluded.unused,loaded=excluded.loaded,damaged=excluded.damaged,version=excluded.version''',
                  (bid,unused,loaded,damaged,version+1))
        return ident

    def request(self, c, bid, p):
        self.branch(c, bid)
        ident = uid()
        c.execute('INSERT INTO empty_box_requests VALUES(?,?,?,0,?,?,?,?)',
                  (ident,bid,integer(p.get('quantity'),1),'OPEN',self.m.now(),text(p.get('actor'),True),text(p.get('note',''))))
        return ident

    def allocated(self, c, rid):
        return c.execute("""SELECT COALESCE(SUM(COALESCE(h.quantity,s.quantity)),0) n
            FROM empty_box_transfers t JOIN empty_box_transfer_sources s ON s.transfer_id=t.id
            LEFT JOIN empty_box_handoffs h ON h.source_id=s.id
            WHERE t.request_id=? AND t.status IN ('PLANNED','IN_TRANSIT')""", (rid,)).fetchone()['n']

    def transfer(self, c, actor, p):
        r = c.execute("SELECT * FROM empty_box_requests WHERE id=? AND status='OPEN'", (p.get('request_id'),)).fetchone()
        if not r:
            raise HTTPException(409, '申請已完成、取消或不存在')
        sources = p.get('sources')
        if not isinstance(sources,list) or not sources or len(sources)>100:
            raise HTTPException(400, '請指定收取館')
        destination = self.branch(c, r['branch_id'])
        arrival = day(p.get('planned_date'))
        if arrival < self.m.today():
            raise HTTPException(400, '不可排定過去日期')
        seen = set()
        clean = []
        for s in sources:
            if not isinstance(s,dict):
                raise HTTPException(400, '來源格式錯誤')
            bid = integer(s.get('branch_id'),1)
            if bid in seen or bid == r['branch_id']:
                raise HTTPException(400, '來源重複或與需求館相同')
            seen.add(bid)
            b = self.branch(c, bid)
            qty = integer(s.get('quantity'),1)
            planned = day(s.get('planned_date'))
            if planned < self.m.today() or planned > arrival:
                raise HTTPException(400, '收取日期須介於今日與送達日之間')
            stock = self.balance(c,bid)
            if not stock or stock['unused']-self.reserved(c,bid) < qty:
                raise HTTPException(409, '來源可調出量不足，請重新盤點或調整')
            clean.append((uid(),bid,qty,planned,b['route_id']))
        quantity = sum(s[2] for s in clean)
        if quantity > r['quantity']-r['fulfilled']-self.allocated(c,r['id']):
            raise HTTPException(409, '超過申請未分配數量')
        ident = uid()
        c.execute('INSERT INTO empty_box_transfers VALUES(?,?,?,?,?,?,NULL,?,?,?,?)',
                  (ident,r['id'],r['branch_id'],quantity,arrival,destination['route_id'],'PLANNED',self.m.now(),actor,text(p.get('reason',''))))
        for sid,bid,qty,planned,route in clean:
            c.execute('INSERT INTO empty_box_transfer_sources VALUES(?,?,?,?,?,?,NULL)', (sid,ident,bid,qty,planned,route))
        return ident

    def needs_replan(self, c, branch_id, planned, route):
        b = c.execute('SELECT * FROM branches WHERE id=?', (branch_id,)).fetchone()
        return not b or not b['active'] or b['route_id'] != route or not self.m.branch_expected_on(c,b,planned)

    def task_rows(self, c, bid=None):
        result=[]
        for row in c.execute("SELECT * FROM empty_box_transfers WHERE status IN ('PLANNED','IN_TRANSIT') ORDER BY planned_date,id").fetchall():
            t = dict(row)
            sources = [dict(s) for s in c.execute('SELECT * FROM empty_box_transfer_sources WHERE transfer_id=? ORDER BY id',(t['id'],)).fetchall()]
            invalid = self.needs_replan(c,t['branch_id'],t['planned_date'],t['route_id']) or any(
                not s['actual_date'] and self.needs_replan(c,s['branch_id'],s['planned_date'],s['route_id']) for s in sources)
            # Overdue plans require a deliberate new schedule; early receipt remains possible.
            invalid = invalid or (t['planned_date'] < self.m.today()) or any(not s['actual_date'] and s['planned_date'] < self.m.today() for s in sources)
            t['needs_replan'] = bool(invalid)
            t['sources'] = sources
            t['in_transit'] = c.execute("SELECT COALESCE(SUM(quantity),0) n FROM empty_box_handoffs WHERE transfer_id=? AND kind='PICKUP'",(t['id'],)).fetchone()['n']
            t['storage'] = '在途／倉庫暫存' if t['in_transit'] else '待收取'
            if bid is None:
                result.append(t)
                continue
            # Branch sees its own task only, never other branches' inventory or identities.
            if t['branch_id']==bid:
                result.append({k:v for k,v in t.items() if k not in ('sources','actor','reason','route_id')} | {'kind':'RECEIVE','task_id':t['id'],'ready':all(s['actual_date'] for s in sources)})
            for s in sources:
                if s['branch_id']==bid and not s['actual_date']:
                    result.append({'task_id':s['id'],'kind':'PICKUP','transfer_id':t['id'], 'planned_date':s['planned_date'],'quantity':s['quantity'],'needs_replan':bool(invalid)})
        return result

    def handoff(self, c, sess, p, identity=None, delivery=None):
        bid = sess['branch_id']
        self.branch(c,bid)
        if not isinstance(p,dict):
            raise HTTPException(400, '交接格式錯誤')
        kind = p.get('kind')
        if kind not in ('PICKUP','RECEIVE'):
            raise HTTPException(400, '交接類型錯誤')
        task_id = text(p.get('task_id'),True)
        source = None
        if kind=='PICKUP':
            source = c.execute('SELECT * FROM empty_box_transfer_sources WHERE id=? AND branch_id=?',(task_id,bid)).fetchone()
            tid = source['transfer_id'] if source else None
        else:
            tid = task_id
        t = c.execute('SELECT * FROM empty_box_transfers WHERE id=?',(tid,)).fetchone() if tid else None
        if not t or (kind=='RECEIVE' and t['branch_id'] != bid):
            raise HTTPException(404, '找不到本站交接任務')
        key = f'{kind}:{task_id}'
        old = c.execute('SELECT * FROM empty_box_handoffs WHERE event_key=?',(key,)).fetchone()
        quantity = integer(p.get('quantity'))
        actual = day(p.get('actual_date'))
        reason = text(p.get('reason',''))
        if actual > self.m.today():
            raise HTTPException(400, '實際交接日期不可在未來')
        if actual < t['created_at'][:10]:
            raise HTTPException(400, '實際交接日期不可早於調度建立日')
        if old:
            if (old['quantity'],old['actual_date'],old['reason']) != (quantity,actual,reason):
                raise HTTPException(409, '交接已確認，重送內容不同')
            return old['id']
        if t['status'] in ('CANCELLED','COMPLETED'):
            raise HTTPException(409, '調度已結束')
        # A historical supplemental signature checks the actual handoff calendar,
        # not today's calendar. A closure on the planned day still needs replanning.
        sites = [(t['branch_id'],t['planned_date'],t['route_id'])]
        sources = c.execute('SELECT * FROM empty_box_transfer_sources WHERE transfer_id=?',(tid,)).fetchall()
        sites += [(s['branch_id'],s['planned_date'],s['route_id']) for s in sources if not s['actual_date']]
        if any(self.needs_replan(c,*site) for site in sites):
            raise HTTPException(409, '班表或路線已變更，待總館重新安排')
        planned_qty = source['quantity'] if source else t['quantity']
        if quantity != planned_qty and not reason:
            raise HTTPException(400, '實際與預定數量不同，請填寫差異原因')
        who, signature = identity or self.m._selected_signing_identity(c,sess,p)
        if delivery is not None and (delivery['branch_id']!=bid or delivery['status']=='VOID'):
            raise HTTPException(409, '配送關聯錯誤')
        stock = self.balance(c,bid)
        if kind=='PICKUP':
            if not stock or stock['unused']-self.reserved(c,bid,source['id']) < quantity:
                raise HTTPException(409, '庫存不足或已保留給其他調度')
            r=c.execute('SELECT * FROM empty_box_requests WHERE id=?',(t['request_id'],)).fetchone()
            if quantity-source['quantity'] > r['quantity']-r['fulfilled']-self.allocated(c,r['id']):
                raise HTTPException(409, '超額收取超過申請尚可補足數量')
            delta=-quantity
            c.execute('UPDATE empty_box_transfer_sources SET actual_date=? WHERE id=?',(actual,source['id']))
            c.execute("UPDATE empty_box_transfers SET status='IN_TRANSIT' WHERE id=?",(tid,))
        else:
            if any(not s['actual_date'] for s in sources):
                raise HTTPException(409, '來源尚未全數確認交回')
            handoffs = c.execute("SELECT quantity,actual_date FROM empty_box_handoffs WHERE transfer_id=? AND kind='PICKUP'",(tid,)).fetchall()
            collected=sum(h['quantity'] for h in handoffs)
            if quantity != collected:
                raise HTTPException(409, '收到量須與已交回量一致，差異請先由總館處理')
            if any(actual < h['actual_date'] for h in handoffs):
                raise HTTPException(400, '送達日不可早於收取日')
            r=c.execute('SELECT * FROM empty_box_requests WHERE id=?',(t['request_id'],)).fetchone()
            if quantity > r['quantity']-r['fulfilled']-self.allocated(c,r['id'])+collected:
                raise HTTPException(409, '超過申請尚可補足數量')
            delta=quantity
            c.execute("UPDATE empty_box_transfers SET status='COMPLETED',actual_date=? WHERE id=?",(actual,tid))
            fulfilled=r['fulfilled']+quantity
            c.execute('UPDATE empty_box_requests SET fulfilled=?,status=? WHERE id=?', (fulfilled,'COMPLETED' if fulfilled==r['quantity'] else 'OPEN',r['id']))
        if stock:
            c.execute('UPDATE empty_box_balances SET unused=unused+?,version=version+1 WHERE branch_id=?',(delta,bid))
        elif delta>=0:
            # An uncounted branch can receive boxes; coverage still comes only from snapshots.
            c.execute('INSERT INTO empty_box_balances VALUES(?,?,0,0,1)',(bid,delta))
        ident=uid()
        c.execute('INSERT INTO empty_box_handoffs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (ident,key,tid,source['id'] if source else None,bid,kind,quantity,actual,self.m.now(),who,signature,reason,delivery['id'] if delivery else None))
        c.execute('INSERT INTO empty_box_movements VALUES(?,?,?,?)',(key,bid,delta,self.m.now()))
        self.m.audit(c,'BRANCH',bid,'BRANCH','EMPTY_BOX_HANDOFF','EMPTY_BOX',ident,after={'quantity':quantity,'actual_date':actual,'kind':kind})
        return ident

    def overview(self,c,bid=None):
        tasks=self.task_rows(c,bid)
        if bid is not None:
            self.branch(c,bid)
            return {'balance':self.balance(c,bid),'stocktakes':[dict(r) for r in c.execute('SELECT * FROM empty_box_stocktakes WHERE branch_id=? ORDER BY version DESC',(bid,)).fetchall()],
                'requests':[dict(r)|{'remaining':r['quantity']-r['fulfilled']} for r in c.execute('SELECT * FROM empty_box_requests WHERE branch_id=? ORDER BY created_at DESC',(bid,)).fetchall()],
                'tasks':tasks,'reminders':[dict(r) for r in c.execute('SELECT * FROM empty_box_reminders WHERE branch_id=? ORDER BY created_at DESC',(bid,)).fetchall()],
                'handoffs':[dict(r) for r in c.execute('SELECT id,kind,quantity,actual_date,confirmed_at,actor,reason,delivery_id FROM empty_box_handoffs WHERE branch_id=? ORDER BY confirmed_at DESC',(bid,)).fetchall()]}
        branches=[]
        cutoff=(date.fromisoformat(self.m.today())-timedelta(days=30)).isoformat()
        for b in c.execute('SELECT id,name,route_id FROM branches WHERE active=1 ORDER BY code').fetchall():
            snapshot=c.execute('SELECT * FROM empty_box_stocktakes WHERE branch_id=? ORDER BY version DESC LIMIT 1',(b['id'],)).fetchone()
            stock=self.balance(c,b['id'])
            branches.append(dict(b)|{'stocktake':dict(snapshot) if snapshot else None,'balance':stock,'available':stock['unused']-self.reserved(c,b['id']) if stock else 0,'overdue':not snapshot or snapshot['created_at'][:10]<cutoff})
        requests=[dict(r)|{'remaining':r['quantity']-r['fulfilled'],'allocated':self.allocated(c,r['id'])} for r in c.execute("SELECT r.*,b.name branch_name FROM empty_box_requests r JOIN branches b ON b.id=r.branch_id WHERE r.status='OPEN' AND r.fulfilled<r.quantity ORDER BY r.created_at").fetchall()]
        counted=[b for b in branches if b['stocktake']]
        return {'branches':branches,'requests':requests,'tasks':tasks,'counted':len(counted),'total_branches':len(branches),
                'unused_snapshot_total':sum(b['stocktake']['unused'] for b in counted), 'shortage_branches':len({r['branch_id'] for r in requests})}

    def suggestions(self,c,bid,start):
        b=self.branch(c,bid)
        start=day(start)
        return [(date.fromisoformat(start)+timedelta(days=i)).isoformat() for i in range(60)
                if self.m.branch_expected_on(c,b,(date.fromisoformat(start)+timedelta(days=i)).isoformat())][:8]

    def driver_note(self,c,bid,service_date):
        if not enabled(): return ''
        notes=[]
        for t in self.task_rows(c,bid):
            if t['planned_date']==service_date:
                action='交回' if t['kind']=='PICKUP' else '收到'
                notes.append(f"空箱{action} {t['quantity']} 箱"+('（待重新安排）' if t['needs_replan'] else ''))
        return '；'.join(notes)


def install(m):
    ledger=Ledger(m)
    app=m.app

    def user(req): return m.require_user(req,['ADMIN','SECRETARY','SECRETARIAT'])

    @app.get('/api/empty-boxes/config')
    def config(): return {'enabled':enabled()}

    @app.get('/api/branch-session/empty-boxes')
    def branch_state(req:Request):
        s=m.branch_auth(req)
        with ledger.transaction() as c: return ledger.overview(c,s['branch_id'])

    @app.post('/api/branch-session/empty-boxes/{operation}')
    async def branch_write(operation:str,req:Request):
        s=m.branch_auth(req); p=await req.json()
        if not isinstance(p,dict): raise HTTPException(400,'請使用 JSON 物件')
        with ledger.transaction(True) as c:
            ledger.branch(c,s['branch_id'])
            if operation=='stocktakes':
                return ledger.command(c,f"branch:{s['branch_id']}",operation,p,lambda:ledger.stocktake(c,s['branch_id'],p))
            if operation=='requests':
                return ledger.command(c,f"branch:{s['branch_id']}",operation,p,lambda:ledger.request(c,s['branch_id'],p))
            if operation=='handoffs':
                return {'ok':True,'id':ledger.handoff(c,s,p)}
            if operation=='cancel-request':
                r=c.execute('SELECT * FROM empty_box_requests WHERE id=? AND branch_id=?',(p.get('id'),s['branch_id'])).fetchone()
                if not r: raise HTTPException(404,'找不到本站申請')
                if ledger.allocated(c,r['id']): raise HTTPException(409,'仍有調度，請先由總館取消調度')
                if r['status']=='COMPLETED': raise HTTPException(409,'已完成申請不可取消')
                c.execute("UPDATE empty_box_requests SET status='CANCELLED' WHERE id=?",(r['id'],))
                m.audit(c,'BRANCH',s['branch_id'],'BRANCH','EMPTY_BOX_CANCEL_REQUEST','EMPTY_BOX',r['id'])
                return {'ok':True}
            raise HTTPException(404,'未知操作')

    @app.get('/api/empty-boxes')
    def office_state(req:Request):
        user(req)
        with ledger.transaction() as c: return ledger.overview(c)

    @app.get('/api/empty-boxes/suggestions')
    def suggestions(req:Request,branch_id:int,start:str):
        user(req)
        with ledger.transaction() as c: return {'dates':ledger.suggestions(c,branch_id,start)}

    @app.post('/api/empty-boxes/{operation}')
    async def office_write(operation:str,req:Request):
        u=user(req); p=await req.json(); actor=f"user:{u['id']}"
        if not isinstance(p,dict): raise HTTPException(400,'請使用 JSON 物件')
        with ledger.transaction(True) as c:
            if operation=='transfers':
                return ledger.command(c,actor,operation,p,lambda:ledger.transfer(c,actor,p))
            if operation=='reminders':
                bid=integer(p.get('branch_id'),1)
                b=next((b for b in ledger.overview(c)['branches'] if b['id']==bid),None)
                if not b or not b['overdue']: raise HTTPException(409,'僅可提醒未盤點或逾期盤點館')
                ident=uid()
                c.execute('INSERT INTO empty_box_reminders VALUES(?,?,?,?,?)',(ident,bid,actor,m.now(),text(p.get('note','請更新空箱盤點'))))
                m.audit(c,'USER',u['id'],u['role'],'EMPTY_BOX_REMINDER','EMPTY_BOX',ident,after=p)
                return {'ok':True,'id':ident}
            if operation in ('cancel-transfer','reschedule'):
                t=c.execute('SELECT * FROM empty_box_transfers WHERE id=?',(p.get('id'),)).fetchone()
                if not t or t['status'] in ('COMPLETED','CANCELLED'): raise HTTPException(409,'調度已結束或不存在')
                reason=text(p.get('reason'),True)
                sources=c.execute('SELECT * FROM empty_box_transfer_sources WHERE transfer_id=?',(t['id'],)).fetchall()
                if operation=='cancel-transfer':
                    if any(s['actual_date'] for s in sources): raise HTTPException(409,'已收取空箱不可取消，請重新安排送達')
                    c.execute("UPDATE empty_box_transfers SET status='CANCELLED' WHERE id=?",(t['id'],))
                else:
                    planned=day(p.get('planned_date'))
                    if planned<m.today(): raise HTTPException(400,'不可排定過去日期')
                    b=ledger.branch(c,t['branch_id'])
                    if not m.branch_expected_on(c,b,planned): raise HTTPException(409,'送達日無有效班表')
                    c.execute('UPDATE empty_box_transfers SET planned_date=?,route_id=?,reason=? WHERE id=?',(planned,b['route_id'],reason,t['id']))
                    dates=p.get('source_dates',{})
                    if not isinstance(dates,dict): raise HTTPException(400,'來源日期格式錯誤')
                    for s in sources:
                        if s['actual_date']: continue
                        d=day(dates.get(s['id'],s['planned_date']))
                        b=ledger.branch(c,s['branch_id'])
                        if d<m.today() or d>planned or not m.branch_expected_on(c,b,d): raise HTTPException(409,'收取日無有效班表')
                        c.execute('UPDATE empty_box_transfer_sources SET planned_date=?,route_id=? WHERE id=?',(d,b['route_id'],s['id']))
                m.audit(c,'USER',u['id'],u['role'],'EMPTY_BOX_'+operation.upper(),'EMPTY_BOX',t['id'],before=dict(t),after=p,reason=reason)
                return {'ok':True}
            raise HTTPException(404,'未知操作')
    return ledger
