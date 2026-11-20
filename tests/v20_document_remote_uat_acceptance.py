"""Explicit, additive Issue #45 acceptance on the pinned remote UAT only."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from unicodedata import normalize
from uuid import uuid4
import warnings
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://movingmatch-v20-uat.onrender.com'
CASES = (
    'preflight', 'synthetic_fixtures', 'first_signature_5_to_4',
    'permissions_validation_and_conflicts', 'correction_4_to_3_and_reports',
    'previous_month_locked_reapproval', 'legacy_null_and_history',
    'existing_records_preserved',
)
SNAPSHOT_TABLES = (
    'routes', 'branches', 'drivers', 'driver_devices', 'users', 'daily_routes',
    'deliveries', 'daily_reports', 'delivery_items', 'corrections',
    'delivery_driver_assignments', 'route_segment_signatures', 'branch_signers',
    'audit_logs',
)


def require(condition):
    if not condition:
        raise ValueError('Acceptance condition failed')


def credentials(path):
    if path:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'r',
                       encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_mode & 0o077 == 0 and 0 < info.st_size <= 65536
                    and not path.resolve().is_relative_to(ROOT))
            result = json.load(stream)
    else:
        result = {'uat-head-office': os.environ.get('MOVINGMATCH_V20_UAT_ADMIN_PASSWORD'),
                  'uat-secretary': os.environ.get('MOVINGMATCH_V20_UAT_SECRETARY_PASSWORD')}
    require(all(isinstance(result.get(name), str) and result[name]
                for name in ('uat-head-office', 'uat-secretary')))
    return result


def check_revision(expected):
    require(re.fullmatch(r'[0-9a-f]{40}', expected) is not None)
    command = ['git', '-C', str(ROOT)]
    head = subprocess.check_output(command + ['rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True)
    top = subprocess.check_output(command + ['rev-parse', '--show-toplevel'], stderr=subprocess.DEVNULL, text=True)
    require(head.strip() == expected and Path(top.strip()).resolve() == ROOT)
    subprocess.run(command + ['ls-files', '--error-unmatch', '--', Path(__file__).relative_to(ROOT).as_posix()],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(command + ['diff', '--quiet', 'HEAD', '--'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pin_digest(value):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', value.encode(), salt, 120000)
    return '$'.join(base64.b64encode(part).decode() for part in (salt, digest))


def ink(run_id, index):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (200, 70), 'white')
    points = [(12 + i * 20, 10 + int(run_id[i], 16) * 3) for i in range(8)]
    points.append((185, 6 + index * 4))
    ImageDraw.Draw(image).line(points, fill='black', width=4)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()


class Acceptance:
    def __init__(self, db, commit, run_id):
        self.db, self.commit, self.run_id = db, commit, run_id
        self.tag = 'UAT45-' + run_id.upper()

    def request(self, path, token=None, payload=None, *, method=None, expected=200):
        import requests
        require(path.startswith('/') and not path.startswith('//'))
        headers = {'Cache-Control': 'no-cache', 'X-MovingMatch-UAT-Commit': self.commit}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        with requests.Session() as session:
            session.trust_env = False
            response = session.request(method or ('POST' if payload is not None else 'GET'),
                                       ORIGIN + path, headers=headers, json=payload,
                                       allow_redirects=False, timeout=(10, 45), verify=True)
        require(response.status_code in (expected if isinstance(expected, tuple) else (expected,)))
        return response

    def health(self):
        from app.uat_init import PROJECT_ID, BRANCH_ID, DATABASE
        from app.uat_serve import RENDER_UAT_SERVICE
        target = {'commit': self.commit, 'service': RENDER_UAT_SERVICE,
                  'project': PROJECT_ID, 'branch': BRANCH_ID, 'database': DATABASE}
        state = self.request('/health').json()
        require(state.get('ok') is True and state.get('uat') is True and state.get('uat_target') == target)

    def snapshot(self, upper=None):
        result = {}
        with self.db.transaction():
            self.db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            for table in SNAPSHOT_TABLES:
                limit = upper[table] if upper is not None else self.db.execute(
                    f'SELECT COALESCE(MAX(id),0) n FROM {table}').fetchone()['n']
                rows = self.db.execute(f'SELECT id,md5(row_to_json(t)::text) digest FROM {table} t '
                                       'WHERE id<=%s ORDER BY id', (limit,)).fetchall()
                result[table] = (limit, rows)
        return result

    def fixtures(self):
        self.health()
        now = datetime.now(ZoneInfo('Asia/Taipei'))
        last_month = now.date().replace(day=1) - timedelta(days=1)
        self.branch_pin, self.other_pin, self.driver_pin = (secrets.token_urlsafe(24) for _ in range(3))
        self.device_key = secrets.token_urlsafe(32)
        self.branch_code, self.other_code = self.tag + '-B', self.tag + '-X'
        db = self.db
        with db.transaction():
            db.execute('SELECT pg_advisory_xact_lock(194020)')
            dates = []
            day = last_month
            while day.month == last_month.month and len(dates) < 3:
                value = day.isoformat()
                if not db.execute('''SELECT 1 WHERE EXISTS(SELECT 1 FROM daily_routes WHERE service_date=%s)
                    OR EXISTS(SELECT 1 FROM daily_reports WHERE service_date=%s)
                    OR EXISTS(SELECT 1 FROM deliveries WHERE service_date=%s)
                    OR EXISTS(SELECT 1 FROM global_closures WHERE service_date=%s)''', (value,)*4).fetchone():
                    dates.append(value)
                day -= timedelta(days=1)
            require(len(dates) == 3)
            self.route_id = db.execute('INSERT INTO routes(code,name) VALUES(%s,%s) RETURNING id',
                                       (self.tag, self.tag)).fetchone()['id']
            self.driver_id = db.execute('INSERT INTO drivers(name,pin_hash) VALUES(%s,%s) RETURNING id',
                                        (self.tag, pin_digest(self.driver_pin))).fetchone()['id']
            db.execute('INSERT INTO driver_devices(driver_id,device_key,activated_at) VALUES(%s,%s,%s)',
                       (self.driver_id, self.device_key, now.isoformat()))
            for code, pin in ((self.branch_code, self.branch_pin), (self.other_code, self.other_pin)):
                row = db.execute('''INSERT INTO branches(code,name,route_id,stop_order,pin_hash,delivery_weekdays)
                    VALUES(%s,%s,%s,1,%s,%s) RETURNING id''',
                    (code, code, self.route_id, pin_digest(pin), '1')).fetchone()
                if code == self.branch_code:
                    self.branch_id = row['id']
            self.stops = []
            for i, day in enumerate(dates):
                stamp = day + 'T08:00:00+08:00'
                route = db.execute('''INSERT INTO daily_routes(service_date,route_id,driver_id,status,
                    driver_signature,driver_signed_at) VALUES(%s,%s,%s,'DRIVER_SIGNED',%s,%s) RETURNING id''',
                    (day, self.route_id, self.driver_id, ink(self.run_id, 0), stamp)).fetchone()['id']
                did = db.execute('''INSERT INTO deliveries(service_date,daily_route_id,branch_id,status,note_final)
                    VALUES(%s,%s,%s,'LATE_BRANCH_PENDING',%s) RETURNING id''',
                    (day, route, self.branch_id, self.tag)).fetchone()['id']
                self.stops.append((day, route, did))
                if i == 1:
                    db.execute('''UPDATE deliveries SET status='STOP_COMPLETED',document_original=5,document_final=4,
                        branch_signature=%s,branch_signed_at=%s,driver_confirmed_at=%s,receipt_at=%s,
                        signer_name=%s WHERE id=%s''', (ink(self.run_id, 1), stamp, stamp, stamp, self.tag, did))
                    db.execute('UPDATE daily_routes SET secretary_signature=%s,secretary_signed_at=%s WHERE id=%s',
                               (ink(self.run_id, 2), stamp, route))
                    self.insert_locked_report(day, ink(self.run_id, 3), stamp)

    def insert_locked_report(self, day, signature, stamp):
        self.db.execute('''INSERT INTO daily_reports(service_date,status,secretary_signature,
            secretary_signed_at,locked_at) VALUES(%s,'LOCKED',%s,%s,%s)''', (day, signature, stamp, stamp))

    def login(self, passwords):
        self.health()
        self.admin, self.office = (
            self.request('/api/auth/login', payload={'username': user, 'password': passwords[user]}).json()['token']
            for user in ('uat-head-office', 'uat-secretary'))
        self.branch, self.other = (
            self.request('/api/branch-session/desktop-login', payload={'branch_code': code, 'pin': pin}).json()['session']
            for code, pin in ((self.branch_code, self.branch_pin), (self.other_code, self.other_pin)))
        self.driver = self.request('/api/driver/unlock', payload={'device_key': self.device_key,
                                                                  'pin': self.driver_pin}).json()['token']
        self.signer = self.request('/api/branch-session/signers', self.branch, {'name': self.tag}).json()['id']

    def select(self, did):
        self.request('/api/branch-session/select-task', self.branch, {'delivery_id': did})

    def receipt(self, did):
        return self.request(f'/api/history/receipts/{did}', self.admin).json()

    def payload(self, did, count, image):
        row = self.receipt(did)['delivery']
        return {'delivery_id': did, 'row_version': row['row_version'], 'document': count,
                'reason': self.tag, 'note': self.tag, 'signature': ink(self.run_id, image), 'signer_id': self.signer}

    def confirm(self, did, count):
        pending = self.request('/api/driver/today', self.driver).json()
        proposal = next(row['document_correction'] for row in pending if row['id'] == did)
        require(proposal['document'] == count)
        path = f'/api/driver/deliveries/{did}/confirm'
        body = {'document_correction_id': proposal['id']}
        self.request(path, self.driver, body)
        self.request(path, self.driver, body, expected=409)
        require(self.receipt(did)['delivery']['document_final'] == count)

    def status(self, day):
        return self.request('/api/secretary/sign-status?service_date=' + day, self.admin).json()

    def route_signature(self, route, state, image):
        revision = next(r['review_revision'] for r in state['routes'] if r['id'] == route)
        self.request(f'/api/routes/{route}/secretary-sign', self.office,
                     {'signature': ink(self.run_id, image), 'review_revision': revision})

    def verify_day_scope(self, day, route, did):
        routes = self.db.execute('SELECT id FROM daily_routes WHERE service_date=%s', (day,)).fetchall()
        deliveries = self.db.execute('SELECT id FROM deliveries WHERE service_date=%s', (day,)).fetchall()
        require(routes == [{'id': route}] and deliveries == [{'id': did}])

    def relock(self, day, route, did, image):
        self.verify_day_scope(day, route, did)
        state = self.status(day)
        require(state['report']['status'] == 'AMENDING')
        self.route_signature(route, state, image)
        self.request('/api/secretary/final-sign', self.office, {
            'signature': ink(self.run_id, image + 1), 'review_revision': state['review_revision'], 'service_date': day})
        require(self.status(day)['report']['status'] == 'LOCKED')

    def download(self, day, count=None, allowed=True):
        from openpyxl import load_workbook
        from pypdf import PdfReader
        for ext in ('xlsx', 'pdf'):
            path = f'/api/reports/daily.{ext}?service_date={day}'
            official = self.request(path + '&official=true', self.admin, expected=200 if allowed else 409)
            if not allowed:
                self.request(path, self.admin)
            elif ext == 'pdf':
                require(official.content.startswith(b'%PDF'))
                with io.BytesIO(official.content) as stream:
                    content = ''.join(page.extract_text() for page in PdfReader(stream).pages)
                content = re.sub(r'\s+', '', normalize('NFKC', content))
                require(f'全部總計:公文-總館送出{count}|' in content)
            else:
                with io.BytesIO(official.content) as stream:
                    book = load_workbook(stream, read_only=True)
                    try:
                        total = next(row[3] for row in book.active.values if row[0] == '全部總計')
                        require(total == count)
                    finally:
                        book.close()

    def first_signature_5_to_4(self):
        day, route, did = self.stops[0]
        self.verify_day_scope(day, route, did)
        self.select(did)
        self.request(f'/api/secretary/deliveries/{did}/document', self.office, {'qty': 5}, method='PATCH')
        self.request('/api/branch-session/sign', self.branch, self.payload(did, 4, 4))
        self.confirm(did, 4)
        state = self.status(day)
        self.route_signature(route, state, 5)
        # Production forbids first-time final signing for past dates. Seed the
        # previous day-level signature on this vacant synthetic date only.
        self.health()
        with self.db.transaction():
            self.db.execute('SELECT pg_advisory_xact_lock(194020)')
            self.verify_day_scope(day, route, did)
            self.insert_locked_report(day, ink(self.run_id, 6), day + 'T09:00:00+08:00')

    def permissions_validation_and_conflicts(self):
        day, route, did = self.stops[0]
        self.verify_day_scope(day, route, did)
        path = '/api/branch-session/correct-document'
        valid = self.payload(did, 3, 7)
        self.request('/api/branch-session/select-task', self.other, {'delivery_id': did}, expected=403)
        self.request(path, self.other, valid, expected=(403, 409))
        self.request(path, self.branch, {**valid, 'row_version': -1}, expected=409)
        for invalid in ({'reason': ' '}, {'signature': 'data:image/png;base64,AAAA'},
                        {'signature': ink(self.run_id, 4)}):
            self.request(path, self.branch, valid | invalid, expected=400)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.request(
                path, self.branch, valid, expected=(200, 409)).status_code, range(2)))
        require(sorted(statuses) == [200, 409])
        self.request(path, self.branch, valid, expected=409)
        self.request(f'/api/driver/deliveries/{did}/confirm', self.other,
                     {'document_correction_id': 0}, expected=401)
        self.request(f'/api/routes/{route}/secretary-sign', self.admin,
                     {'signature': ink(self.run_id, 8)}, expected=403)

    def correction_4_to_3_and_reports(self):
        day, route, did = self.stops[0]
        self.confirm(did, 3)
        self.relock(day, route, did, 8)
        self.download(day, 3)
        dash = self.request('/api/dashboard/monthly-route-cumulative?month=' + day[:7], self.admin).json()
        day_routes = next(item['routes'] for item in dash['daily'] if item['date'] == day)
        require(next(r['document'] for r in day_routes if r['route_id'] == self.route_id) == 3)
        month = self.request('/api/reports/monthly-route-summary?month=' + day[:7], self.admin).json()
        row = next(item['routes'][self.tag] for item in month['days'] if item['date'] == day)
        require(row['document'] == 3 and row['document_corrections_pending'] == 0)
        detail = self.receipt(did)
        require(detail['delivery']['document_original'] == 5 and detail['delivery']['document_final'] == 3)
        changes = [(json.loads(r['before_json'])['document'], json.loads(r['after_json'])['document'])
                   for r in detail['audit'] if r['action'] == 'BRANCH_DOCUMENT_PROPOSED']
        confirmations = [json.loads(r['after_json'])['document'] for r in detail['audit']
                         if r['action'] == 'DRIVER_DOCUMENT_CONFIRMED']
        require(changes == [(5, 4), (4, 3)] and confirmations == [4, 3])

    def previous_month_locked_reapproval(self):
        day, route, did = self.stops[1]
        stamp = day + 'T08:00:00+08:00'
        self.verify_day_scope(day, route, did)
        self.download(day, 4)
        legacy = self.receipt(did)
        require(legacy['audit'] == [] and legacy['office_audit'] == []
                and legacy['delivery']['document_final'] == 4)
        history = self.request('/api/branch-session/history?month=' + day[:7], self.branch).json()
        require(any(r['id'] == did and r['document_final'] == 4 for r in history['records']))
        signature_path = f'/api/branch-session/history/{did}/signatures'
        require(self.request(signature_path, self.branch).json()['branch_signature'] == ink(self.run_id, 1))
        self.select(did)
        self.request('/api/branch-session/correct-document', self.branch, self.payload(did, 3, 10))
        require(self.status(day)['report']['status'] == 'AMENDING')
        self.download(day, allowed=False)
        self.confirm(did, 3)
        self.download(day, allowed=False)
        self.relock(day, route, did, 11)
        self.download(day, 3)
        detail = self.receipt(did)
        archive = {r['entity_type']: json.loads(r['before_json']) for r in detail['office_audit']
                   if r['action'] == 'OFFICE_DOCUMENT_REVIEW_INVALIDATED'}
        require(archive['DAILY_ROUTE']['secretary_signature'] == ink(self.run_id, 2)
                and archive['DAY']['secretary_signature'] == ink(self.run_id, 3)
                and archive['DAILY_ROUTE']['secretary_signed_at'] == archive['DAY']['locked_at'] == stamp
                and detail['delivery']['branch_signed_at'] == stamp
                and detail['delivery']['branch_signature'] == ink(self.run_id, 1))

    def legacy_null_and_history(self):
        _, _, did = self.stops[2]
        path = '/api/branch-session/sign'
        self.select(did)
        payload = self.payload(did, 0, 13)
        del payload['document']
        for invalid in (payload, payload | {'document': None}, payload | {'document': 0.5}):
            self.request(path, self.branch, invalid, expected=400)
        require(self.receipt(did)['delivery']['branch_signed_at'] is None)
        self.request(path, self.branch, payload | {'document': 0})
        self.confirm(did, 0)
        require(self.receipt(did)['delivery']['document_original'] is None)


def main():
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings('ignore')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-remote-uat', action='store_true')
    parser.add_argument('--expected-commit')
    parser.add_argument('--credentials-file', type=Path)
    args = parser.parse_args()
    run_id = uuid4().hex
    results = dict.fromkeys(CASES, 'NOT RUN')
    current = 'preflight'
    if args.run_remote_uat and not any(os.getenv(k) for k in ('CI', 'GITHUB_ACTIONS', 'PYTEST_CURRENT_TEST')):
        try:
            sys.path.insert(0, str(ROOT))
            from app.uat_init import guarded_conninfo
            from app.uat_serve import verify
            import psycopg
            from psycopg.rows import dict_row
            from pypdf import PdfReader  # Verify the optional remote-only dependency before writing.

            check_revision(args.expected_commit or '')
            passwords = credentials(args.credentials_file)
            conninfo = guarded_conninfo(os.environ['MOVINGMATCH_V20_UAT_DATABASE_URL'])
            with psycopg.connect(conninfo, row_factory=dict_row, autocommit=True) as db:
                suite = Acceptance(db, args.expected_commit, run_id)
                suite.health()
                with db.transaction():
                    db.execute('SET TRANSACTION READ ONLY')
                    verify(db)
                require(db.execute('SELECT pg_try_advisory_lock(454620) locked').fetchone()['locked'])
                before = suite.snapshot()
                results[current] = 'PASS'
                try:
                    for current in CASES[1:-1]:
                        suite.health()
                        getattr(suite, current if current != 'synthetic_fixtures' else 'fixtures')()
                        if current == 'synthetic_fixtures':
                            suite.login(passwords)
                        results[current] = 'PASS'
                except Exception:
                    results[current] = 'FAIL'
                current = 'existing_records_preserved'
                require(suite.snapshot({k: v[0] for k, v in before.items()}) == before)
                results[current] = 'PASS'
        except Exception:
            # Never emit request URLs, response bodies, parameters, driver errors or tracebacks.
            results[current] = 'FAIL'
    summary = 'PASS' if all(v == 'PASS' for v in results.values()) else (
        'FAIL' if 'FAIL' in results.values() else 'NOT RUN')
    print(json.dumps({'run_id': run_id[:8] + '…', 'summary': summary, 'cases': results}))
    return 0 if summary == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
