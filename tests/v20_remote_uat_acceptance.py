"""Additive acceptance against the pinned synthetic UAT only; never unittest discovery.

Inject MOVINGMATCH_V20_UAT_DATABASE_URL securely and provide --credentials-file.
Outputs case names/counts only. All generated signatures and records are synthetic.
"""
import argparse
import base64
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import sys
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run(credentials_file):
    from app.uat_init import guarded_conninfo
    from app.uat_serve import verify
    import psycopg
    from psycopg.rows import dict_row
    import requests
    from PIL import Image, ImageDraw
    conninfo=guarded_conninfo(os.environ['MOVINGMATCH_V20_UAT_DATABASE_URL'])
    credentials=json.loads(credentials_file.read_text())
    origin='https://movingmatch-v20-uat.onrender.com'
    run_id=uuid.uuid4().hex
    today=datetime.now(ZoneInfo('Asia/Taipei')).date()
    image=Image.new('RGB',(200,70),'white')
    ImageDraw.Draw(image).line([(10,50),(40,10),(75,55),(120,15),(180,50)],fill='black',width=4)
    buffer=io.BytesIO();image.save(buffer,format='PNG')
    signature='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()

    def call(method,path,token=None,payload=None):
        headers={'Authorization':'Bearer '+token} if token else {}
        response=requests.request(method,origin+path,headers=headers,json=payload,timeout=90,allow_redirects=False)
        if response.status_code!=200:
            raise RuntimeError('HTTP acceptance failure: '+path+' status='+str(response.status_code))
        return response.json()

    def office(operation,payload):
        return call('POST','/api/empty-boxes/'+operation,admin,payload)

    def branch(number,operation='',payload=None):
        return call('POST' if payload is not None else 'GET','/api/branch-session/empty-boxes'+('/'+operation if operation else ''),tokens[number],payload)

    # Confirm marker and full schema before any login or synthetic test writes.
    with psycopg.connect(conninfo,row_factory=dict_row) as c:
        verify(c)
        ids={int(r['code'][-1]):r['id'] for r in c.execute("SELECT id,code FROM branches WHERE code IN ('UAT-B1','UAT-B2','UAT-B3','UAT-B4')").fetchall()}
        assert len(ids)==4
    admin=call('POST','/api/auth/login',payload={'username':'uat-head-office','password':credentials['uat-head-office']})['token']
    for username in ('uat-secretary','uat-secretariat'):
        token=call('POST','/api/auth/login',payload={'username':username,'password':credentials[username]})['token']
        call('GET','/api/empty-boxes',token)
    tokens={}
    people={}
    balances={}
    for number in range(1,5):
        result=call('POST','/api/branch-session/desktop-login',payload={'branch_code':f'UAT-B{number}','pin':credentials[f'UAT-B{number}']})
        tokens[number]=result['session']
        people[number]=call('POST','/api/branch-session/signers',tokens[number],{'name':'虛構驗收人-'+run_id[:8]})['id']
        old=branch(number)['balance']
        balances[number]=(old['unused'] if old else 0)+20
        branch(number,'stocktakes',{'unused':balances[number],'loaded':0,'damaged':0,'actor':'虛構遠端驗收','note':run_id,'version':old['version'] if old else 0,'event_key':uuid.uuid4().hex})
    request=branch(1,'requests',{'quantity':6,'actor':'虛構遠端驗收','note':run_id,'event_key':uuid.uuid4().hex})['id']
    dates=call('GET',f'/api/empty-boxes/suggestions?branch_id={ids[1]}&start={today.isoformat()}',admin)['dates']
    arrival=next(day for day in dates if day>today.isoformat())
    sources=[]
    for number,quantity in ((2,2),(3,4)):
        dates=call('GET',f'/api/empty-boxes/suggestions?branch_id={ids[number]}&start={today.isoformat()}',admin)['dates']
        sources.append({'branch_id':ids[number],'quantity':quantity,'planned_date':dates[0]})
    transfer=office('transfers',{'request_id':request,'planned_date':arrival,'sources':sources,'event_key':uuid.uuid4().hex})['id']
    for number in (2,3,1):
        task=next(t for t in branch(number)['tasks'] if t.get('transfer_id',t['task_id'])==transfer)
        payload={'task_id':task['task_id'],'kind':task['kind'],'quantity':task['quantity'],'actual_date':today.isoformat(),'reason':'虛構提前交接測試','signer_id':people[number],'signature':signature}
        branch(number,'handoffs',payload)
        branch(number,'handoffs',payload)  # Replay must not add a second movement.
    assert branch(1)['balance']['unused']==balances[1]+6
    assert branch(2)['balance']['unused']==balances[2]-2
    assert branch(3)['balance']['unused']==balances[3]-4

    # Historical late-signing fixture: INSERT new synthetic rows only; no resets.
    with psycopg.connect(conninfo,row_factory=dict_row) as c:
        verify(c)
        route=c.execute('SELECT route_id FROM branches WHERE id=%s',(ids[4],)).fetchone()['route_id']
        target_route=c.execute('SELECT route_id FROM branches WHERE id=%s',(ids[1],)).fetchone()['route_id']
        day=today-timedelta(days=1)
        while day.weekday()>4 or c.execute('SELECT 1 FROM deliveries WHERE service_date=%s AND branch_id=%s',(day.isoformat(),ids[4])).fetchone():
            day-=timedelta(days=1)
        historical=day.isoformat()
        rid,tid,sid=[uuid.uuid4().hex for _ in range(3)]
        c.execute("INSERT INTO empty_box_requests(id,branch_id,quantity,status,created_at,actor,note) VALUES(%s,%s,2,'OPEN',%s,'虛構稍後補簽',%s)",(rid,ids[1],historical,run_id))
        c.execute("INSERT INTO empty_box_transfers(id,request_id,branch_id,quantity,planned_date,route_id,status,created_at,actor,reason) VALUES(%s,%s,%s,2,%s,%s,'PLANNED',%s,'虛構稍後補簽',%s)",(tid,rid,ids[1],today.isoformat(),target_route,historical,run_id))
        c.execute('INSERT INTO empty_box_transfer_sources(id,transfer_id,branch_id,quantity,planned_date,route_id) VALUES(%s,%s,%s,2,%s,%s)',(sid,tid,ids[4],historical,route))
        driver=c.execute("SELECT id FROM drivers WHERE name='虛構測試司機 UAT-1'").fetchone()['id']
        c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(%s,%s,%s) ON CONFLICT(service_date,route_id) DO NOTHING',(historical,route,driver))
        daily=c.execute('SELECT id FROM daily_routes WHERE service_date=%s AND route_id=%s',(historical,route)).fetchone()['id']
        delivery=c.execute("INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_original,document_final) VALUES(%s,%s,%s,'LATE_BRANCH_PENDING',0,0) RETURNING id",(historical,daily,ids[4])).fetchone()['id']
    # Re-login normally selects today's delivery; select the new historical task explicitly.
    call('POST','/api/branch-session/select-task',tokens[4],{'delivery_id':delivery})
    call('POST','/api/branch-session/sign',tokens[4],{'signer_id':people[4],'signature':signature,'note':'虛構遠端稍後補簽 '+run_id,'empty_box_handoffs':[{'kind':'PICKUP','task_id':sid,'quantity':2,'actual_date':historical,'reason':''}]})
    with psycopg.connect(conninfo,row_factory=dict_row) as c:
        rows=c.execute('SELECT h.*,m.delta FROM empty_box_handoffs h JOIN empty_box_movements m USING(event_key) WHERE h.transfer_id=%s',(transfer,)).fetchall()
        assert len(rows)==3 and sum(r['delta'] for r in rows)==0
        late=c.execute('SELECT h.actual_date,h.confirmed_at,h.signature,d.branch_signature,d.status FROM empty_box_handoffs h JOIN deliveries d ON d.id=h.delivery_id WHERE d.id=%s',(delivery,)).fetchone()
        assert late['actual_date']==historical and late['confirmed_at'][:10]>historical
        assert late['signature']==late['branch_signature'] and late['status']=='STOP_COMPLETED'
        assert c.execute('SELECT actual_date FROM empty_box_transfers WHERE id=%s',(transfer,)).fetchone()['actual_date']<arrival
    return {'target':origin,'run_id':run_id,'cases_passed':['office_role_logins','branch_stocktake','replenishment_request','multiple_sources','early_arrival','late_signature','inventory_ledger','electronic_receipt','handoff_replay'],'existing_results_deleted':False,'synthetic_only':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file',required=True,type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.credentials_file),ensure_ascii=False))
    except Exception as error:
        # Never print response bodies, request payloads, secrets or driver errors.
        raise SystemExit('Remote UAT acceptance failed; category='+type(error).__name__+'; details withheld') from None
