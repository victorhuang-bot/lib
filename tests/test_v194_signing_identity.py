"""Synthetic signing workflows on new temporary SQLite DBs; no real email/network."""
import base64
import io
import unittest
from datetime import date,timedelta
from concurrent.futures import ThreadPoolExecutor
from PIL import Image,ImageDraw
import test_v194 as fixture
from app import main


def signature(ink=True):
    image=Image.new('RGBA',(80,30),(0,0,0,0))
    if ink: ImageDraw.Draw(image).line((3,20,20,5,50,22),fill='black',width=2)
    stream=io.BytesIO();image.save(stream,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()


class SigningIdentityTests(unittest.TestCase):
    setUp=fixture.V194Tests.setUp
    connection=fixture.V194Tests.connection

    def person(self,name='合成人員甲',headers=None):
        response=self.client.post('/api/branch-session/signers',headers=headers or self.headers,json={'name':name})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def task(self,mode='normal',bid=1):
        day=(date.fromisoformat(main.today())-timedelta(days=1 if mode=='late' else 0)).isoformat()
        with self.connection() as c:
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,1,1) ON CONFLICT(service_date,route_id) DO NOTHING',(day,))
            rid=c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=1',(day,)).fetchone()[0]
            c.execute('DELETE FROM deliveries WHERE service_date=? AND branch_id=?',(day,bid))
            status={'normal':'WAITING_BRANCH','late':'LATE_BRANCH_PENDING','correct':'WAITING_BRANCH_CORRECTION'}[mode]
            cur=c.execute('''INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_original,
                document_final,document_return_final,outbound_original,outbound_final,inbound_final)
                VALUES(?,?,?,?,11,11,22,33,33,44)''',(day,rid,bid,status))
            did=cur.lastrowid
            c.execute('UPDATE branch_sessions SET delivery_id=? WHERE branch_id=?',(did,bid))
            if mode=='correct':
                c.execute("UPDATE deliveries SET signer_name='原始合成人員',branch_signature=?,branch_signed_at='2026-01-01T08:00:00',receipt_at='2026-01-01T08:00:00' WHERE id=?",(signature(),did))
                c.execute("INSERT INTO corrections(delivery_id,requested_by_driver_id,requested_at,fields_json,status) VALUES(?,1,?,'[\"document_return\",\"inbound\"]','PENDING')",(did,main.now()))
        return did

    def submit(self,person,mode='normal',**changes):
        payload={'signer_id':person['id'],'signer':person['name'],'signature':signature(),
                 'document':11,'document_return':22,'outbound':33,'inbound':44,'reason':'合成更正原因'}
        payload.update(changes)
        return self.client.post('/api/branch-session/'+('correct' if mode=='correct' else 'sign'),headers=self.headers,json=payload)

    def row(self,did):
        with self.connection() as c:return dict(c.execute('SELECT * FROM deliveries WHERE id=?',(did,)).fetchone())

    def test_first_add_normal_sign_snapshots_identity_signature_time_and_quantities(self):
        self.assertEqual(self.client.get('/api/branch-session/signers',headers=self.headers).json(),[])
        person=self.person();did=self.task();signed=self.submit(person)
        self.assertEqual(signed.status_code,200,signed.text)
        row=self.row(did)
        self.assertEqual(row['signer_name'],person['name']);self.assertEqual(row['branch_signature'],signature())
        self.assertTrue(row['branch_signed_at']);self.assertTrue(row['receipt_at'])
        self.assertEqual([row[k] for k in ('document_final','document_return_final','outbound_final','inbound_final')],[11,22,33,44])
        self.assertEqual(row['status'],'WAITING_DRIVER_CONFIRM')

    def test_late_sign_reuses_roster_and_keeps_late_locked_report_exception(self):
        person=self.person();did=self.task('late')
        with self.connection() as c:c.execute("INSERT INTO daily_reports(service_date,status) VALUES(?,'LOCKED')",(self.row(did)['service_date'],))
        r=self.submit(person,'late');self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.row(did)['status'],'STOP_COMPLETED')
        self.assertEqual(self.row(did)['signer_name'],person['name'])
        self.assertTrue(self.row(did)['driver_confirmed_at'])

    def test_historical_task_selection_uses_same_signing_roster(self):
        person=self.person();did=self.task('late')
        with self.connection() as c:c.execute('UPDATE branch_sessions SET delivery_id=NULL WHERE branch_id=1')
        r=self.client.post('/api/branch-session/select-task',headers=self.headers,json={'delivery_id':did})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.submit(person,'late').status_code,200)
        self.assertEqual(self.row(did)['signer_name'],person['name'])

    def test_correction_preserves_original_identity_signature_and_time(self):
        person=self.person('合成更正人員');did=self.task('correct');before=self.row(did)
        r=self.submit(person,'correct',document_return=7,inbound=None)
        self.assertEqual(r.status_code,200,r.text);after=self.row(did)
        for key in ('signer_name','branch_signature','branch_signed_at','receipt_at','document_final','outbound_final'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(after['correction_signer_name'],person['name'])
        self.assertEqual(after['correction_signature'],signature());self.assertTrue(after['corrected_at'])
        self.assertEqual(after['document_return_final'],7);self.assertEqual(after['inbound_final'],44)
        self.assertEqual(after['status'],'WAITING_DRIVER_RECONFIRM')

    def test_cross_branch_person_rejected_in_all_three_flows(self):
        foreign=self.person(headers=self.other)
        for mode in ('normal','late','correct'):
            with self.subTest(mode=mode):
                did=self.task(mode);before=self.row(did)
                r=self.submit(foreign,mode);self.assertEqual(r.status_code,409,r.text)
                self.assertEqual(before,self.row(did))
        self.assertEqual(self.client.get('/api/branch-session/signers',headers=self.headers).json(),[])

    def test_deactivated_person_rejected_in_all_three_flows(self):
        person=self.person()
        self.assertEqual(self.client.patch('/api/branch-session/signers/'+str(person['id']),headers=self.headers,json={'active':False}).status_code,200)
        self.assertEqual(self.client.get('/api/branch-session/signers',headers=self.headers).json(),[])
        for mode in ('normal','late','correct'):
            with self.subTest(mode=mode):
                did=self.task(mode);before=self.row(did)
                r=self.submit(person,mode);self.assertEqual(r.status_code,409,r.text)
                self.assertEqual(before,self.row(did))

    def test_rename_preserves_signed_history_and_stale_name_is_rejected(self):
        person=self.person();old=self.task('late');self.assertEqual(self.submit(person,'late').status_code,200)
        before=self.row(old)
        r=self.client.patch('/api/branch-session/signers/'+str(person['id']),headers=self.headers,json={'name':'改名後合成人員'})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(before,self.row(old))
        did=self.task();self.assertEqual(self.submit(person).status_code,409)
        self.assertIsNone(self.row(did)['branch_signature'])
        self.assertEqual(self.submit(r.json()).status_code,200)
        self.assertEqual(self.row(did)['signer_name'],'改名後合成人員');self.assertEqual(before,self.row(old))

    def test_deactivate_preserves_original_and_corrected_history(self):
        person=self.person();did=self.task('correct');self.assertEqual(self.submit(person,'correct').status_code,200)
        before=self.row(did)
        self.client.patch('/api/branch-session/signers/'+str(person['id']),headers=self.headers,json={'active':False})
        self.assertEqual(before,self.row(did))
        self.assertEqual(self.client.get('/api/branch-session/signers',headers=self.headers).json(),[])

    def test_missing_person_and_blank_or_malformed_signatures_cannot_submit(self):
        person=self.person()
        for mode in ('normal','late','correct'):
            for changes in ({'signer_id':None},{'signer_id':True},{'signer_id':'1'},
                            {'signature':''},{'signature':signature(False)},
                            {'signature':'data:image/png;base64,bm90LWEtcmVhbC1pbWFnZQ=='},
                            {'signature':'https://invalid.example/signature.png'}):
                with self.subTest(mode=mode,changes=changes):
                    did=self.task(mode);before=self.row(did)
                    r=self.submit(person,mode,**changes);self.assertEqual(r.status_code,400,r.text)
                    self.assertEqual(before,self.row(did))

    def test_canonical_name_cannot_be_forged(self):
        person=self.person();did=self.task()
        self.assertEqual(self.submit(person,signer='偽造姓名').status_code,409)
        self.assertIsNone(self.row(did)['signer_name'])
        response=self.submit(person);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.row(did)['signer_name'],person['name'])

    def test_inactive_branch_cannot_sign_or_correct(self):
        person=self.person()
        for mode in ('normal','correct'):
            did=self.task(mode)
            with self.connection() as c:c.execute('UPDATE branches SET active=0 WHERE id=1')
            self.assertEqual(self.submit(person,mode).status_code,403)
            with self.connection() as c:c.execute('UPDATE branches SET active=1 WHERE id=1')

    def test_session_cannot_sign_other_branch_delivery(self):
        person=self.person();did=self.task(bid=2)
        with self.connection() as c:c.execute('UPDATE branch_sessions SET delivery_id=? WHERE branch_id=1',(did,))
        before=self.row(did);self.assertEqual(self.submit(person).status_code,404)
        self.assertEqual(before,self.row(did))

    def test_duplicate_sign_does_not_overwrite_original(self):
        person=self.person();did=self.task();self.assertEqual(self.submit(person).status_code,200)
        before=self.row(did)
        r=self.submit(person,document=999)
        self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['already_signed'])
        self.assertEqual(before,self.row(did))

    def test_simultaneous_deactivate_and_sign_are_serialized(self):
        person=self.person();did=self.task()
        def deactivate():return self.client.patch('/api/branch-session/signers/'+str(person['id']),headers=self.headers,json={'active':False})
        with ThreadPoolExecutor(max_workers=2) as pool:
            signed=pool.submit(self.submit,person);stopped=pool.submit(deactivate)
            results=(signed.result(),stopped.result())
        self.assertEqual(results[1].status_code,200,results[1].text)
        self.assertIn(results[0].status_code,(200,409),results[0].text)
        row=self.row(did)
        self.assertEqual(bool(row['branch_signature']),results[0].status_code==200)
        if row['branch_signature']:self.assertEqual(row['signer_name'],person['name'])
