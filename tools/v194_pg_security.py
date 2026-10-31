"""Test-only connection guards. Import never creates a connection.

Every physical connection, including pool replacements, validates the exact
approved test host/database, TLS verification, system CA and PostgreSQL 18.6.
"""
import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

EXPECTED_HOST = 'dpg-dbhh8oflk1mc73corrp0-a.singapore-postgres.render.com'
EXPECTED_DB = 'movingmatch_v194_test'
CA_FILE = '/etc/ssl/cert.pem'
VALIDATED = {}
VALIDATION_ERRORS = []
REGISTRY_LOCK = threading.Lock()


class CheckFailure(RuntimeError):
    pass


class Blocked(CheckFailure):
    pass


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def require(condition, label):
    if not condition:
        raise CheckFailure(label)


def healthy():
    with REGISTRY_LOCK:
        if VALIDATION_ERRORS:
            raise Blocked('Physical connection verification failed; test stopped')


def target():
    # Never read generic DATABASE_URL or a production DSN as a fallback.
    raw = os.environ.get('MOVINGMATCH_V194_TEST_DATABASE_URL', '')
    require(bool(raw.strip()), 'Dedicated test DSN is required')
    params = conninfo_to_dict(raw)
    require(bool(params.get('user')) and bool(params.get('password')), 'Dedicated DSN must include its own credentials; no environment fallback')
    modes = parse_qs(urlsplit(raw).query, keep_blank_values=True).get('sslmode', [])
    require(params.get('dbname') == EXPECTED_DB, 'Database target mismatch')
    require(params.get('host') == EXPECTED_HOST, 'Host target mismatch')
    require(params.get('port', '5432') == '5432', 'Port target mismatch')
    require(not params.get('hostaddr') and not params.get('service'), 'URL routing override')
    require(not os.environ.get('PGHOSTADDR') and not os.environ.get('PGSERVICE'), 'Environment routing override')
    require(params.get('sslmode', 'verify-full') == 'verify-full' and all(m == 'verify-full' for m in modes), 'Conflicting sslmode')
    require(bool(ssl.create_default_context(cafile=CA_FILE).get_ca_certs()), 'Trusted system CA unavailable')
    return make_conninfo(raw, host=EXPECTED_HOST, port='5432', dbname=EXPECTED_DB,
                         sslmode='verify-full', sslrootcert=CA_FILE, gssencmode='disable',
                         options='-c search_path=public -c statement_timeout=15000')


def verify_new_connection(connection):
    params = connection.info.get_parameters()
    require(params.get('host') == EXPECTED_HOST, 'Effective host mismatch')
    require(params.get('dbname') == EXPECTED_DB, 'Effective database mismatch')
    require(params.get('sslmode') == 'verify-full', 'Effective sslmode mismatch')
    require(params.get('sslrootcert') == CA_FILE, 'Effective CA mismatch')
    row = connection.execute('SELECT current_database() AS db,current_schema() AS schema,pg_backend_pid() AS pid,version() AS version').fetchone()
    db, schema, pid, version = (row['db'], row['schema'], row['pid'], row['version']) if isinstance(row, dict) else row
    require(db == EXPECTED_DB and schema == 'public', 'Connected database/schema mismatch')
    require(connection.info.server_version == 180006, 'PostgreSQL major version mismatch')
    row = connection.execute('SELECT ssl,version,cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
    tls = list(row.values()) if isinstance(row, dict) else list(row)
    require(tls[0] and tls[1] in ('TLSv1.2', 'TLSv1.3') and bool(tls[2]), 'TLS evidence mismatch')
    # Only identity SELECTs ran. Return every newly opened connection IDLE.
    connection.rollback()
    require(connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE, 'Validation transaction not closed')
    return {'backend_pid': pid, 'version': version, 'sslmode': 'verify-full',
            'ca_hostname_verified': True, 'tls': tls[1], 'cipher': tls[2]}


class VerifiedConnection(psycopg.Connection):
    def cursor(self, *args, **kwargs):
        # Stop subsequent application SQL if any pool physical connection failed.
        healthy()
        return super().cursor(*args, **kwargs)

    @classmethod
    def connect(cls, conninfo='', **kwargs):
        healthy()
        connection = None
        try:
            dsn = target()
            require(conninfo == dsn, 'Unapproved DSN at physical connection creation')
            allowed = {'autocommit', 'row_factory', 'connect_timeout', 'prepare_threshold',
                       'context', 'cursor_factory', 'sslmode', 'sslrootcert'}
            require(set(kwargs) <= allowed, 'Unapproved connection keyword override')
            require(kwargs.get('sslmode', 'verify-full') == 'verify-full', 'Keyword sslmode downgrade')
            require(kwargs.get('sslrootcert', CA_FILE) == CA_FILE, 'Keyword CA override')
            kwargs.update(sslmode='verify-full', sslrootcert=CA_FILE)
            connection = super().connect(dsn, **kwargs)
            evidence = verify_new_connection(connection)
            with REGISTRY_LOCK:
                VALIDATED[id(connection)] = evidence
            return connection
        except BaseException:
            if connection is not None:
                connection.close()
            with REGISTRY_LOCK:
                VALIDATION_ERRORS.append('Physical connection verification failed')
            raise Blocked('Physical connection creation/verification failed') from None


def verified_connect(**kwargs):
    return VerifiedConnection.connect(target(), connect_timeout=15, **kwargs)


def evidence_for(connection):
    healthy()
    with REGISTRY_LOCK:
        evidence = VALIDATED.get(id(connection))
    if not isinstance(connection, VerifiedConnection) or evidence is None:
        raise Blocked('Connection lacks physical-creation verification')
    require(connection.info.backend_pid == evidence['backend_pid'], 'Backend identity changed')
    return evidence


def guarded_application(dsn):
    require(dsn == target(), 'Application DSN mismatch')
    os.environ['DATABASE_URL'] = dsn
    os.environ['APP_ENV'] = 'test'
    from app import main
    require(main.USE_POSTGRES and main.DATABASE_URL == dsn, 'Application fallback is prohibited')
    if main._PG_POOL is not None:
        raise Blocked('Pre-existing pool cannot be trusted or intercepted')
    original_pool = main.ConnectionPool
    if original_pool is None or 'connection_class' not in inspect.signature(original_pool).parameters:
        raise Blocked('Pool cannot install per-physical-connection verification')

    def verified_pool(*args, **kwargs):
        healthy()
        require(not args and kwargs.get('conninfo') == target(), 'Pool target mismatch')
        require('connection_class' not in kwargs, 'Pool class override prohibited')
        kwargs['connection_class'] = VerifiedConnection
        requested_open = kwargs.pop('open', True)
        require(requested_open is True, 'Unexpected pool lifecycle mode')
        kwargs['open'] = False  # No background creation before interception is checked.
        pool = original_pool(**kwargs)
        if pool.connection_class is not VerifiedConnection:
            pool.close()
            raise Blocked('Pool ignored the physical verification class')
        pool.open()
        return pool
    main.ConnectionPool = verified_pool
    original_db = main.db
    def checked_db():
        healthy()
        require(main.USE_POSTGRES and main.DATABASE_URL == target(), 'Application target changed')
        adapter = original_db()
        try:
            evidence_for(adapter.con)
            return adapter
        except BaseException:
            adapter.close()
            raise
    main.db = checked_db
    def forbidden(*args, **kwargs):
        raise Blocked('Forbidden seed/reset/business/mail path')
    for name in ('reset_test_day_once', 'apply_demo_branch_limit', 'seed_demo_data',
                 '_seed_demo_data', 'migrate_official_routes_and_branches',
                 'migrate_default_route_drivers', 'stabilize_accounts_and_today_drivers',
                 'gmail_send_email'):
        setattr(main, name, forbidden)
    return main
