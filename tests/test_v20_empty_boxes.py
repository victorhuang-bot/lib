"""V20 API acceptance on synthetic isolated DBs; inherited connections are ignored."""
import os
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import test_v194 as fixture
import test_v194_signing_identity as signing
from test_v194_signing_identity import signature
from app import main
from app.empty_boxes import migrate, SCHEMA
from app.schema import SchemaTransaction


class EmptyBoxCases:
    person=signing.SigningIdentityTests.person
    def task(self,mode='normal',bid=1):
        service_date='2030-01-07'
        with self.connection() as c:
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,1,1) ON CONFLICT(service_date,route_id) DO NOTHING',(service_date,))
            route=c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=1',(service_date,)).fetchone()['id']
            status='LATE_BRANCH_PENDING' if mode=='late' else 'WAITING_BRANCH'
            did=c.execute("INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,note_final) VALUES(?,?,?,?,'原始備註')",(service_date,route,bid,status)).lastrowid
            c.execute('UPDATE branch_sessions SET delivery_id=? WHERE branch_id=?',(did,bid))
        return did
    row=signing.SigningIdentityTests.row

    def setup_boxes(self):
        self.flag=patch.dict(os.environ,{'MOVINGMATCH_V20_ENABLED':'1'})
        self.flag.start();self.addCleanup(self.flag.stop)
        self.clock=patch.object(main,'today',return_value='2030-01-07')
        self.addCleanup(self.clock.stop);self.clock=self.clock.start()
        self.timestamp=patch.object(main,'now',return_value='2030-01-07T08:00:00+00:00')
        self.addCleanup(self.timestamp.stop);self.timestamp=self.timestamp.start()
        with self.connection() as c:
            c.execute("UPDATE branch_sessions SET expires_at='2099-01-01T00:00:00+00:00'")
            c.execute("UPDATE sessions SET expires_at='2099-01-01T00:00:00+00:00'")
            c.execute("UPDATE branches SET active=1,delivery_weekdays='1,2,3,4,5' WHERE id IN (1,2,3)")
            c.execute('INSERT INTO branch_sessions(token_hash,branch_id,expires_at) VALUES(?,3,?)',(main.thash('branch-three'),'2099-01-01T00:00:00+00:00'))
            for role in ('SECRETARY','SECRETARIAT'):
                user=c.execute('INSERT INTO users(username,role,is_active) VALUES(?,?,1)',('box-'+role,role)).lastrowid
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(main.thash(role),user,'2099-01-01T00:00:00+00:00'))
        self.third={'Authorization':'Bearer branch-three'}
        self.people={bid:self.person('合成盤點人'+str(bid),headers=h) for bid,h in ((1,self.headers),(2,self.other),(3,self.third))}

    def post(self,operation,p,headers=None,office=False):
        return self.client.post(('/api/empty-boxes/' if office else '/api/branch-session/empty-boxes/')+operation,headers=headers or (self.admin if office else self.headers),json=p)

    def state(self,headers=None):
        r=self.client.get('/api/branch-session/empty-boxes',headers=headers or self.headers)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def count(self,bid=1,unused=20):
        h={1:self.headers,2:self.other,3:self.third}[bid]
        stock=self.state(h)['balance']
        p={'unused':unused,'loaded':3,'damaged':1,'actor':'合成人員','note':'盤點測試','version':stock['version'] if stock else 0,'event_key':main.secrets.token_hex(8)}
        r=self.post('stocktakes',p,h)
        self.assertEqual(r.status_code,200,r.text)
        return r,p

    def request_boxes(self,quantity=10):
        r=self.post('requests',{'quantity':quantity,'actor':'合成需求人','event_key':main.secrets.token_hex(8)})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['id']

    def dispatch(self,rid=None,sources=None,arrival='2030-01-09'):
        if rid is None:rid=self.request_boxes()
        p={'request_id':rid,'planned_date':arrival,'sources':sources or [{'branch_id':2,'quantity':4,'planned_date':'2030-01-07'}],'event_key':main.secrets.token_hex(8)}
        return self.post('transfers',p,office=True)

    def handoff(self,task,bid=2,qty=None,actual='2030-01-07',reason=''):
        h={1:self.headers,2:self.other,3:self.third}[bid]
        p={'task_id':task['task_id'],'kind':task['kind'],'quantity':task['quantity'] if qty is None else qty,'actual_date':actual,'reason':reason,'signer_id':self.people[bid]['id'],'signature':signature()}
        return self.post('handoffs',p,h),p

    def test_stocktake_history_scope_and_strict_validation(self):
        self.setup_boxes();r,p=self.count()
        self.assertEqual(self.state()['balance']['unused'],20)
        self.assertEqual(len(self.state()['stocktakes']),1)
        repeated=self.post('stocktakes',p);self.assertTrue(repeated.json()['already_saved'])
        self.assertEqual(len(self.state()['stocktakes']),1)
        self.assertEqual(self.state(self.other)['stocktakes'],[])
        for value in (True,1.5,'2',-1):
            r=self.post('stocktakes',dict(p,unused=value,event_key=str(value)))
            self.assertEqual(r.status_code,400,r.text)
        self.assertEqual(self.post('stocktakes',dict(p,event_key='stale')).status_code,409)
        self.count(unused=18)
        s=self.state();self.assertEqual([r['unused'] for r in s['stocktakes']],[18,20])
        board=self.client.get('/api/empty-boxes',headers=self.admin).json()
        self.assertEqual(board['counted'],1);self.assertEqual(board['unused_snapshot_total'],18)
        self.assertEqual(board['shortage_branches'],0)

    def test_roles_cross_branch_and_reminders(self):
        self.setup_boxes();self.count(2)
        for h in (self.headers,self.other,{}, {'Authorization':'Bearer nonsense'}):
            self.assertIn(self.client.get('/api/empty-boxes',headers=h).status_code,(401,403))
        for role in ('SECRETARY','SECRETARIAT'):
            h={'Authorization':'Bearer '+role}
            self.assertEqual(self.client.get('/api/empty-boxes',headers=h).status_code,200)
            self.assertEqual(self.post('reminders',{'branch_id':1},h,True).status_code,200)
        self.assertEqual(self.post('reminders',{'branch_id':2},office=True).status_code,409)
        self.assertEqual(len(self.state()['reminders']),2)
        self.assertEqual(self.state(self.other)['reminders'],[])
        tid=self.dispatch().json()['id']
        task=self.state(self.other)['tasks'][0]
        r,p=self.handoff(task,bid=1)
        self.assertEqual(r.status_code,404)
        p['signer_id']=self.people[1]['id']
        self.assertEqual(self.post('handoffs',p,self.other).status_code,409)
        with self.connection() as c:c.execute('UPDATE branches SET active=0 WHERE id=2')
        self.assertEqual(self.client.get('/api/branch-session/empty-boxes',headers=self.other).status_code,409)

    def test_multiple_sources_partial_fulfillment_early_arrival_and_idempotency(self):
        self.setup_boxes();self.count(1,2);self.count(2,10);self.count(3,8)
        rid=self.request_boxes(10)
        sources=[{'branch_id':2,'quantity':4,'planned_date':'2030-01-07'},{'branch_id':3,'quantity':2,'planned_date':'2030-01-08'}]
        result=self.dispatch(rid,sources);self.assertEqual(result.status_code,200,result.text)
        for bid,h in ((2,self.other),(3,self.third)):
            task=self.state(h)['tasks'][0];r,p=self.handoff(task,bid)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(self.post('handoffs',p,h).status_code,200)
            self.assertEqual(self.post('handoffs',dict(p,quantity=p['quantity']+1),h).status_code,409)
        task=self.state()['tasks'][0]
        r,p=self.handoff(task,1,actual='2030-01-07')
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.post('handoffs',p).status_code,200)
        s=self.state();self.assertEqual(s['balance']['unused'],8)
        self.assertEqual(s['requests'][0]['remaining'],4)
        board=self.client.get('/api/empty-boxes',headers=self.admin).json()
        self.assertEqual(board['shortage_branches'],1)
        self.assertEqual(self.state(self.other)['balance']['unused'],6)
        self.assertEqual(self.state(self.third)['balance']['unused'],6)
        with self.connection() as c:
            t=c.execute('SELECT * FROM empty_box_transfers WHERE id=?',(result.json()['id'],)).fetchone()
            self.assertEqual(t['planned_date'],'2030-01-09');self.assertEqual(t['actual_date'],'2030-01-07')
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM empty_box_movements').fetchone()['n'],3)
            self.assertEqual(c.execute('SELECT SUM(delta) n FROM empty_box_movements').fetchone()['n'],0)
        self.assertEqual(self.post('cancel-request',{'id':rid},self.other).status_code,404)
        self.assertEqual(self.post('cancel-request',{'id':rid}).status_code,200)
        self.assertEqual(self.client.get('/api/empty-boxes',headers=self.admin).json()['shortage_branches'],0)

    def test_overallocation_concurrency_and_reservation_release(self):
        self.setup_boxes();self.count(2,5);rid=self.request_boxes(20)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.dispatch(rid),range(2)))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        self.assertEqual(self.state(self.other)['balance']['unused'],5)
        board=self.client.get('/api/empty-boxes',headers=self.admin).json()
        self.assertEqual(next(b for b in board['branches'] if b['id']==2)['available'],1)
        self.assertEqual(self.count_fails(2,0).status_code,409)
        tid=next(r for r in results if r.status_code==200).json()['id']
        self.assertEqual(self.post('cancel-request',{'id':rid}).status_code,409)
        self.assertEqual(self.post('cancel-transfer',{'id':tid,'reason':'改期'},office=True).status_code,200)
        self.assertEqual(self.dispatch(rid).status_code,200)

    def count_fails(self,bid,unused):
        h={1:self.headers,2:self.other}[bid];b=self.state(h)['balance']
        return self.post('stocktakes',{'unused':unused,'loaded':0,'damaged':0,'actor':'合成人員','version':b['version'],'event_key':main.secrets.token_hex(8)},h)

    def test_schedule_route_changes_closures_and_replan(self):
        self.setup_boxes();self.count(2);tid=self.dispatch().json()['id'];task=self.state(self.other)['tasks'][0]
        with self.connection() as c:
            route=c.execute('SELECT id FROM routes WHERE id<>(SELECT route_id FROM branches WHERE id=2) LIMIT 1').fetchone()['id']
        changed=self.client.patch('/api/branches/2',headers=self.admin,json={'route_id':route})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertTrue(self.state(self.other)['tasks'][0]['needs_replan'])
        self.assertEqual(self.handoff(task)[0].status_code,409)
        self.assertEqual(self.post('reschedule',{'id':tid,'planned_date':'2030-01-09','reason':'改線','source_dates':{task['task_id']:'2030-01-07'}},office=True).status_code,200)
        with self.connection() as c:c.execute("INSERT INTO global_closures(service_date,reason) VALUES('2030-01-07','颱風')")
        self.assertTrue(self.state(self.other)['tasks'][0]['needs_replan'])
        self.assertEqual(self.handoff(task)[0].status_code,409)
        dates=self.client.get('/api/empty-boxes/suggestions?branch_id=2&start=2030-01-07',headers=self.admin).json()['dates']
        self.assertNotIn('2030-01-07',dates);self.assertNotIn('2030-01-12',dates)
        self.assertEqual(self.post('reschedule',{'id':tid,'planned_date':'2030-01-09','reason':'颱風後','source_dates':{task['task_id']:'2030-01-08'}},office=True).status_code,200)
        self.assertFalse(self.state(self.other)['tasks'][0]['needs_replan'])

    def test_difference_reason_and_no_fake_delivery(self):
        self.setup_boxes();self.count(2);tid=self.dispatch().json()['id'];t=self.state(self.other)['tasks'][0]
        with self.connection() as c:before=c.execute('SELECT COUNT(*) n FROM deliveries').fetchone()['n']
        self.assertEqual(self.handoff(t,qty=3)[0].status_code,400)
        self.assertEqual(self.handoff(t,qty=3,reason='少一箱')[0].status_code,200)
        self.assertEqual(self.post('cancel-transfer',{'id':tid,'reason':'取消'},office=True).status_code,409)
        t=self.state()['tasks'][0]
        self.assertEqual(self.handoff(t,1,qty=5,reason='多收')[0].status_code,409)
        self.assertEqual(self.handoff(t,1,qty=3,reason='實收三箱')[0].status_code,200)
        self.assertEqual(self.state()['requests'][0]['remaining'],7)
        with self.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) n FROM deliveries').fetchone()['n'],before)

    def test_late_signature_atomic_delivery_link_and_retry(self):
        self.setup_boxes();self.count(2);tid=self.dispatch(arrival='2030-01-07').json()['id']
        source=self.state(self.other)['tasks'][0]
        # Arrival happened on the original date; signature is submitted next day.
        self.clock.return_value='2030-01-08';self.timestamp.return_value='2030-01-08T08:00:00+00:00'
        did=self.task('late',bid=2)
        p={'signer_id':self.people[2]['id'],'signature':signature(),'note':'原備註','empty_box_handoffs':[{'kind':'PICKUP','task_id':source['task_id'],'quantity':4,'actual_date':'2030-01-07','reason':''}]}
        bad=dict(p,empty_box_handoffs=[dict(p['empty_box_handoffs'][0],quantity=999)])
        response=self.client.post('/api/branch-session/sign',headers=self.other,json=bad)
        self.assertEqual(response.status_code,400,response.text);self.assertIsNone(self.row(did)['branch_signed_at'])
        response=self.client.post('/api/branch-session/sign',headers=self.other,json=p)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.client.post('/api/branch-session/sign',headers=self.other,json=p).status_code,200)
        with self.connection() as c:
            hand=c.execute('SELECT * FROM empty_box_handoffs WHERE delivery_id=?',(did,)).fetchone()
            self.assertEqual(hand['actual_date'],'2030-01-07');self.assertTrue(hand['confirmed_at'].startswith('2030-01-08'))
            self.assertEqual(hand['signature'],self.row(did)['branch_signature'])
        self.assertEqual(self.state(self.other)['balance']['unused'],16)

    def test_migration_repeated_preserves_existing_records_constraints(self):
        self.setup_boxes();self.count(2)
        with self.connection() as c:
            before=[dict(r) for r in c.execute('SELECT * FROM branches').fetchall()]
            snapshots=[dict(r) for r in c.execute('SELECT * FROM empty_box_stocktakes').fetchall()]
            migrate(SchemaTransaction(c,main.USE_POSTGRES));migrate(SchemaTransaction(c,main.USE_POSTGRES))
            self.assertEqual(before,[dict(r) for r in c.execute('SELECT * FROM branches').fetchall()])
            self.assertEqual(snapshots,[dict(r) for r in c.execute('SELECT * FROM empty_box_stocktakes').fetchall()])
        for sql in ("INSERT INTO empty_box_balances VALUES(1,-1,0,0,1)","INSERT INTO empty_box_balances VALUES(999999,1,0,0,1)"):
            with self.assertRaises(Exception):
                with self.connection() as c:c.execute(sql)

    def test_completed_request_overage_budget_and_replay_conflict(self):
        self.setup_boxes();self.count(2,20);rid=self.request_boxes(5)
        transfer=self.dispatch(rid);self.assertEqual(transfer.status_code,200,transfer.text)
        task=self.state(self.other)['tasks'][0]
        self.assertEqual(self.handoff(task,qty=6,reason='超額')[0].status_code,409)
        self.assertEqual(self.handoff(task,qty=5,reason='多交一箱')[0].status_code,200)
        self.assertEqual(self.handoff(self.state()['tasks'][0],bid=1,qty=5,reason='多收一箱')[0].status_code,200)
        r=self.state()['requests'][0]
        self.assertEqual((r['status'],r['remaining']),('COMPLETED',0))
        self.assertEqual(self.client.get('/api/empty-boxes',headers=self.admin).json()['shortage_branches'],0)
        self.assertEqual(self.dispatch(rid).status_code,409)
        _,p=self.count(2,20)
        changed=self.post('stocktakes',dict(p,unused=21),self.other)
        self.assertEqual(changed.status_code,409)

    def test_normal_delivery_shared_signature_and_driver_unchanged_counts(self):
        self.setup_boxes();self.count(2)
        self.dispatch(arrival='2030-01-09')
        did=self.task(bid=2);t=self.state(self.other)['tasks'][0]
        with self.connection() as c:
            device=c.execute("INSERT INTO driver_devices(driver_id,device_key,activated_at) VALUES(1,'box-driver',?)",(main.now(),)).lastrowid
            c.execute('INSERT INTO driver_sessions(token_hash,driver_id,device_id,expires_at) VALUES(?,1,?,?)',(main.thash('box-driver'),device,'2099-01-01T00:00:00+00:00'))
        h={'Authorization':'Bearer box-driver'}
        self.assertIn(self.client.get('/api/empty-boxes',headers=h).status_code,(401,403))
        driver=self.client.get('/api/driver/today',headers=h)
        self.assertEqual(driver.status_code,200,driver.text)
        row=next(r for r in driver.json() if r['id']==did)
        self.assertEqual(row['note_final'],'原始備註');self.assertIn('空箱交回 4 箱',row['note_display'])
        self.assertIsNone(row['outbound_final'])
        p={'signer_id':self.people[2]['id'],'signature':signature(),'note':'原始備註',
           'empty_box_handoffs':[{'kind':'PICKUP','task_id':t['task_id'],'quantity':4,'actual_date':'2030-01-07','reason':''}]}
        r=self.client.post('/api/branch-session/sign',headers=self.other,json=p)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(did)['status'],'WAITING_DRIVER_CONFIRM')
        self.assertEqual(self.row(did)['note_final'],'原始備註')
        with self.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) n FROM empty_box_movements').fetchone()['n'],1)

    def test_branch_stop_suggestions_and_malformed_payload(self):
        self.setup_boxes();self.count(2);self.dispatch()
        with self.connection() as c:c.execute("INSERT INTO delivery_exceptions(service_date,branch_id,exception_type) VALUES('2030-01-07',2,'STOP')")
        self.assertTrue(self.state(self.other)['tasks'][0]['needs_replan'])
        dates=self.client.get('/api/empty-boxes/suggestions?branch_id=2&start=2030-01-07',headers=self.admin).json()['dates']
        self.assertNotIn('2030-01-07',dates)
        self.assertEqual(self.post('requests',[]).status_code,400)
        self.assertEqual(self.post('transfers',[],office=True).status_code,400)

    def test_feature_disabled_fails_closed(self):
        with patch.dict(os.environ,{'MOVINGMATCH_V20_ENABLED':'0'}):
            self.assertEqual(self.client.get('/api/empty-boxes',headers=self.admin).status_code,503)
            self.assertEqual(self.client.get('/api/empty-boxes/config').json(),{'enabled':False})


class V20SQLiteTests(EmptyBoxCases,unittest.TestCase):
    setUp=fixture.V194Tests.setUp
    connection=fixture.V194Tests.connection
