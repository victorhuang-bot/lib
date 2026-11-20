"""Issue #45. Reuse the API cases with isolated SQLite and PostgreSQL fixtures."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import json
import unittest

from openpyxl import load_workbook
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

    def prepare_stop(self, day=None):
        day = day or main.today()
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

    def test_legacy_unfilled_document_requires_explicit_count(self):
        did, _, person = self.prepare_stop()
        sign = '/api/branch-session/sign'
        with self.connection() as c:
            c.execute('UPDATE deliveries SET document_original=NULL,document_final=NULL WHERE id=?', (did,))
        for status in ('WAITING_BRANCH', 'LATE_BRANCH_PENDING'):
            with self.connection() as c:
                c.execute('UPDATE deliveries SET status=? WHERE id=?', (status, did))
            before = self.row(did)
            payload = self.payload(did, person)
            del payload['document']
            for request in (payload, {**payload, 'document': None}):
                result = self.post(sign, request)
                self.assertEqual(result.status_code, 400, result.text)
                self.assertIn('實收數量', result.json()['detail'])
                self.assertEqual(self.row(did), before)

        missing_reason = self.post(sign, {**payload, 'document': 0, 'reason': ''})
        self.assertEqual(missing_reason.status_code, 400, missing_reason.text)
        self.ok(self.post(sign, {**payload, 'document': 0}))
        proposal = self.driver_proposal(did)
        self.assertEqual(proposal['document'], 0)
        self.ok(self.confirm(did, proposal['id']))
        final = self.row(did)
        self.assertEqual((final['status'], final['document_original'], final['document_final']),
                         ('STOP_COMPLETED', None, 0))

    def test_previous_month_locked_legacy_receipt_amends_and_relocks(self):
        day = (date.fromisoformat(main.today()).replace(day=1) - timedelta(days=1)).isoformat()
        did, route, person = self.prepare_stop(day)
        stamp = day+'T08:00:00+08:00'
        old_branch, old_route, old_final = signature(4), signature(7), signature(8)
        with self.connection() as c:
            c.execute("""UPDATE deliveries SET status='STOP_COMPLETED',document_final=4,signer_name=?,
                         branch_signature=?,branch_signed_at=?,driver_confirmed_at=?,receipt_at=? WHERE id=?""",
                      ('舊版簽收人', old_branch, stamp, stamp, stamp, did))
            c.execute("""UPDATE daily_routes SET status='DRIVER_SIGNED',driver_signature=?,driver_signed_at=?,
                         secretary_signature=?,secretary_signed_at=? WHERE id=?""",
                      (signature(6), stamp, old_route, stamp, route))
            c.execute("""INSERT INTO daily_reports(service_date,status,secretary_signature,secretary_signed_at,locked_at)
                         VALUES(?,'LOCKED',?,?,?)""", (day, old_final, stamp, stamp))
            c.execute('UPDATE branch_sessions SET delivery_id=NULL WHERE branch_id=1')

        detail_url = f'/api/history/receipts/{did}'
        legacy = self.ok(self.client.get(detail_url, headers=self.admin))
        self.assertEqual(legacy['audit'], [])
        self.assertEqual(legacy['office_audit'], [])
        self.assertEqual(legacy['delivery']['document_final'], 4)
        past = self.ok(self.client.get('/api/branch-session/history?month='+day[:7], headers=self.headers))
        self.assertEqual(next(r for r in past['records'] if r['id']==did)['document_final'], 4)
        signatures = self.ok(self.client.get(f'/api/branch-session/history/{did}/signatures', headers=self.headers))
        self.assertEqual(signatures['branch_signature'], old_branch)

        def download(ext, official=True):
            url=f'/api/reports/daily.{ext}?service_date={day}&official={str(official).lower()}'
            return self.client.get(url, headers=self.admin)

        def official_count(expected):
            book_response = download('xlsx')
            self.assertEqual(book_response.status_code, 200, book_response.text if not book_response.is_success else '')
            with io.BytesIO(book_response.content) as stream:
                workbook = load_workbook(stream, read_only=True)
                rows = list(workbook.active.values)
                workbook.close()
            self.assertEqual(next(row[3] for row in rows if row[0]=='全部總計'), expected)
            pdf = download('pdf')
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.content.startswith(b'%PDF'))

        official_count(4)
        self.ok(self.post('/api/branch-session/select-task', {'delivery_id': did}))
        self.ok(self.post('/api/branch-session/correct-document', self.payload(did, person, document=3, offset=5)))
        status_url = '/api/secretary/sign-status?service_date='+day
        status = self.ok(self.client.get(status_url, headers=self.admin))
        self.assertEqual(status['report']['status'], 'AMENDING')
        self.assertEqual(self.row(did)['status'], 'WAITING_DRIVER_RECONFIRM')
        for ext in ('xlsx', 'pdf'):
            self.assertEqual(download(ext).status_code, 409)
            self.assertEqual(download(ext, official=False).status_code, 200)

        proposal = self.driver_proposal(did)
        self.assertEqual(proposal['document'], 3)
        self.ok(self.confirm(did, proposal['id']))
        self.assertEqual(self.row(did)['document_final'], 3)
        self.assertEqual(download('xlsx').status_code, 409)
        final_payload = {'signature': signature(10), 'review_revision': status['review_revision'], 'service_date': day}
        self.assertEqual(self.post('/api/secretary/final-sign', final_payload, 'SECRETARY').status_code, 409)
        revision = next(r['review_revision'] for r in status['routes'] if r['id']==route)
        self.ok(self.post(f'/api/routes/{route}/secretary-sign',
                          {'signature': signature(9), 'review_revision': revision}, 'SECRETARY'))
        self.assertEqual(download('pdf').status_code, 409)
        self.ok(self.post('/api/secretary/final-sign', final_payload, 'SECRETARY'))
        official_count(3)

        detail = self.ok(self.client.get(detail_url, headers=self.auth('SECRETARIAT')))
        self.assertEqual(detail['delivery']['report_status'], 'LOCKED')
        self.assertEqual(detail['delivery']['branch_signature'], old_branch)
        self.assertEqual(detail['delivery']['correction_signature'], signature(5))
        self.assertEqual(detail['delivery']['secretary_signature'], signature(9))
        self.assertEqual(detail['delivery']['final_secretary_signature'], signature(10))
        archived = {r['entity_type']: json.loads(r['before_json'])
                    for r in detail['office_audit'] if r['action']=='OFFICE_DOCUMENT_REVIEW_INVALIDATED'}
        self.assertEqual(archived['DAILY_ROUTE']['secretary_signature'], old_route)
        self.assertEqual(archived['DAY']['secretary_signature'], old_final)
        self.assertEqual(archived['DAY']['locked_at'], stamp)
        self.assertEqual(archived['DAILY_ROUTE']['secretary_signed_at'], stamp)
        submitted = next(r for r in detail['audit'] if r['action']=='BRANCH_DOCUMENT_PROPOSED')
        self.assertEqual(json.loads(submitted['before_json'])['driver_confirmed_at'], stamp)

    def test_mixed_route_codes_keep_report_and_dashboard_order(self):
        did, first_route, _ = self.prepare_stop()
        day = main.today()
        quantities = {'1': 5, '2': 6, '10': 7, '2A': 8, 'SECOND': 9}
        expected = list(quantities)
        with self.connection() as c:
            c.execute('UPDATE routes SET active=0 WHERE code NOT IN (?,?,?,?,?)', tuple(expected))
            c.execute('DELETE FROM daily_routes WHERE service_date=? AND id<>?', (day, first_route))
            c.execute("UPDATE deliveries SET status='STOP_COMPLETED' WHERE id=?", (did,))
            for code in ('SECOND', '10', '2A', '2'):
                c.execute('INSERT INTO routes(code,name,active) VALUES(?,?,1) '
                          'ON CONFLICT(code) DO UPDATE SET active=1', (code, code))
                rid = c.execute('SELECT id FROM routes WHERE code=?', (code,)).fetchone()['id']
                route = c.execute('INSERT INTO daily_routes(service_date,route_id,driver_id) VALUES(?,?,1)',
                                  (day, rid)).lastrowid
                branch = c.execute("INSERT INTO branches(code,name,route_id,active) VALUES(?,?,?,1)",
                                   ('REPORT_'+code, '報表 '+code, rid)).lastrowid
                c.execute("""INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,document_final)
                             VALUES(?,?,?,'STOP_COMPLETED',?)""", (day, route, branch, quantities[code]))
            c.execute("INSERT INTO daily_reports(service_date,status) VALUES(?,'LOCKED')", (day,))

        month = self.ok(self.client.get('/api/reports/monthly-route-summary?month='+day[:7], headers=self.admin))
        self.assertEqual(month['routes'], expected)
        self.assertEqual(list(month['days'][0]['routes']), expected)
        self.assertEqual({code: row['document'] for code, row in month['totals'].items()}, quantities)
        self.assertEqual(month['grand_total']['document'], 35)
        self.assertEqual(month['document_corrections_pending'], 0)

        daily = self.client.get('/api/reports/daily.xlsx?service_date='+day, headers=self.admin)
        self.assertEqual(daily.status_code, 200, daily.text if daily.status_code != 200 else '')
        with io.BytesIO(daily.content) as stream:
            workbook = load_workbook(stream, read_only=True)
            rows = list(workbook.active.values)
            workbook.close()
        route_column = rows[1].index('路線')
        document_column = rows[1].index('公文－總館送出')
        self.assertEqual([(row[route_column], row[document_column]) for row in rows[2:]], list(quantities.items()))

        dashboard = self.ok(self.client.get('/api/dashboard/today', headers=self.admin))
        self.assertEqual([r['code'] for r in dashboard['routes']], expected)
        self.assertEqual((dashboard['total'], dashboard['completed']), (5, 5))
        deliveries = self.ok(self.client.get('/api/dashboard/deliveries', headers=self.admin))
        self.assertEqual([(r['route_code'], r['document_final']) for r in deliveries], list(quantities.items()))
        sign_status = self.ok(self.client.get('/api/secretary/sign-status?service_date='+day, headers=self.admin))
        self.assertEqual([r['code'] for r in sign_status['routes']], expected)

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
