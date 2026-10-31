"""Schema-only bootstrap, with one transaction owned by the runner.

No application import, seeds, business data updates or network calls at import.
"""
import re

BASE_SCHEMA = r"""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, role TEXT, email TEXT, is_active INTEGER DEFAULT 1, failed_count INTEGER DEFAULT 0, locked_until TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS password_resets(id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, expires_at TEXT, used_at TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS routes(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1, pin_hash TEXT);
    CREATE TABLE IF NOT EXISTS driver_devices(id INTEGER PRIMARY KEY, driver_id INTEGER, device_key TEXT UNIQUE, activated_at TEXT, revoked_at TEXT, last_seen_at TEXT);
    CREATE TABLE IF NOT EXISTS driver_activation_tokens(id INTEGER PRIMARY KEY, driver_id INTEGER, token_hash TEXT UNIQUE, created_at TEXT, expires_at TEXT, used_at TEXT, revoked_at TEXT, created_by INTEGER);
    CREATE TABLE IF NOT EXISTS branches(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, route_id INTEGER, stop_order INTEGER, active INTEGER DEFAULT 1, pin_hash TEXT, access_token_hash TEXT UNIQUE, qr_created_at TEXT, address TEXT DEFAULT '', phone TEXT DEFAULT '', contact_name TEXT DEFAULT '', contact_info TEXT DEFAULT '', delivery_weekdays TEXT DEFAULT '1,2,3,4,5', delivery_frequency TEXT DEFAULT '每週固定');
    CREATE TABLE IF NOT EXISTS daily_routes(id INTEGER PRIMARY KEY, service_date TEXT, route_id INTEGER, driver_id INTEGER, status TEXT DEFAULT 'ACTIVE', driver_signature TEXT, driver_signed_at TEXT, UNIQUE(service_date,route_id));
    CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY, service_date TEXT, daily_route_id INTEGER, branch_id INTEGER, status TEXT DEFAULT 'WAITING_SECRETARY', document_original INTEGER, document_final INTEGER, document_return_final INTEGER, outbound_original INTEGER, outbound_final INTEGER, inbound_final INTEGER, note_final TEXT, signer_name TEXT, branch_signed_at TEXT, branch_signature TEXT, receipt_at TEXT, voided_at TEXT, voided_by INTEGER, void_reason TEXT, admin_closed_at TEXT, admin_closed_by INTEGER, admin_close_reason TEXT, correction_signature TEXT, correction_signer_name TEXT, correction_reason TEXT, corrected_at TEXT, driver_confirmed_at TEXT, row_version INTEGER DEFAULT 1, UNIQUE(service_date,branch_id));
    CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, delivery_id INTEGER, requested_by_driver_id INTEGER, requested_at TEXT, fields_json TEXT, driver_note TEXT, status TEXT, resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, actor_type TEXT, actor_id TEXT, role TEXT, action TEXT, entity_type TEXT, entity_id TEXT, before_json TEXT, after_json TEXT, reason TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS branch_sessions(token_hash TEXT PRIMARY KEY, branch_id INTEGER, delivery_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS driver_sessions(token_hash TEXT PRIMARY KEY, driver_id INTEGER, device_id INTEGER, expires_at TEXT);
    CREATE TABLE IF NOT EXISTS daily_reports(id INTEGER PRIMARY KEY, service_date TEXT UNIQUE, secretary_signature TEXT, secretary_signed_at TEXT, status TEXT DEFAULT 'OPEN', locked_at TEXT);
    CREATE TABLE IF NOT EXISTS email_recipients(id INTEGER PRIMARY KEY, email TEXT, recipient_type TEXT DEFAULT 'TO', active INTEGER DEFAULT 1, created_at TEXT);
    CREATE TABLE IF NOT EXISTS email_logs(id INTEGER PRIMARY KEY, report_type TEXT, period TEXT, recipients TEXT, status TEXT, error_message TEXT, sent_at TEXT, created_at TEXT, email_type TEXT, subject TEXT, to_addresses TEXT, cc_addresses TEXT, bcc_addresses TEXT, sender TEXT, provider TEXT, message_id TEXT, triggered_by TEXT, delivery_identifier TEXT);
    CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS delivery_exceptions(id INTEGER PRIMARY KEY, service_date TEXT, branch_id INTEGER, exception_type TEXT, reason TEXT, created_by INTEGER, created_at TEXT, UNIQUE(service_date,branch_id));
    CREATE TABLE IF NOT EXISTS global_closures(id INTEGER PRIMARY KEY, service_date TEXT UNIQUE, reason TEXT, created_by INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS route_handoffs(id INTEGER PRIMARY KEY, service_date TEXT, daily_route_id INTEGER, from_driver_id INTEGER, to_driver_id INTEGER, start_delivery_id INTEGER, reason TEXT, note TEXT, created_by INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS delivery_driver_assignments(id INTEGER PRIMARY KEY, delivery_id INTEGER UNIQUE, driver_id INTEGER, assigned_at TEXT, handoff_id INTEGER);
    CREATE TABLE IF NOT EXISTS route_segment_signatures(id INTEGER PRIMARY KEY, service_date TEXT, daily_route_id INTEGER, driver_id INTEGER, signature TEXT, signed_at TEXT, UNIQUE(service_date,daily_route_id,driver_id));
"""


class SchemaTransaction:
    """Suppress legacy helper commits and sqlite executescript implicit commits."""
    def __init__(self, connection, postgres):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, params=()):
        return self.connection.execute(sql, params)

    def executescript(self, script):
        if self.postgres:
            script = re.sub(r'\bid INTEGER PRIMARY KEY\b',
                            'id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY', script)
        for statement in script.split(';'):
            if statement.strip():
                self.execute(statement)

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def commit(self):
        # Only initialize_schema may commit the migration batch.
        pass


def initialize_schema(connection, postgres, migrations):
    tx = SchemaTransaction(connection, postgres)
    try:
        if postgres:
            tx.execute("SET LOCAL lock_timeout TO '8s'")
            tx.execute("SET LOCAL statement_timeout TO '60s'")
            tx.execute('SELECT pg_advisory_xact_lock(194000)')
        else:
            tx.execute('BEGIN IMMEDIATE')
        tx.executescript(BASE_SCHEMA)
        for migration in migrations:
            migration(tx)
        tx.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY)')
        validate_schema(tx)
        tx.execute("INSERT INTO schema_migrations(version) VALUES('v194-safe-schema-1') ON CONFLICT(version) DO NOTHING")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def validate_schema(connection):
    # Verify all expected tables/columns, including old-schema migration additions.
    for name, body in re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)\((.*?)\);', BASE_SCHEMA, re.S):
        columns = re.findall(r'(?:^|,)\s*(\w+)\s+(?:INTEGER|TEXT)', body)
        connection.execute('SELECT '+','.join(columns)+' FROM '+name+' WHERE 1=0')
    connection.execute('SELECT id,branch_id,name,active,created_at,updated_at FROM branch_signers WHERE 1=0')
    connection.execute('SELECT secretary_signature,secretary_signed_at FROM daily_routes WHERE 1=0')
    connection.execute('SELECT id,delivery_id,item_type,item_name,quantity,created_by,updated_at FROM delivery_items WHERE 1=0')
