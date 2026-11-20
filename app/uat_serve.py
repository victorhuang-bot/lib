"""Fail-closed entrypoint exclusively for the independent V20 UAT service."""
import os
import re
import unittest


def verify(raw):
    from app.uat_init import Connection, DATABASE, ROLE, MARKER, PROJECT_ID, BRANCH_ID
    from app.v20_schema_checks import assert_postgres_schema
    c = Connection(raw)
    if (c.execute('SELECT current_database() db, current_user usr').fetchone()
            != {'db': DATABASE, 'usr': ROLE} or not raw.pgconn.ssl_in_use):
        raise ValueError('Server identity rejected')
    marker = c.execute('SELECT value FROM app_settings WHERE key=?', (MARKER,)).fetchone()
    if not marker or marker['value'] != PROJECT_ID + '/' + BRANCH_ID:
        raise ValueError('UAT initialization required')
    return assert_postgres_schema(unittest.TestCase(), c)


RENDER_UAT_SERVICE = 'srv-dbtoc5t6e07s73anb6g0'
UAT_COMMIT_ENV = 'MOVINGMATCH_V20_VERIFIED_UAT_COMMIT'


def main():
    try:
        from app.uat_init import guarded_conninfo
        from psycopg.rows import dict_row
        import psycopg
        conninfo = guarded_conninfo(os.environ['MOVINGMATCH_V20_UAT_DATABASE_URL'])
        with psycopg.connect(conninfo, row_factory=dict_row) as raw:
            raw.execute('SET TRANSACTION READ ONLY')
            verify(raw)
        os.environ.pop(UAT_COMMIT_ENV, None)
        commit = os.getenv('RENDER_GIT_COMMIT', '')
        if os.getenv('RENDER_SERVICE_ID') == RENDER_UAT_SERVICE and re.fullmatch(r'[0-9a-f]{40}', commit):
            os.environ[UAT_COMMIT_ENV] = commit
        os.environ['DATABASE_URL'] = conninfo
        os.environ['APP_ENV'] = 'production'
        os.environ['MOVINGMATCH_V20_UAT'] = '1'
        os.environ['MOVINGMATCH_V20_ENABLED'] = '1'
    except Exception:
        raise SystemExit('UAT verification failed; server not started; details withheld') from None
    os.execvp('uvicorn', ['uvicorn', 'app.main:app', '--host', '0.0.0.0',
                         '--port', os.environ.get('PORT', '8000')])


if __name__ == '__main__':
    main()
