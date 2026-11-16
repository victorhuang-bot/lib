"""Explicit additive V20 migration; never imports application or inherited DB URL.

Only dedicated SQLite paths or loopback PostgreSQL test databases are accepted.
"""
import argparse
import os
import sqlite3
from pathlib import Path
from app.empty_boxes import migrate
from app.schema import SchemaTransaction


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    target=parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--sqlite',type=Path)
    target.add_argument('--local-postgres',help='Loopback test DSN; database must start movingmatch_v20_test')
    args=parser.parse_args()
    # libpq environment must not redirect a declared loopback test target.
    for key in list(os.environ):
        if key.startswith('PG'): os.environ.pop(key,None)
    if args.sqlite:
        path=args.sqlite.resolve()
        if not path.is_file() or path==Path(__file__).resolve().parent.parent/'data'/'app.db':
            parser.error('Use an existing isolated database, never the tracked application DB')
        c=sqlite3.connect(path)
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        postgres=False
    else:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        params=conninfo_to_dict(args.local_postgres)
        if params.get('host') not in ('127.0.0.1','localhost','::1') or not params.get('dbname','').startswith('movingmatch_v20_test') or any(params.get(k) for k in ('hostaddr','service')):
            parser.error('Only loopback movingmatch_v20_test databases are supported')
        c=psycopg.connect(args.local_postgres)
        c.execute('SET LOCAL lock_timeout = \'8s\'')
        c.execute('SELECT pg_advisory_xact_lock(194020)')
        postgres=True
    try:
        migrate(SchemaTransaction(c,postgres))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:c.close()
    print('PASS: V20 additive schema migration complete')


if __name__=='__main__':main()
