"""Explicit, additive initialization of the pinned synthetic V20 Neon UAT only.

Run: python -m app.uat_init --credentials-file /private/path/uat-credentials.json
Only MOVINGMATCH_V20_UAT_DATABASE_URL selects the connection. No app startup.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import unittest

PROJECT_ID = 'blue-paper-82274906'
BRANCH_ID = 'br-withered-cloud-b3jqn8nc'
HOST = 'ep-royal-night-b3ldf8au.c-4.ap-southeast-1.aws.neon.tech'
DATABASE = 'movingmatch_v20_uat'
ROLE = 'movingmatch_v20_uat_owner'
MARKER = 'V20_SYNTHETIC_UAT_V1'


def guarded_conninfo(value):
    import certifi
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    params = conninfo_to_dict(value)
    allowed = {'host', 'port', 'dbname', 'user', 'password', 'sslmode', 'channel_binding'}
    if (set(params) - allowed or params.get('host') != HOST
            or params.get('dbname') != DATABASE or params.get('user') != ROLE
            or params.get('port', '5432') != '5432'
            or params.get('sslmode', 'verify-full') not in ('require', 'verify-full')
            or any(k.startswith('PG') for k in os.environ)):
        raise ValueError('UAT target rejected')
    return make_conninfo(**(params | {'sslmode': 'verify-full', 'sslrootcert': certifi.where(),
                                     'connect_timeout': '10'}))


class Connection:
    """Minimal qmark adapter; never opens another connection or pool."""
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        return self.raw.execute(sql.replace('?', '%s'), params)

    def commit(self):
        # Outer transaction owns schema, validation, seed and marker together.
        pass

    def rollback(self):
        self.raw.rollback()


def initialize(raw, credentials):
    from app import main
    from app import empty_boxes
    from app.schema import initialize_schema
    from app.v20_schema_checks import assert_postgres_schema
    c = Connection(raw)
    identity = c.execute('SELECT current_database() db, current_user usr').fetchone()
    if identity != {'db': DATABASE, 'usr': ROLE} or not raw.pgconn.ssl_in_use:
        raise ValueError('UAT server identity rejected')
    c.execute('SELECT pg_advisory_xact_lock(202000)')
    tables = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    if tables:
        if not any(r['tablename'] == 'app_settings' for r in tables):
            raise ValueError('Unrecognized database')
        marker = c.execute('SELECT value FROM app_settings WHERE key=?', (MARKER,)).fetchone()
        if not marker or marker['value'] != PROJECT_ID + '/' + BRANCH_ID:
            raise ValueError('Existing unmarked database rejected')
    initialize_schema(c, True, (
        main.migrate_branch_signers, main.migrate_corrections_for_repeat_requests,
        main.migrate_email_logs_gmail_api, main.migrate_route_secretary_signatures,
        main.migrate_document_return_final, main.migrate_v1929,
        main.migrate_carried_item_units, empty_boxes.migrate,
    ))
    report = assert_postgres_schema(unittest.TestCase(), c)
    # ON CONFLICT never rotates a password or overwrites a tester's changes.
    for number in (1, 2):
        c.execute('INSERT INTO routes(code,name,active) VALUES(?,?,1) ON CONFLICT(code) DO NOTHING',
                  (f'UAT-R{number}', f'虛構測試路線{number}'))
    for number in range(1, 5):
        code = f'UAT-B{number}'
        if not c.execute('SELECT id FROM branches WHERE code=?', (code,)).fetchone():
            password = secrets.token_urlsafe(24)
            credentials[code] = password
            route = c.execute('SELECT id FROM routes WHERE code=?', (f'UAT-R{1 if number < 3 else 2}',)).fetchone()['id']
            c.execute('INSERT INTO branches(code,name,route_id,stop_order,active,pin_hash) VALUES(?,?,?,?,1,?)',
                      (code, f'虛構星球測試分館{number}', route, number, main.hash_secret(password)))
    for username, role in (('uat-head-office', 'ADMIN'), ('uat-secretary', 'SECRETARY'), ('uat-secretariat', 'SECRETARIAT')):
        if not c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
            password = secrets.token_urlsafe(24)
            credentials[username] = password
            c.execute('INSERT INTO users(username,password_hash,role,is_active) VALUES(?,?,?,1)',
                      (username, main.hash_secret(password), role))
    if not c.execute("SELECT id FROM drivers WHERE name='虛構測試司機 UAT-1'").fetchone():
        password = secrets.token_urlsafe(24)
        credentials['uat-driver-1'] = password
        c.execute('INSERT INTO drivers(name,active,pin_hash) VALUES(?,1,?)',
                  ('虛構測試司機 UAT-1', main.hash_secret(password)))
    c.execute('INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING',
              (MARKER, PROJECT_ID + '/' + BRANCH_ID))
    report.update(project_id=PROJECT_ID, branch_id=BRANCH_ID, synthetic_only=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file', required=True, type=Path)
    args = parser.parse_args()
    # Reject repository storage and symlinks; exclusive 0600 output before DB writes.
    path = args.credentials_file.absolute()
    root = Path(__file__).resolve().parent.parent
    fd = None
    try:
        if path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError('Credential output must be outside repository')
        conninfo = guarded_conninfo(os.environ['MOVINGMATCH_V20_UAT_DATABASE_URL'])
        # Migration helpers select PostgreSQL dialect from this at import time.
        # The generic inherited URL is replaced only after the UAT guard passes.
        os.environ['DATABASE_URL'] = conninfo
        os.environ['APP_ENV'] = 'test'
        import psycopg
        from psycopg.rows import dict_row
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            fd = None
            with psycopg.connect(conninfo, row_factory=dict_row) as raw:
                credentials = {}
                report = initialize(raw, credentials)
                json.dump(credentials, output)
                output.flush()
                os.fsync(output.fileno())
            # Commit happens only after secure credential output succeeds.
        print(json.dumps(report, ensure_ascii=False))
    except Exception:
        if fd is not None:
            os.close(fd)
        raise SystemExit('UAT initialization failed; details withheld; V20 must remain disabled') from None


if __name__ == '__main__':
    main()
