"""Issue #45. Reuse the API cases with isolated SQLite and PostgreSQL fixtures."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import unittest

from PIL import Image, ImageDraw
from fastapi import HTTPException

import test_v194 as fixture
from app import main


def signature(offset):
    image = Image.new('RGBA', (80, 30))
    ImageDraw.Draw(image).line((offset, 18, 33, 6, 71, 21), fill='black', width=2)
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()


class DocumentCorrectionCases:
    @staticmethod
    def auth(token):
        return {'Authorization': 'Bearer ' + token}

    def prepare_stop(self):
        day = main.today()
        with self.connection() as c:
            c.execute('INSERT INTO drivers(id,name,active) VALUES(2,?,1) ON CONFLICT(id) DO NOTHING', ('司機乙',))
            c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,1,1) '
                      'ON CONFLICT(service_date,route_id) DO NOTHING', (day,))
            route_id = c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=1', (day,)).fetchone()['id']
            c.execute('UPDATE daily_routes SET driver_id=1 WHERE id=?', (route_id,))
            c.execute('DELETE FROM deliveries WHERE service_date=?', (day,))
            c.execute("UPDATE routes SET code='1' WHERE id=1")
            did = c.execute("""INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_original,
                document_final,document_return_final,outbound_original,outbound_final,inbound_final)
                VALUES(?,?,1,'WAITING_BRANCH',5,5,22,33,33,44)""", (day, route_id)).lastrowid
            c.execute('UPDATE branch_sessions SET delivery_id=? WHERE branch_id=1', (did,))
            for driver in (1, 2):
                c.execute('INSERT INTO driver_sessions(token_hash,driver_id,expires_at) VALUES(?,?,?)',
                          (main.thash(f'driver-{driver}'), driver, main.future_iso(hours=1)))
            for role in ('SECRETARY', 'SECRETARIAT'):
                user = c.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',
                                 ('test-'+role, 'unused', role)).lastrowid
                c.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                          (main.thash(role), user, main.future_iso(hours=1)))
        person = self.client.post('/api/branch-session/signers', headers=self.headers, json={'name': '合成分館人員'})
        self.assertEqual(person.status_code, 200, person.text)
        return did, route_id, person.json()['id']

    def row(self, did):
        with self.connection() as c:
            return dict(c.execute('SELECT * FROM deliveries WHERE id=?', (did,)).fetchone())

    def payload(self, did, person, document=4, offset=4, **extra):
        return {'document': document, 'reason': '依現場實收清點', 'signer_id': person,
                'signature': signature(offset), 'delivery_id': did, 'row_version': self.row(did)['row_version'],
                'document_return': 22, 'outbound': 33, 'inbound': 44, **extra}

    def post(self, url, payload, token='branch-one'):
        return self.client.post(url, headers=self.auth(token), json=payload)

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def driver_proposal(self, did):
        rows = self.ok(self.client.get('/api/driver/today', headers=self.auth('driver-1')))
        return next(r['document_correction'] for r in rows if r['id'] == did)

    def confirm(self, did, proposal, token='driver-1'):
        return self.post(f'/api/driver/deliveries/{did}/confirm', {'document_correction_id': proposal}, token)

    def assert_report(self, did, document, pending):
        day = self.row(did)['service_date']
        month = self.ok(self.client.get('/api/reports/monthly-route-summary', headers=self.admin))
        self.assertEqual(month['grand_total']['document'], document or 0)
        self.assertEqual(month['document_corrections_pending'], pending)
        dashboard = self.ok(self.client.get('/api/dashboard/monthly-route-cumulative', headers=self.admin))
        self.assertEqual(dashboard['grand_total']['document'], document or 0)
        with self.connection() as c:
            daily = main.official_daily_payload(c, day)
            rows = main.report_rows(c, day, day)
        self.assertEqual(daily['grand']['document'], document or 0)
        row = next(r for r in rows if r['公文收回'] == 22)
        self.assertEqual(row['公文送出'], document)
        self.assertEqual(row['分館簽收狀態']=='公文更正待確認', bool(pending))

    def test_document_5_to_4_to_3_and_office_resigning(self):
        did, route, person = self.prepare_stop()
        sign = '/api/branch-session/sign'
        propose = '/api/branch-session/correct-document'
        office = f'/api/routes/{route}/secretary-sign'
        final = '/api/secretary/final-sign'
        day = main.today()
        self.assertEqual(self.post(sign, self.payload(did, person, reason=' ')).status_code, 400)
        self.ok(self.post(sign, self.payload(did, person)))
        self.assert_report(did, None, 1)
        first = self.driver_proposal(did)
        self.assertEqual(first['document'], 4)
        self.ok(self.confirm(did, first['id']))
        self.assert_report(did, 4, 0)
        self.assertEqual(self.row(did)['document_original'], 5)
        with self.connection() as c:
            c.execute("UPDATE daily_routes SET status='DRIVER_SIGNED' WHERE id=?", (route,))
        status_url = '/api/secretary/sign-status?service_date=' + day
        initial = self.ok(self.client.get(status_url, headers=self.admin))
        initial_revision = next(r['review_revision'] for r in initial['routes'] if r['id']==route)
        self.ok(self.post(office, {'signature': signature(7), 'review_revision': initial_revision}, 'SECRETARY'))
        self.ok(self.post(final, {'signature': signature(8), 'review_revision': initial['review_revision']}, 'SECRETARY'))
        old = self.row(did)

        correction = self.payload(did, person, document=3, offset=5)
        self.assertEqual(self.post(propose, {**correction, 'signature': signature(4)}).status_code, 400)
        self.ok(self.post(propose, correction))
        self.assertEqual(self.post(propose, correction).status_code, 409)
        self.assertEqual(self.row(did)['status'], 'WAITING_DRIVER_RECONFIRM')
        self.assertIsNone(self.row(did)['driver_confirmed_at'])
        self.assert_report(did, None, 1)
        status = self.ok(self.client.get(status_url, headers=self.admin))
        self.assertEqual(status['report']['status'], 'AMENDING')
        revision = next(r['review_revision'] for r in status['routes'] if r['id']==route)
        self.assertEqual(self.post(office, {'signature': signature(9), 'review_revision': revision}, 'SECRETARY').status_code, 409)
        self.assertEqual(self.post(final, {'signature': signature(10)}, 'SECRETARY').status_code, 409)
        second = self.driver_proposal(did)
        self.assertEqual(second['document'], 3)
        self.assertEqual(self.confirm(did, first['id']).status_code, 409)
        self.assertEqual(self.confirm(did, second['id'], 'driver-2').status_code, 403)
        self.ok(self.confirm(did, second['id']))
        self.assert_report(did, 3, 0)
        self.assertEqual(self.post(office, {'signature': signature(9), 'review_revision': initial_revision}, 'SECRETARY').status_code, 409)
        self.assertEqual(self.post(office, {'signature': signature(7), 'review_revision': revision}, 'SECRETARY').status_code, 400)
        self.ok(self.post(office, {'signature': signature(9), 'review_revision': revision}, 'SECRETARY'))
        final_payload = {'signature': signature(10), 'review_revision': status['review_revision'], 'service_date': day}
        self.assertEqual(self.post(final, {**final_payload, 'signature': signature(8)}, 'SECRETARY').status_code, 400)
        self.ok(self.post(final, final_payload, 'SECRETARY'))
        final_row = self.row(did)
        self.assertEqual((final_row['document_original'], final_row['document_final']), (5, 3))
        for field in ('branch_signed_at','branch_signature','receipt_at',
                      'document_return_final','outbound_final','inbound_final'):
            self.assertEqual(final_row[field], old[field])
        history = self.ok(self.client.get(f'/api/history/receipts/{did}', headers=self.auth('SECRETARIAT')))
        archived = {json.loads(r['before_json']).get('secretary_signature')
                    for r in history['office_audit'] if r['action']=='OFFICE_DOCUMENT_REVIEW_INVALIDATED'}
        self.assertEqual(archived - {None}, {signature(7), signature(8)})
        self.assertEqual(history['delivery']['report_status'], 'LOCKED')

    def test_document_parallel_replay_and_branch_scope(self):
        did, _, person = self.prepare_stop()
        self.ok(self.post('/api/branch-session/sign', self.payload(did, person, document=5)))
        self.ok(self.confirm(did, None))
        payload = self.payload(did, person, document=3, offset=5)
        path = '/api/branch-session/correct-document'
        self.assertIn(self.post(path, payload, 'branch-two').status_code, (403, 409))
        self.assertEqual(self.post(path, {**payload, 'row_version': -1}).status_code, 409)
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(lambda _: self.post(path, payload).status_code, range(2)))
        self.assertEqual(sorted(statuses), [200,409])
        proposal = self.driver_proposal(did)
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(lambda _: self.confirm(did, proposal['id']).status_code, range(2)))
        self.assertEqual(sorted(statuses), [200,409])


class BranchDocumentCorrectionTests(DocumentCorrectionCases, unittest.TestCase):
    setUp = fixture.V194Tests.setUp
    connection = fixture.V194Tests.connection


class BranchDocumentValidationTests(unittest.TestCase):
    def test_quantity_accepts_zero_and_rejects_other_types(self):
        self.assertEqual(main.branch_document_quantity(0), 0)
        for value in (-1, True, 1.5, '5', 2**31):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                main.branch_document_quantity(value)

    def test_review_requires_visible_handwriting(self):
        main.validate_branch_signature(signature(4))
        with self.assertRaises(HTTPException):
            main.validate_branch_signature('data:image/png;base64,AAAA')
