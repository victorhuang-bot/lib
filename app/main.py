from fastapi import FastAPI, Request, Response, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import sqlite3, os, secrets, hashlib, hmac, base64, io, json, asyncio, csv
from datetime import datetime, timedelta, date
import qrcode
from openpyxl import Workbook
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv('DATA_DIR', str(BASE / 'data')))
DB = DATA_DIR / 'app.db'
APP_ENV = os.getenv('APP_ENV','development').lower()
APP_BASE_URL = os.getenv('APP_BASE_URL','').rstrip('/')
DEMO_RESET_LINKS = os.getenv('DEMO_RESET_LINKS','false').lower() == 'true'
DEMO_ACTIVE_BRANCHES = int(os.getenv('DEMO_ACTIVE_BRANCHES','3'))

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME','moving')
SECRETARY_USERNAME = os.getenv('SECRETARY_USERNAME','lib')
RESET_EMAIL = os.getenv('RESET_EMAIL','lib@moving-match.com')

def initial_secret(name, development_default):
    value=os.getenv(name)
    if value:
        return value
    if APP_ENV != 'production':
        return development_default
    raise RuntimeError(f'Missing required production environment variable: {name}')

STATIC = BASE / 'app' / 'static'
app = FastAPI(title='圖書物流配送暨電子簽收管理系統 MVP')
app.mount('/static', StaticFiles(directory=STATIC), name='static')

subscribers = set()

def now(): return datetime.now().isoformat(timespec='seconds')
def today(): return date.today().isoformat()
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(DB, timeout=30); con.row_factory=sqlite3.Row; con.execute('PRAGMA foreign_keys=ON'); con.execute('PRAGMA journal_mode=WAL'); return con

def public_base_url(req: Request):
    if APP_BASE_URL:
        return APP_BASE_URL
    proto=req.headers.get('x-forwarded-proto') or req.url.scheme
    host=req.headers.get('x-forwarded-host') or req.headers.get('host')
    if host:
        return f'{proto}://{host}'.rstrip('/')
    return str(req.base_url).rstrip('/')

def hash_secret(s, salt=None):
    salt = salt or secrets.token_bytes(16)
    dig = hashlib.pbkdf2_hmac('sha256', s.encode(), salt, 120000)
    return base64.b64encode(salt).decode()+'$'+base64.b64encode(dig).decode()
def verify_secret(s, stored):
    try:
        a,b=stored.split('$'); salt=base64.b64decode(a); exp=base64.b64decode(b)
        got=hashlib.pbkdf2_hmac('sha256', s.encode(), salt, 120000)
        return hmac.compare_digest(exp,got)
    except: return False
def thash(s): return hashlib.sha256(s.encode()).hexdigest()

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT, email TEXT, is_active INTEGER DEFAULT 1, failed_count INTEGER DEFAULT 0, locked_until TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS password_resets(id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, expires_at TEXT, used_at TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS routes(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1, pin_hash TEXT);
    CREATE TABLE IF NOT EXISTS driver_devices(id INTEGER PRIMARY KEY, driver_id INTEGER, device_key TEXT UNIQUE, activated_at TEXT, revoked_at TEXT, last_seen_at TEXT);
    CREATE TABLE IF NOT EXISTS driver_activation_tokens(id INTEGER PRIMARY KEY, driver_id INTEGER, token_hash TEXT UNIQUE, created_at TEXT, expires_at TEXT, used_at TEXT, revoked_at TEXT, created_by INTEGER);
    CREATE TABLE IF NOT EXISTS branches(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, route_id INTEGER, stop_order INTEGER, active INTEGER DEFAULT 1, pin_hash TEXT, access_token_hash TEXT UNIQUE, qr_created_at TEXT, address TEXT DEFAULT '', phone TEXT DEFAULT '', contact_name TEXT DEFAULT '', contact_info TEXT DEFAULT '', delivery_weekdays TEXT DEFAULT '1,2,3,4,5', delivery_frequency TEXT DEFAULT '每週固定');
    CREATE TABLE IF NOT EXISTS daily_routes(id INTEGER PRIMARY KEY, service_date TEXT, route_id INTEGER, driver_id INTEGER, status TEXT DEFAULT 'ACTIVE', driver_signature TEXT, driver_signed_at TEXT, UNIQUE(service_date,route_id));
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, service_date TEXT, daily_route_id INTEGER, branch_id INTEGER, status TEXT DEFAULT 'WAITING_SECRETARY', document_original INTEGER, document_final INTEGER, outbound_original INTEGER, outbound_final INTEGER, inbound_final INTEGER, note_final TEXT, signer_name TEXT, branch_signed_at TEXT, branch_signature TEXT, correction_signature TEXT, correction_signer_name TEXT, correction_reason TEXT, corrected_at TEXT, driver_confirmed_at TEXT, row_version INTEGER DEFAULT 1, UNIQUE(service_date,branch_id));
    CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, delivery_id INTEGER, requested_by_driver_id INTEGER, requested_at TEXT, fields_json TEXT, driver_note TEXT, status TEXT, resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, actor_type TEXT, actor_id TEXT, role TEXT, action TEXT, entity_type TEXT, entity_id TEXT, before_json TEXT, after_json TEXT, reason TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS branch_sessions(token_hash TEXT PRIMARY KEY, branch_id INTEGER, delivery_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS driver_sessions(token_hash TEXT PRIMARY KEY, driver_id INTEGER, device_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS daily_reports(id INTEGER PRIMARY KEY, service_date TEXT UNIQUE, secretary_signature TEXT, secretary_signed_at TEXT, status TEXT DEFAULT 'OPEN', locked_at TEXT);
    CREATE TABLE IF NOT EXISTS email_recipients(id INTEGER PRIMARY KEY, email TEXT, recipient_type TEXT DEFAULT 'TO', active INTEGER DEFAULT 1, created_at TEXT);
    CREATE TABLE IF NOT EXISTS email_logs(id INTEGER PRIMARY KEY, report_type TEXT, period TEXT, recipients TEXT, status TEXT, error_message TEXT, sent_at TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT);
    ''')
    if not c.execute('SELECT 1 FROM users').fetchone():
        admin_password=initial_secret('ADMIN_INITIAL_PASSWORD','85017306')
        secretary_password=initial_secret('SECRETARY_INITIAL_PASSWORD','03751080')
        branch_pin=initial_secret('DEMO_BRANCH_PIN','1234')
        c.execute('INSERT INTO users(username,password_hash,role,email) VALUES(?,?,?,?)',(ADMIN_USERNAME,hash_secret(admin_password),'ADMIN',RESET_EMAIL))
        c.execute('INSERT INTO users(username,password_hash,role,email) VALUES(?,?,?,?)',(SECRETARY_USERNAME,hash_secret(secretary_password),'SECRETARY',RESET_EMAIL))
        for i,code in enumerate('ABCDEF',1): c.execute('INSERT INTO routes(id,code,name) VALUES(?,?,?)',(i,code,f'{code}線'))
        for n in ['王先生','李先生','陳先生','林先生','張先生']: c.execute('INSERT INTO drivers(name) VALUES(?)',(n,))
        names=['松山分館','民生分館','中山分館','北投分館','石牌分館','大安分館','士林分館','萬華分館','信義分館','內湖分館','南港分館','文山分館']
        for i in range(1,81):
            name=names[i-1] if i<=len(names) else f'示範分館{i:02d}'
            route=((i-1)%6)+1; order=((i-1)//6)+1; tok=secrets.token_urlsafe(24)
            active=1 if i<=DEMO_ACTIVE_BRANCHES else 0
            c.execute('INSERT INTO branches(code,name,route_id,stop_order,active,pin_hash,access_token_hash,qr_created_at) VALUES(?,?,?,?,?,?,?,?)',(f'B{i:03d}',name,route,order,active,hash_secret(branch_pin),thash(tok),now()))
            # raw bootstrap token only for demo retrieval is derived by rotation endpoint; initial QR regenerated below through helper table impossible, so store demo token in audit
            c.execute('INSERT INTO audit_logs(actor_type,role,action,entity_type,entity_id,after_json,created_at) VALUES(?,?,?,?,?,?,?)',('SYSTEM','SYSTEM','SEED_BRANCH_TOKEN','BRANCH',str(i),json.dumps({'token':tok}),now()))
        c.commit()
    migrate_corrections_for_repeat_requests(c)
    apply_demo_branch_limit(c)
    ensure_today(c)
    reset_test_day_once(c)
    c.close()

def migrate_corrections_for_repeat_requests(c):
    row=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='corrections'").fetchone()
    sql=(row['sql'] or '') if row else ''
    if 'delivery_id INTEGER UNIQUE' in sql or 'UNIQUE' in sql.upper():
        c.execute('ALTER TABLE corrections RENAME TO corrections_old')
        c.execute('CREATE TABLE corrections(id INTEGER PRIMARY KEY, delivery_id INTEGER, requested_by_driver_id INTEGER, requested_at TEXT, fields_json TEXT, driver_note TEXT, status TEXT, resolved_at TEXT)')
        c.execute('INSERT INTO corrections(id,delivery_id,requested_by_driver_id,requested_at,fields_json,driver_note,status,resolved_at) SELECT id,delivery_id,requested_by_driver_id,requested_at,fields_json,driver_note,status,resolved_at FROM corrections_old')
        c.execute('DROP TABLE corrections_old')
        c.commit()

def apply_demo_branch_limit(c):
    # Apply the 3-branch demo preset once only. After that, admin enable/disable choices are respected.
    key='DEMO_BRANCH_LIMIT_INITIALIZED_V5'
    if c.execute('SELECT 1 FROM app_settings WHERE key=?',(key,)).fetchone():
        return
    n=max(1, DEMO_ACTIVE_BRANCHES)
    c.execute('UPDATE branches SET active=CASE WHEN id<=? THEN 1 ELSE 0 END',(n,))
    c.execute("DELETE FROM branch_sessions WHERE delivery_id IN (SELECT d.id FROM deliveries d JOIN branches b ON b.id=d.branch_id WHERE d.service_date=? AND b.active=0)",(today(),))
    c.execute("DELETE FROM corrections WHERE delivery_id IN (SELECT d.id FROM deliveries d JOIN branches b ON b.id=d.branch_id WHERE d.service_date=? AND b.active=0)",(today(),))
    c.execute("DELETE FROM deliveries WHERE service_date=? AND branch_id IN (SELECT id FROM branches WHERE active=0)",(today(),))
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(key,now()))
    c.commit()

def reset_test_day_once(c):
    key='V4_THREE_BRANCH_TEST_RESET'
    if c.execute('SELECT 1 FROM app_settings WHERE key=?',(key,)).fetchone():
        return
    # One-time clean restart for the hosted test: reset only today's three active test branches.
    active_ids=[r['id'] for r in c.execute('SELECT id FROM branches WHERE active=1 ORDER BY id LIMIT ?',(max(1,DEMO_ACTIVE_BRANCHES),)).fetchall()]
    if active_ids:
        q=','.join('?' for _ in active_ids)
        dids=[r['id'] for r in c.execute(f'SELECT id FROM deliveries WHERE service_date=? AND branch_id IN ({q})',(today(),*active_ids)).fetchall()]
        if dids:
            qd=','.join('?' for _ in dids)
            c.execute(f'DELETE FROM branch_sessions WHERE delivery_id IN ({qd})',dids)
            c.execute(f'DELETE FROM corrections WHERE delivery_id IN ({qd})',dids)
            c.execute(f"UPDATE deliveries SET status='WAITING_SECRETARY',document_original=NULL,document_final=NULL,outbound_original=NULL,outbound_final=NULL,inbound_final=NULL,note_final=NULL,signer_name=NULL,branch_signed_at=NULL,branch_signature=NULL,correction_signature=NULL,correction_signer_name=NULL,correction_reason=NULL,corrected_at=NULL,driver_confirmed_at=NULL,row_version=row_version+1 WHERE id IN ({qd})",dids)
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?)',(key,now()))
    c.commit()

def ensure_today(c):
    d=today()
    for r in c.execute('SELECT * FROM routes WHERE active=1').fetchall():
        dr=c.execute('SELECT * FROM daily_routes WHERE service_date=? AND route_id=?',(d,r['id'])).fetchone()
        if not dr:
            driver=((r['id']-1)%5)+1
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,?,?)',(d,r['id'],driver)); drid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            for b in c.execute('SELECT * FROM branches WHERE active=1 AND route_id=? ORDER BY stop_order',(r['id'],)).fetchall():
                c.execute('INSERT OR IGNORE INTO deliveries(service_date,daily_route_id,branch_id,status) VALUES(?,?,?,?)',(d,drid,b['id'],'WAITING_SECRETARY'))
    c.commit()

init_db()

def audit(c, actor_type, actor_id, role, action, etype, eid, before=None, after=None, reason=None):
    c.execute('INSERT INTO audit_logs(actor_type,actor_id,role,action,entity_type,entity_id,before_json,after_json,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(actor_type,str(actor_id or ''),role,action,etype,str(eid),json.dumps(before,ensure_ascii=False) if before is not None else None,json.dumps(after,ensure_ascii=False) if after is not None else None,reason,now()))

def current_user(req):
    tok=req.cookies.get('session')
    if not tok: return None
    c=db(); r=c.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(thash(tok),now())).fetchone(); c.close(); return dict(r) if r else None

def require_user(req, roles=None):
    u=current_user(req)
    if not u: raise HTTPException(401,'UNAUTHENTICATED')
    if roles and u['role'] not in roles: raise HTTPException(403,'FORBIDDEN')
    return u

async def publish(event):
    dead=[]
    for q in list(subscribers):
        try:q.put_nowait(event)
        except:dead.append(q)
    for q in dead: subscribers.discard(q)

@app.get('/health')
def health(): return {'ok':True,'environment':APP_ENV}

@app.get('/', response_class=HTMLResponse)
def home(): return (STATIC/'index.html').read_text(encoding='utf-8')
@app.get('/branch/{token}', response_class=HTMLResponse)
def branch_page(token:str): return (STATIC/'branch.html').read_text(encoding='utf-8').replace('__BRANCH_TOKEN__',token)
@app.get('/activate-driver/{token}', response_class=HTMLResponse)
def driver_activate_page(token:str): return (STATIC/'activate.html').read_text(encoding='utf-8').replace('__ACTIVATION_TOKEN__',token)
@app.get('/driver', response_class=HTMLResponse)
def driver_page(): return (STATIC/'driver.html').read_text(encoding='utf-8')
@app.get('/reset-password', response_class=HTMLResponse)
def reset_page(): return (STATIC/'reset.html').read_text(encoding='utf-8')

@app.post('/api/auth/login')
async def login(req:Request):
    p=await req.json(); c=db(); u=c.execute('SELECT * FROM users WHERE username=? AND is_active=1',(p.get('username',''),)).fetchone()
    if not u or not verify_secret(p.get('password',''),u['password_hash']): c.close(); raise HTTPException(401,'帳號或密碼錯誤')
    tok=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(thash(tok),u['id'],(datetime.now()+timedelta(hours=12)).isoformat())); audit(c,'USER',u['id'],u['role'],'LOGIN','USER',u['id']); c.commit(); c.close()
    r=JSONResponse({'ok':True,'role':u['role']}); r.set_cookie('session',tok,httponly=True,samesite='lax',secure=(APP_ENV=='production'),max_age=43200); return r
@app.post('/api/auth/logout')
def logout(req:Request):
    tok=req.cookies.get('session'); c=db();
    if tok:c.execute('DELETE FROM sessions WHERE token_hash=?',(thash(tok),));c.commit()
    c.close(); r=JSONResponse({'ok':True});r.delete_cookie('session');return r
@app.get('/api/auth/me')
def me(req:Request):
    u=current_user(req); return {'user': {'username':u['username'],'role':u['role']} if u else None}
@app.post('/api/auth/password/forgot')
async def forgot(req:Request):
    p=await req.json(); c=db(); u=c.execute('SELECT * FROM users WHERE username=?',(p.get('username',''),)).fetchone(); demo=None
    if u:
        raw=secrets.token_urlsafe(32); c.execute('INSERT INTO password_resets(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)',(u['id'],thash(raw),(datetime.now()+timedelta(minutes=15)).isoformat(),now())); c.commit(); demo=(f'/reset-password?token={raw}' if (APP_ENV!='production' or DEMO_RESET_LINKS) else None)
    c.close(); return {'ok':True,'message':'若帳號存在，系統已寄送重設連結。','demo_reset_url':demo}
@app.post('/api/auth/password/reset')
async def reset(req:Request):
    p=await req.json(); c=db(); r=c.execute('SELECT * FROM password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at>?',(thash(p.get('token','')),now())).fetchone()
    if not r: c.close(); raise HTTPException(400,'重設連結無效或已過期')
    c.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_secret(p.get('new_password','')),r['user_id'])); c.execute('UPDATE password_resets SET used_at=? WHERE id=?',(now(),r['id'])); c.execute('DELETE FROM sessions WHERE user_id=?',(r['user_id'],)); c.commit(); c.close(); return {'ok':True}

@app.get('/api/dashboard/today')
def dashboard(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); ensure_today(c)
    rows=c.execute('SELECT status,COUNT(*) n FROM deliveries WHERE service_date=? GROUP BY status',(today(),)).fetchall(); counts={r['status']:r['n'] for r in rows}; total=sum(counts.values()); completed=counts.get('STOP_COMPLETED',0)
    routes=c.execute('''SELECT dr.id,r.code,r.name,d.name driver,COUNT(x.id) total,SUM(CASE WHEN x.status='STOP_COMPLETED' THEN 1 ELSE 0 END) completed,dr.status FROM daily_routes dr JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id LEFT JOIN deliveries x ON x.daily_route_id=dr.id WHERE dr.service_date=? GROUP BY dr.id ORDER BY r.code''',(today(),)).fetchall()
    c.close(); return {'date':today(),'total':total,'completed':completed,'waiting_branch':counts.get('WAITING_BRANCH',0),'waiting_driver':counts.get('WAITING_DRIVER',0),'waiting_driver_confirm':counts.get('WAITING_DRIVER_CONFIRM',0)+counts.get('WAITING_DRIVER_RECONFIRM',0),'waiting_correction':counts.get('WAITING_BRANCH_CORRECTION',0),'routes':[dict(x) for x in routes]}
@app.get('/api/dashboard/deliveries')
def dashboard_deliveries(req:Request, status:str|None=None, route:str|None=None, search:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); c=db(); q='''SELECT x.*,b.code branch_code,b.name branch_name,r.code route_code,d.name driver_name FROM deliveries x JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id WHERE x.service_date=?'''; args=[today()]
    if status:q+=' AND x.status=?';args.append(status)
    if route:q+=' AND r.code=?';args.append(route)
    if search:q+=' AND b.name LIKE ?';args.append('%'+search+'%')
    q+=' ORDER BY r.code,b.stop_order'; rows=[dict(x) for x in c.execute(q,args).fetchall()];c.close();return rows

@app.get('/api/branches')
def branches(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); rows=[dict(x) for x in c.execute("SELECT b.id,b.code,b.name,r.code route_code,b.route_id,b.stop_order,b.active,b.qr_created_at,b.address,b.phone,b.contact_name,b.contact_info,b.delivery_weekdays,b.delivery_frequency,CASE WHEN b.pin_hash IS NULL THEN '未設定' ELSE '已設定' END pin_status FROM branches b JOIN routes r ON r.id=b.route_id ORDER BY b.active DESC,r.code,b.stop_order").fetchall()];c.close();return rows

def raw_branch_token(c,bid):
    # demo-only retrieval of current token from last rotate/seed audit record
    rows=c.execute("SELECT after_json FROM audit_logs WHERE entity_type='BRANCH' AND entity_id=? AND action IN ('SEED_BRANCH_TOKEN','ROTATE_BRANCH_QR') ORDER BY id DESC LIMIT 1",(str(bid),)).fetchone()
    return json.loads(rows['after_json'])['token'] if rows else None
@app.get('/api/branches/{bid}/qr')
def branch_qr(req:Request,bid:int):
    require_user(req,['ADMIN','SECRETARY']); c=db(); b=c.execute('SELECT * FROM branches WHERE id=?',(bid,)).fetchone(); raw=raw_branch_token(c,bid); c.close()
    if not b or not raw: raise HTTPException(404)
    return {'branch':b['name'],'url':public_base_url(req)+'/branch/'+raw}
@app.get('/api/branches/{bid}/qr.png')
def branch_qr_png(req:Request,bid:int):
    require_user(req,['ADMIN','SECRETARY']); c=db(); raw=raw_branch_token(c,bid); c.close();
    if not raw: raise HTTPException(404)
    im=qrcode.make(public_base_url(req)+'/branch/'+raw); bio=io.BytesIO(); im.save(bio,'PNG'); return Response(bio.getvalue(),media_type='image/png')
@app.post('/api/branches/{bid}/qr/rotate')
async def rotate_qr(req:Request,bid:int):
    u=require_user(req,['ADMIN']); c=db(); raw=secrets.token_urlsafe(24); c.execute('UPDATE branches SET access_token_hash=?,qr_created_at=? WHERE id=?',(thash(raw),now(),bid)); audit(c,'USER',u['id'],u['role'],'ROTATE_BRANCH_QR','BRANCH',bid,after={'token':raw});c.commit();c.close(); await publish({'type':'branch.qr_rotated','branch_id':bid});return {'ok':True}
@app.post('/api/branches/{bid}/pin')
async def set_branch_pin(req:Request,bid:int):
    u=require_user(req,['ADMIN','SECRETARY']); p=await req.json(); pin=p.get('pin','');
    if len(pin)!=4 or not pin.isdigit():raise HTTPException(400,'PIN需為4位數字')
    c=db();c.execute('UPDATE branches SET pin_hash=? WHERE id=?',(hash_secret(pin),bid));audit(c,'USER',u['id'],u['role'],'RESET_BRANCH_PIN','BRANCH',bid);c.commit();c.close();return {'ok':True}


@app.get('/api/qr.png')
def generic_qr(req:Request, data:str):
    require_user(req,['ADMIN','SECRETARY']); im=qrcode.make(data); bio=io.BytesIO(); im.save(bio,'PNG'); return Response(bio.getvalue(),media_type='image/png')

@app.get('/api/drivers')
def drivers(req:Request):
    require_user(req,['ADMIN','SECRETARY']);c=db();rows=[dict(x) for x in c.execute("SELECT id,name,active,CASE WHEN pin_hash IS NULL THEN 0 ELSE 1 END pin_set FROM drivers ORDER BY active DESC,id").fetchall()];c.close();return rows
@app.post('/api/drivers/{did}/activation')
async def activation(req:Request,did:int):
    u=require_user(req,['ADMIN']); raw=secrets.token_urlsafe(32); c=db()
    # Only the newest activation QR remains valid. Older unused QR codes are revoked immediately.
    c.execute('UPDATE driver_activation_tokens SET revoked_at=? WHERE driver_id=? AND used_at IS NULL AND revoked_at IS NULL',(now(),did))
    c.execute('INSERT INTO driver_activation_tokens(driver_id,token_hash,created_at,expires_at,created_by) VALUES(?,?,?,?,?)',(did,thash(raw),now(),(datetime.now()+timedelta(minutes=10)).isoformat(),u['id']))
    audit(c,'USER',u['id'],u['role'],'CREATE_DRIVER_ACTIVATION_QR','DRIVER',did,after={'expires_in_seconds':600})
    c.commit();c.close();return {'url':public_base_url(req)+'/activate-driver/'+raw,'expires_in_seconds':600}
@app.get('/api/driver-activation/{token}')
def activation_info(token:str):
    c=db(); r=c.execute('''SELECT t.*,d.name FROM driver_activation_tokens t JOIN drivers d ON d.id=t.driver_id WHERE t.token_hash=?''',(thash(token),)).fetchone();c.close();
    if not r or r['used_at'] or r['revoked_at'] or r['expires_at']<=now(): raise HTTPException(410,'此啟用QR Code已失效')
    return {'driver':r['name'],'expires_at':r['expires_at']}
@app.post('/api/driver-activation/{token}')
async def activate_driver(token:str, req:Request):
    p=await req.json(); pin=p.get('pin','');
    if len(pin)!=4 or not pin.isdigit():raise HTTPException(400,'PIN需為4位數字')
    c=db(); c.execute('BEGIN IMMEDIATE'); r=c.execute('SELECT * FROM driver_activation_tokens WHERE token_hash=?',(thash(token),)).fetchone()
    if not r or r['used_at'] or r['revoked_at'] or r['expires_at']<=now(): c.rollback();c.close();raise HTTPException(410,'此啟用QR Code已失效')
    device_key=secrets.token_urlsafe(24); c.execute('UPDATE drivers SET pin_hash=? WHERE id=?',(hash_secret(pin),r['driver_id'])); c.execute('INSERT INTO driver_devices(driver_id,device_key,activated_at,last_seen_at) VALUES(?,?,?,?)',(r['driver_id'],device_key,now(),now())); c.execute('UPDATE driver_activation_tokens SET used_at=? WHERE id=?',(now(),r['id']));c.commit();c.close();return {'ok':True,'device_key':device_key}
@app.post('/api/driver/unlock')
async def driver_unlock(req:Request):
    p=await req.json(); c=db(); dev=c.execute('''SELECT dd.*,d.pin_hash FROM driver_devices dd JOIN drivers d ON d.id=dd.driver_id WHERE dd.device_key=? AND dd.revoked_at IS NULL''',(p.get('device_key',''),)).fetchone()
    if not dev or not verify_secret(p.get('pin',''),dev['pin_hash'] or ''):c.close();raise HTTPException(401,'裝置或PIN錯誤')
    raw=secrets.token_urlsafe(32);c.execute('INSERT INTO driver_sessions(token_hash,driver_id,device_id,expires_at) VALUES(?,?,?,?)',(thash(raw),dev['driver_id'],dev['id'],(datetime.now()+timedelta(hours=12)).isoformat()));c.commit();c.close();return {'token':raw}
def driver_auth(req):
    auth=req.headers.get('Authorization',''); raw=auth[7:] if auth.startswith('Bearer ') else ''
    c=db();r=c.execute('SELECT * FROM driver_sessions WHERE token_hash=? AND expires_at>?',(thash(raw),now())).fetchone();c.close();
    if not r:raise HTTPException(401,'DRIVER_UNAUTHENTICATED')
    return dict(r)
@app.get('/api/driver/today')
def driver_today(req:Request):
    s=driver_auth(req); c=db(); rows=[dict(x) for x in c.execute('''SELECT x.*,b.name branch_name,b.code branch_code,r.code route_code FROM deliveries x JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id WHERE x.service_date=? AND dr.driver_id=? AND b.active=1 ORDER BY r.code,b.stop_order''',(today(),s['driver_id'])).fetchall()];c.close();return rows
@app.patch('/api/driver/deliveries/{did}/outbound')
async def outbound(did:int,req:Request):
    s=driver_auth(req);p=await req.json();qty=int(p.get('qty',0));c=db(); x=c.execute('''SELECT x.*,dr.driver_id FROM deliveries x JOIN daily_routes dr ON dr.id=x.daily_route_id WHERE x.id=?''',(did,)).fetchone()
    if not x or x['driver_id']!=s['driver_id']:c.close();raise HTTPException(403)
    if x['status'] not in ('WAITING_DRIVER','WAITING_SECRETARY','WAITING_BRANCH'):c.close();raise HTTPException(409,'分館簽收後不可再修改送出數量')
    status='WAITING_BRANCH' if x['document_original'] is not None else 'WAITING_SECRETARY'; c.execute('UPDATE deliveries SET outbound_original=?,outbound_final=?,status=?,row_version=row_version+1 WHERE id=?',(qty,qty,status,did));audit(c,'DRIVER',s['driver_id'],'DRIVER','SET_OR_EDIT_OUTBOUND','DELIVERY',did,before={'qty':x['outbound_final']},after={'qty':qty});c.commit();c.close();await publish({'type':'delivery.updated','id':did});return {'ok':True}
@app.post('/api/driver/deliveries/{did}/confirm')
async def confirm(did:int,req:Request):
    s=driver_auth(req);c=db();x=c.execute('''SELECT x.*,dr.driver_id FROM deliveries x JOIN daily_routes dr ON dr.id=x.daily_route_id WHERE x.id=?''',(did,)).fetchone()
    if not x or x['driver_id']!=s['driver_id']:c.close();raise HTTPException(403)
    if x['status'] not in ('WAITING_DRIVER_CONFIRM','WAITING_DRIVER_RECONFIRM'):c.close();raise HTTPException(409,'目前狀態不可完成本站')
    c.execute("UPDATE deliveries SET status='STOP_COMPLETED',driver_confirmed_at=?,row_version=row_version+1 WHERE id=?",(now(),did));audit(c,'DRIVER',s['driver_id'],'DRIVER','CONFIRM_STOP','DELIVERY',did);c.commit();c.close();await publish({'type':'delivery.updated','id':did});return {'ok':True}
@app.post('/api/driver/deliveries/{did}/request-correction')
async def request_correction(did:int,req:Request):
    s=driver_auth(req); p=await req.json(); c=db(); x=c.execute('''SELECT x.*,dr.driver_id FROM deliveries x JOIN daily_routes dr ON dr.id=x.daily_route_id WHERE x.id=?''',(did,)).fetchone()
    if not x or x['driver_id']!=s['driver_id']:c.close();raise HTTPException(403)
    if x['status'] not in ('WAITING_DRIVER_CONFIRM','WAITING_DRIVER_RECONFIRM'):c.close();raise HTTPException(409,'目前狀態不可要求分館更正')
    if c.execute("SELECT 1 FROM corrections WHERE delivery_id=? AND status='PENDING'",(did,)).fetchone():c.close();raise HTTPException(409,'此筆已有待分館處理的更正要求')
    fields=['document','outbound','inbound']
    c.execute('INSERT INTO corrections(delivery_id,requested_by_driver_id,requested_at,fields_json,driver_note,status) VALUES(?,?,?,?,?,?)',(did,s['driver_id'],now(),json.dumps(fields),p.get('note',''),'PENDING'));audit(c,'DRIVER',s['driver_id'],'DRIVER','REQUEST_CORRECTION','DELIVERY',did,after={'fields':fields,'note':p.get('note','')});c.execute("UPDATE deliveries SET status='WAITING_BRANCH_CORRECTION',row_version=row_version+1 WHERE id=?",(did,));c.commit();c.close();await publish({'type':'delivery.updated','id':did});return {'ok':True}
@app.post('/api/driver/routes/{rid}/sign')
async def sign_route(rid:int,req:Request):
    s=driver_auth(req); p=await req.json();c=db();dr=c.execute('SELECT * FROM daily_routes WHERE id=? AND driver_id=?',(rid,s['driver_id'])).fetchone();
    if not dr:c.close();raise HTTPException(403)
    n=c.execute("SELECT COUNT(*) n FROM deliveries WHERE daily_route_id=? AND status!='STOP_COMPLETED'",(rid,)).fetchone()['n']
    if n:c.close();raise HTTPException(409,'尚有站點未完成')
    c.execute("UPDATE daily_routes SET status='DRIVER_SIGNED',driver_signature=?,driver_signed_at=? WHERE id=?",(p.get('signature',''),now(),rid));c.commit();c.close();await publish({'type':'route.updated','id':rid});return {'ok':True}

@app.post('/api/branch-session/verify')
async def branch_verify(req:Request):
    p=await req.json(); c=db(); b=c.execute('SELECT * FROM branches WHERE access_token_hash=? AND active=1',(thash(p.get('token','')),)).fetchone()
    if not b or not verify_secret(p.get('pin',''),b['pin_hash']):c.close();raise HTTPException(401,'QR或驗證碼錯誤')
    x=c.execute('SELECT * FROM deliveries WHERE service_date=? AND branch_id=?',(today(),b['id'])).fetchone()
    if not x:c.close();raise HTTPException(404,'今日沒有配送任務')
    raw=secrets.token_urlsafe(32);c.execute('INSERT INTO branch_sessions(token_hash,branch_id,delivery_id,expires_at) VALUES(?,?,?,?)',(thash(raw),b['id'],x['id'],(datetime.now()+timedelta(minutes=30)).isoformat()));c.commit();c.close();return {'session':raw,'branch':b['name']}
def branch_auth(req):
    auth=req.headers.get('Authorization',''); raw=auth[7:] if auth.startswith('Bearer ') else ''
    c=db();r=c.execute('SELECT * FROM branch_sessions WHERE token_hash=? AND expires_at>?',(thash(raw),now())).fetchone();c.close();
    if not r:raise HTTPException(401,'BRANCH_SESSION_EXPIRED')
    return dict(r)
@app.get('/api/branch-session/today')
def branch_today(req:Request):
    s=branch_auth(req);c=db();x=c.execute('''SELECT x.*,b.name branch_name FROM deliveries x JOIN branches b ON b.id=x.branch_id WHERE x.id=? AND x.branch_id=?''',(s['delivery_id'],s['branch_id'])).fetchone();corr=c.execute("SELECT * FROM corrections WHERE delivery_id=? ORDER BY CASE WHEN status='PENDING' THEN 0 ELSE 1 END,id DESC LIMIT 1",(x['id'],)).fetchone();c.close();out=dict(x);out['correction']=dict(corr) if corr else None;return out
@app.post('/api/branch-session/sign')
async def branch_sign(req:Request):
    s=branch_auth(req);p=await req.json();c=db();x=c.execute('SELECT * FROM deliveries WHERE id=?',(s['delivery_id'],)).fetchone()
    if x['status']!='WAITING_BRANCH':c.close();raise HTTPException(409,'目前不可第一次簽收')
    vals=(int(p['document']),int(p['outbound']),int(p['inbound']),p.get('note',''),p.get('signer','').strip(),p.get('signature',''))
    if not vals[4] or not vals[5]:c.close();raise HTTPException(400,'姓名與簽名必填')
    c.execute("UPDATE deliveries SET document_final=?,outbound_final=?,inbound_final=?,note_final=?,signer_name=?,branch_signature=?,branch_signed_at=?,status='WAITING_DRIVER_CONFIRM',row_version=row_version+1 WHERE id=?",(*vals,now(),x['id']));audit(c,'BRANCH',s['branch_id'],'BRANCH','BRANCH_SIGN','DELIVERY',x['id'],after={'document':vals[0],'outbound':vals[1],'inbound':vals[2],'note':vals[3],'signer':vals[4]});c.commit();c.close();await publish({'type':'delivery.updated','id':x['id']});return {'ok':True}
@app.post('/api/branch-session/correct')
async def branch_correct(req:Request):
    s=branch_auth(req);p=await req.json();c=db();x=c.execute('SELECT * FROM deliveries WHERE id=?',(s['delivery_id'],)).fetchone();corr=c.execute("SELECT * FROM corrections WHERE delivery_id=? AND status='PENDING' ORDER BY id DESC LIMIT 1",(x['id'],)).fetchone()
    if x['status']!='WAITING_BRANCH_CORRECTION' or not corr or corr['status']!='PENDING':c.close();raise HTTPException(409,'目前沒有可更正資料')
    if not p.get('reason','').strip() or not p.get('signer','').strip() or not p.get('signature',''):c.close();raise HTTPException(400,'更正原因、姓名與新簽名必填')
    def optional_qty(key,current):
        value=p.get(key,None)
        if value is None or str(value).strip()=='':
            return current
        try:
            value=int(value)
        except (TypeError,ValueError):
            c.close(); raise HTTPException(400,f'{key} 數量格式錯誤')
        if value < 0:
            c.close(); raise HTTPException(400,'數量不可小於 0')
        return value
    document=optional_qty('document',x['document_final'])
    outbound_qty=optional_qty('outbound',x['outbound_final'])
    inbound=optional_qty('inbound',x['inbound_final'])
    before={'document':x['document_final'],'outbound':x['outbound_final'],'inbound':x['inbound_final']}
    c.execute("UPDATE deliveries SET document_final=?,outbound_final=?,inbound_final=?,correction_reason=?,correction_signer_name=?,correction_signature=?,corrected_at=?,status='WAITING_DRIVER_RECONFIRM',row_version=row_version+1 WHERE id=?",(document,outbound_qty,inbound,p['reason'],p['signer'],p['signature'],now(),x['id']))
    c.execute("UPDATE corrections SET status='RESOLVED',resolved_at=? WHERE id=?",(now(),corr['id']))
    audit(c,'BRANCH',s['branch_id'],'BRANCH','BRANCH_CORRECT','DELIVERY',x['id'],before=before,after={'document':document,'outbound':outbound_qty,'inbound':inbound,'reason':p['reason'],'signer':p['signer']})
    c.commit();c.close();await publish({'type':'delivery.updated','id':x['id']});return {'ok':True}

@app.patch('/api/secretary/deliveries/{did}/document')
async def set_doc(did:int,req:Request):
    u=require_user(req,['SECRETARY','ADMIN']); p=await req.json()
    try: qty=int(p.get('qty'))
    except: raise HTTPException(400,'公文數量必須是整數')
    if qty < 0: raise HTTPException(400,'公文數量不可小於 0')
    c=db(); x=c.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone()
    if not x: c.close(); raise HTTPException(404,'找不到配送資料')
    if x['outbound_original'] is not None:
        c.close(); raise HTTPException(409,'司機已輸入圖書送出數量，公文數量已鎖定，無法再修改')
    if x['status'] not in ('WAITING_SECRETARY','WAITING_DRIVER'):
        c.close(); raise HTTPException(409,'目前配送狀態不可修改公文數量')
    before={'qty':x['document_final']}
    c.execute("UPDATE deliveries SET document_original=?,document_final=?,status='WAITING_DRIVER',row_version=row_version+1 WHERE id=?",(qty,qty,did))
    audit(c,'USER',u['id'],u['role'],'SET_DOCUMENT','DELIVERY',did,before=before,after={'qty':qty})
    c.commit(); c.close(); await publish({'type':'delivery.updated','id':did}); return {'ok':True,'qty':qty,'locked':False}

@app.post('/api/secretary/documents/zero-all')
async def zero_all(req:Request):
    u=require_user(req,['SECRETARY','ADMIN']); c=db()
    cur=c.execute("UPDATE deliveries SET document_original=0,document_final=0,status='WAITING_DRIVER',row_version=row_version+1 WHERE service_date=? AND outbound_original IS NULL AND status IN ('WAITING_SECRETARY','WAITING_DRIVER')",(today(),))
    changed=cur.rowcount
    audit(c,'USER',u['id'],u['role'],'ZERO_ALL_DOCUMENTS','DAY',today(),after={'changed':changed})
    c.commit(); c.close(); await publish({'type':'dashboard.refresh'}); return {'ok':True,'changed':changed}

@app.post('/api/secretary/final-sign')
async def final_sign(req:Request):
    u=require_user(req,['SECRETARY']);p=await req.json();c=db();n=c.execute("SELECT COUNT(*) n FROM daily_routes WHERE service_date=? AND status!='DRIVER_SIGNED'",(today(),)).fetchone()['n']
    if n:c.close();raise HTTPException(409,'尚有路線未完成司機簽名')
    c.execute("INSERT INTO daily_reports(service_date,secretary_signature,secretary_signed_at,status,locked_at) VALUES(?,?,?,?,?) ON CONFLICT(service_date) DO UPDATE SET secretary_signature=excluded.secretary_signature,secretary_signed_at=excluded.secretary_signed_at,status='LOCKED',locked_at=excluded.locked_at",(today(),p.get('signature',''),now(),'LOCKED',now()));c.commit();c.close();await publish({'type':'report.locked'});return {'ok':True}

@app.get('/api/reports/today.csv')
def report_csv(req:Request):
    require_user(req,['ADMIN','SECRETARY']);c=db();rows=c.execute('''SELECT r.code 路線,d.name 司機,b.name 分館,x.document_final 公文,x.outbound_final 圖書送出,x.inbound_final 圖書收回,x.note_final 備註,x.signer_name 簽收人,x.branch_signed_at 簽收時間,x.status 狀態 FROM deliveries x JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id WHERE x.service_date=? ORDER BY r.code,b.stop_order''',(today(),)).fetchall();c.close();bio=io.StringIO();w=csv.writer(bio);headers=rows[0].keys() if rows else [];w.writerow(headers);[w.writerow(list(r)) for r in rows];data='\ufeff'+bio.getvalue();return Response(data,media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{today()}_library_logistics.csv"'})
@app.get('/api/audit')
def audits(req:Request,limit:int=100):
    require_user(req,['ADMIN','SECRETARY']);c=db();rows=[dict(x) for x in c.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?',(min(limit,500),)).fetchall()];c.close();return rows


# ===== Expanded Admin / Secretary management =====
@app.post('/api/branches')
async def create_branch(req:Request):
    u=require_user(req,['ADMIN']); p=await req.json(); c=db(); code=(p.get('code') or '').strip(); name=(p.get('name') or '').strip()
    if not code or not name: c.close(); raise HTTPException(400,'分館代碼與名稱必填')
    if c.execute('SELECT 1 FROM branches WHERE code=?',(code,)).fetchone(): c.close(); raise HTTPException(409,'分館代碼已存在')
    route_id=int(p.get('route_id') or 1); stop_order=int(p.get('stop_order') or 1); raw=secrets.token_urlsafe(24)
    c.execute('''INSERT INTO branches(code,name,route_id,stop_order,active,pin_hash,access_token_hash,qr_created_at,address,phone,contact_name,contact_info,delivery_weekdays,delivery_frequency) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?)''',(code,name,route_id,stop_order,hash_secret(p.get('pin') or '1234'),thash(raw),now(),p.get('address',''),p.get('phone',''),p.get('contact_name',''),p.get('contact_info',''),p.get('delivery_weekdays','1,2,3,4,5'),p.get('delivery_frequency','每週固定')))
    bid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,'USER',u['id'],u['role'],'CREATE_BRANCH','BRANCH',bid,after={'token':raw,'code':code,'name':name}); c.commit(); c.close(); return {'ok':True,'id':bid}

@app.get('/api/branches/{bid}/detail')
def branch_detail(req:Request,bid:int):
    require_user(req,['ADMIN','SECRETARY']); c=db(); b=c.execute('SELECT b.*,r.code route_code FROM branches b LEFT JOIN routes r ON r.id=b.route_id WHERE b.id=?',(bid,)).fetchone(); c.close()
    if not b: raise HTTPException(404)
    out=dict(b); out['pin_status']='已設定' if out.get('pin_hash') else '未設定'; out.pop('pin_hash',None); out.pop('access_token_hash',None); return out

@app.patch('/api/branches/{bid}')
async def update_branch(req:Request,bid:int):
    u=require_user(req,['ADMIN']); p=await req.json(); c=db(); old=c.execute('SELECT * FROM branches WHERE id=?',(bid,)).fetchone()
    if not old: c.close(); raise HTTPException(404)
    fields=['code','name','route_id','stop_order','address','phone','contact_name','contact_info','delivery_weekdays','delivery_frequency']; vals={k:p[k] for k in fields if k in p}
    if vals: c.execute('UPDATE branches SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),bid))
    audit(c,'USER',u['id'],u['role'],'UPDATE_BRANCH','BRANCH',bid,after=vals); c.commit(); c.close(); return {'ok':True}

@app.post('/api/branches/{bid}/deactivate')
async def deactivate_branch(req:Request,bid:int):
    u=require_user(req,['ADMIN']); c=db(); c.execute('UPDATE branches SET active=0 WHERE id=?',(bid,)); audit(c,'USER',u['id'],u['role'],'DEACTIVATE_BRANCH','BRANCH',bid); c.commit(); c.close(); return {'ok':True}
@app.post('/api/branches/{bid}/activate')
async def activate_branch(req:Request,bid:int):
    u=require_user(req,['ADMIN']); c=db(); c.execute('UPDATE branches SET active=1 WHERE id=?',(bid,)); audit(c,'USER',u['id'],u['role'],'ACTIVATE_BRANCH','BRANCH',bid); c.commit(); c.close(); return {'ok':True}

@app.delete('/api/branches/{bid}')
async def delete_branch(req:Request,bid:int):
    u=require_user(req,['ADMIN']); c=db(); b=c.execute('SELECT * FROM branches WHERE id=?',(bid,)).fetchone()
    if not b: c.close(); raise HTTPException(404,'找不到分館')
    used=c.execute('SELECT 1 FROM deliveries WHERE branch_id=? LIMIT 1',(bid,)).fetchone()
    if used: c.close(); raise HTTPException(409,'此分館已有配送歷史，為保留簽收紀錄不可永久刪除；請改用「停用」')
    c.execute('DELETE FROM branches WHERE id=?',(bid,)); audit(c,'USER',u['id'],u['role'],'DELETE_BRANCH','BRANCH',bid,after={'code':b['code'],'name':b['name']}); c.commit(); c.close(); return {'ok':True}

@app.get('/api/routes')
def list_routes(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM routes ORDER BY code').fetchall()]; c.close(); return rows
@app.post('/api/routes')
async def create_route(req:Request):
    u=require_user(req,['ADMIN']); p=await req.json(); c=db(); c.execute('INSERT INTO routes(code,name,active) VALUES(?,?,1)',((p.get('code') or '').strip(),(p.get('name') or '').strip())); rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,'USER',u['id'],u['role'],'CREATE_ROUTE','ROUTE',rid); c.commit(); c.close(); return {'ok':True}
@app.patch('/api/routes/{rid}')
async def update_route(req:Request,rid:int):
    u=require_user(req,['ADMIN']); p=await req.json(); c=db(); vals={k:p[k] for k in ['code','name','active'] if k in p}
    if vals:c.execute('UPDATE routes SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),rid))
    audit(c,'USER',u['id'],u['role'],'UPDATE_ROUTE','ROUTE',rid,after=vals); c.commit(); c.close(); return {'ok':True}

@app.post('/api/drivers')
async def create_driver(req:Request):
    u=require_user(req,['ADMIN']); p=await req.json(); name=(p.get('name') or '').strip()
    if not name: raise HTTPException(400,'司機姓名必填')
    c=db(); c.execute('INSERT INTO drivers(name,active) VALUES(?,1)',(name,)); did=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,'USER',u['id'],u['role'],'CREATE_DRIVER','DRIVER',did); c.commit(); c.close(); return {'ok':True,'id':did}
@app.patch('/api/drivers/{did}')
async def update_driver(req:Request,did:int):
    u=require_user(req,['ADMIN']); p=await req.json(); c=db(); vals={k:p[k] for k in ['name','active'] if k in p}
    if vals:c.execute('UPDATE drivers SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),did))
    audit(c,'USER',u['id'],u['role'],'UPDATE_DRIVER','DRIVER',did,after=vals); c.commit(); c.close(); return {'ok':True}

@app.delete('/api/drivers/{did}')
async def delete_driver(req:Request,did:int):
    u=require_user(req,['ADMIN']); c=db(); d=c.execute('SELECT * FROM drivers WHERE id=?',(did,)).fetchone()
    if not d: c.close(); raise HTTPException(404,'找不到司機')
    used=c.execute('SELECT 1 FROM daily_routes WHERE driver_id=? LIMIT 1',(did,)).fetchone()
    devices=c.execute('SELECT 1 FROM driver_devices WHERE driver_id=? LIMIT 1',(did,)).fetchone()
    if used or devices: c.close(); raise HTTPException(409,'此司機已有配送或裝置歷史，為保留紀錄不可永久刪除；請改用「停用」')
    c.execute('DELETE FROM drivers WHERE id=?',(did,)); audit(c,'USER',u['id'],u['role'],'DELETE_DRIVER','DRIVER',did,after={'name':d['name']}); c.commit(); c.close(); return {'ok':True}
@app.post('/api/drivers/{did}/pin')
async def reset_driver_pin(req:Request,did:int):
    u=require_user(req,['ADMIN']); p=await req.json(); pin=str(p.get('pin',''))
    if len(pin)!=4 or not pin.isdigit(): raise HTTPException(400,'PIN需為4位數字')
    c=db(); c.execute('UPDATE drivers SET pin_hash=? WHERE id=?',(hash_secret(pin),did)); audit(c,'USER',u['id'],u['role'],'RESET_DRIVER_PIN','DRIVER',did); c.commit(); c.close(); return {'ok':True}
@app.get('/api/drivers/{did}/devices')
def driver_devices(req:Request,did:int):
    require_user(req,['ADMIN']); c=db(); rows=[dict(x) for x in c.execute('SELECT id,driver_id,activated_at,revoked_at,last_seen_at FROM driver_devices WHERE driver_id=? ORDER BY id DESC',(did,)).fetchall()]; c.close(); return rows
@app.post('/api/driver-devices/{device_id}/revoke')
async def revoke_device(req:Request,device_id:int):
    u=require_user(req,['ADMIN']); c=db(); c.execute('UPDATE driver_devices SET revoked_at=? WHERE id=?',(now(),device_id)); audit(c,'USER',u['id'],u['role'],'REVOKE_DRIVER_DEVICE','DRIVER_DEVICE',device_id); c.commit(); c.close(); return {'ok':True}

@app.get('/api/daily-routes')
def daily_route_list(req:Request, service_date:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); d=service_date or today(); c=db(); rows=[dict(x) for x in c.execute('''SELECT dr.id,dr.service_date,dr.status,r.id route_id,r.code,r.name,d.id driver_id,d.name driver_name,COUNT(x.id) total,SUM(CASE WHEN x.status='STOP_COMPLETED' THEN 1 ELSE 0 END) completed FROM daily_routes dr JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id LEFT JOIN deliveries x ON x.daily_route_id=dr.id WHERE dr.service_date=? GROUP BY dr.id ORDER BY r.code''',(d,)).fetchall()]; c.close(); return rows
@app.patch('/api/daily-routes/{drid}/driver')
async def assign_driver(req:Request,drid:int):
    u=require_user(req,['ADMIN','SECRETARY']); p=await req.json(); did=int(p.get('driver_id')); c=db(); c.execute('UPDATE daily_routes SET driver_id=? WHERE id=?',(did,drid)); audit(c,'USER',u['id'],u['role'],'ASSIGN_DAILY_DRIVER','DAILY_ROUTE',drid,after={'driver_id':did}); c.commit(); c.close(); await publish({'type':'route.updated','id':drid}); return {'ok':True}

@app.get('/api/deliveries/all')
def all_deliveries(req:Request, service_date:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); d=service_date or today(); c=db(); rows=[dict(x) for x in c.execute('''SELECT x.*,b.code branch_code,b.name branch_name,r.code route_code,d.name driver_name,CASE WHEN co.id IS NULL THEN 0 ELSE 1 END has_correction,co.driver_note correction_driver_note,co.status correction_status FROM deliveries x JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id LEFT JOIN corrections co ON co.delivery_id=x.id WHERE x.service_date=? ORDER BY r.code,b.stop_order''',(d,)).fetchall()]; c.close(); return rows
@app.get('/api/corrections')
def correction_list(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); rows=[dict(x) for x in c.execute('''SELECT co.*,b.name branch_name,r.code route_code,d.name driver_name,x.document_final,x.outbound_final,x.inbound_final,x.note_final,x.correction_reason,x.correction_signer_name,x.corrected_at FROM corrections co JOIN deliveries x ON x.id=co.delivery_id JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=co.requested_by_driver_id ORDER BY co.id DESC''').fetchall()]; c.close(); return rows

def report_rows(c,start_date,end_date):
    return c.execute('''SELECT x.service_date 日期,r.code 路線,d.name 司機,b.code 分館代碼,b.name 分館,x.document_final 公文,x.outbound_final 圖書送出,x.inbound_final 圖書收回,x.note_final 備註,x.signer_name 簽收人,x.branch_signed_at 簽收時間,CASE WHEN co.id IS NULL THEN '否' ELSE '是' END 是否更正,x.correction_reason 更正原因,x.status 狀態 FROM deliveries x JOIN branches b ON b.id=x.branch_id JOIN daily_routes dr ON dr.id=x.daily_route_id JOIN routes r ON r.id=dr.route_id JOIN drivers d ON d.id=dr.driver_id LEFT JOIN corrections co ON co.delivery_id=x.id WHERE x.service_date BETWEEN ? AND ? ORDER BY x.service_date,r.code,b.stop_order''',(start_date,end_date)).fetchall()
def xlsx_bytes(rows,title):
    wb=Workbook(); ws=wb.active; ws.title='配送報表'; ws.append([title])
    if rows:
        headers=list(rows[0].keys()); ws.append(headers)
        for r in rows: ws.append([r[h] for h in headers])
        ws.freeze_panes='A3'
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width=min(max(12,max(len(str(c.value or '')) for c in col)+2),30)
    bio=io.BytesIO(); wb.save(bio); return bio.getvalue()
def pdf_bytes(rows,title):
    bio=io.BytesIO(); pdfmetrics.registerFont(UnicodeCIDFont('MSung-Light')); cv=canvas.Canvas(bio,pagesize=(842,595)); cv.setFont('MSung-Light',16); cv.drawString(30,565,title); y=540; cv.setFont('MSung-Light',8)
    for r in rows:
        line=f"{r['日期']}  {r['路線']}線  {r['分館']}  公文:{r['公文'] or 0}  送出:{r['圖書送出'] or 0}  收回:{r['圖書收回'] or 0}  狀態:{r['狀態']}"; cv.drawString(30,y,line[:120]); y-=14
        if y<30: cv.showPage(); cv.setFont('MSung-Light',8); y=565
    cv.save(); return bio.getvalue()
@app.get('/api/reports/daily.xlsx')
def daily_xlsx(req:Request,service_date:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); d=service_date or today(); c=db(); rows=report_rows(c,d,d); c.close(); return Response(xlsx_bytes(rows,f'{d} 圖書物流配送日報'),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{d}_daily.xlsx"'})
@app.get('/api/reports/daily.pdf')
def daily_pdf(req:Request,service_date:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); d=service_date or today(); c=db(); rows=report_rows(c,d,d); c.close(); return Response(pdf_bytes(rows,f'{d} 圖書物流配送日報'),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{d}_daily.pdf"'})
def month_range(m):
    start=m+'-01'; y=int(m[:4]); mo=int(m[5:7]); nxt=f'{y+1}-01-01' if mo==12 else f'{y}-{mo+1:02d}-01'; end=(datetime.fromisoformat(nxt)-timedelta(days=1)).date().isoformat(); return start,end
@app.get('/api/reports/monthly.xlsx')
def monthly_xlsx(req:Request,month:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); m=month or today()[:7]; a,b=month_range(m); c=db(); rows=report_rows(c,a,b); c.close(); return Response(xlsx_bytes(rows,f'{m} 圖書物流配送月報'),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="{m}_monthly.xlsx"'})
@app.get('/api/reports/monthly.pdf')
def monthly_pdf(req:Request,month:str|None=None):
    require_user(req,['ADMIN','SECRETARY']); m=month or today()[:7]; a,b=month_range(m); c=db(); rows=report_rows(c,a,b); c.close(); return Response(pdf_bytes(rows,f'{m} 圖書物流配送月報'),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="{m}_monthly.pdf"'})

@app.get('/api/email/recipients')
def recipients(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM email_recipients ORDER BY id').fetchall()]; c.close(); return rows
@app.post('/api/email/recipients')
async def add_recipient(req:Request):
    u=require_user(req,['ADMIN','SECRETARY']); p=await req.json(); email=(p.get('email') or '').strip()
    if '@' not in email: raise HTTPException(400,'Email格式錯誤')
    c=db(); c.execute('INSERT INTO email_recipients(email,recipient_type,active,created_at) VALUES(?,?,1,?)',(email,p.get('recipient_type','TO'),now())); rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,'USER',u['id'],u['role'],'ADD_EMAIL_RECIPIENT','EMAIL_RECIPIENT',rid); c.commit(); c.close(); return {'ok':True}
@app.post('/api/email/recipients/{rid}/toggle')
async def toggle_recipient(req:Request,rid:int):
    u=require_user(req,['ADMIN','SECRETARY']); c=db(); c.execute('UPDATE email_recipients SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?',(rid,)); audit(c,'USER',u['id'],u['role'],'TOGGLE_EMAIL_RECIPIENT','EMAIL_RECIPIENT',rid); c.commit(); c.close(); return {'ok':True}
@app.get('/api/email/logs')
def email_logs(req:Request):
    require_user(req,['ADMIN','SECRETARY']); c=db(); rows=[dict(x) for x in c.execute('SELECT * FROM email_logs ORDER BY id DESC LIMIT 100').fetchall()]; c.close(); return rows
@app.post('/api/email/send-report')
async def send_report(req:Request):
    u=require_user(req,['ADMIN','SECRETARY']); p=await req.json(); period=p.get('period') or today(); typ=p.get('report_type','DAILY'); c=db(); rec=[x['email'] for x in c.execute('SELECT email FROM email_recipients WHERE active=1').fetchall()]
    if not rec: c.close(); raise HTTPException(409,'尚未設定Email收件人')
    c.execute('INSERT INTO email_logs(report_type,period,recipients,status,sent_at,created_at) VALUES(?,?,?,?,?,?)',(typ,period,','.join(rec),'DEMO_SENT',now(),now())); lid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; audit(c,'USER',u['id'],u['role'],'SEND_REPORT_EMAIL','EMAIL_LOG',lid,after={'recipients':rec}); c.commit(); c.close(); return {'ok':True,'status':'DEMO_SENT','recipients':rec}
@app.post('/api/email/logs/{lid}/resend')
async def resend_email(req:Request,lid:int):
    u=require_user(req,['ADMIN','SECRETARY']); c=db(); old=c.execute('SELECT * FROM email_logs WHERE id=?',(lid,)).fetchone()
    if not old: c.close(); raise HTTPException(404)
    c.execute('INSERT INTO email_logs(report_type,period,recipients,status,sent_at,created_at) VALUES(?,?,?,?,?,?)',(old['report_type'],old['period'],old['recipients'],'DEMO_RESENT',now(),now())); audit(c,'USER',u['id'],u['role'],'RESEND_REPORT_EMAIL','EMAIL_LOG',lid); c.commit(); c.close(); return {'ok':True}

@app.get('/api/events')
async def events(req:Request):
    require_user(req,['ADMIN','SECRETARY']);q=asyncio.Queue();subscribers.add(q)
    async def gen():
        try:
            while True:
                try:e=await asyncio.wait_for(q.get(),20);yield 'event: update\ndata: '+json.dumps(e,ensure_ascii=False)+'\n\n'
                except asyncio.TimeoutError:yield 'event: ping\ndata: {}\n\n'
        finally:subscribers.discard(q)
    return StreamingResponse(gen(),media_type='text/event-stream')
