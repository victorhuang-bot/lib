"""Explicit schema-only command. No seeds, mail, application startup or resets."""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--sqlite', type=Path, help='Dedicated local database path')
    target.add_argument('--test-postgres', action='store_true', help='Approved V19.4 test database only')
    args = parser.parse_args()
    # Clear generic connection selection before importing the application module.
    os.environ.pop('DATABASE_URL', None)
    if args.sqlite:
        path = args.sqlite.resolve()
        if path.name != 'app.db' or path == Path(__file__).resolve().parent.parent / 'data' / 'app.db':
            parser.error('Use a dedicated directory with filename app.db')
        os.environ['DATA_DIR'] = str(path.parent)
    else:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        try:
            params = conninfo_to_dict(os.environ['MOVINGMATCH_V194_TEST_DATABASE_URL'])
            if (params.get('dbname') != 'movingmatch_v194_test'
                    or params.get('host') != 'dpg-dbhh8oflk1mc73corrp0-a.singapore-postgres.render.com'
                    or params.get('sslmode', 'verify-full') != 'verify-full'
                    or params.get('hostaddr') or params.get('service')
                    or os.environ.get('PGSERVICE') or os.environ.get('PGHOSTADDR')):
                raise ValueError('Target guard')
            os.environ['DATABASE_URL'] = make_conninfo(
                    os.environ['MOVINGMATCH_V194_TEST_DATABASE_URL'],
                    sslmode='verify-full', sslrootcert='/etc/ssl/cert.pem')
        except Exception:
            parser.error('Test target/TLS validation failed; connection details withheld')
    from app import main as application
    try:
        if args.test_postgres:
            c = application.db()
            try:
                if c.con.info.get_parameters().get('sslmode') != 'verify-full':
                    raise RuntimeError('TLS guard')
                if c.execute('SELECT current_database() AS name').fetchone()['name'] != 'movingmatch_v194_test':
                    raise RuntimeError('Database guard')
            finally:
                c.close()
        application.init_db()
    except Exception:
        raise SystemExit('Schema initialization failed; connection details withheld') from None
    print('PASS: schema initialized; no business seed or reset executed')


if __name__ == '__main__':
    main()
