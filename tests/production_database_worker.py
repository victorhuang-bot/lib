"""Fresh-process probes for production failure paths; synthetic settings, no network/DB I/O."""
import builtins
import json
import io
import logging
import os
from pathlib import Path
import socket
import sqlite3
import sys
import traceback
from unittest.mock import MagicMock, patch


def main():
    cfg=json.loads(sys.argv[1]);report={'case':cfg['name'],'sqlite_calls':0,'network_calls':0,
        'pg_attempts':0,'seed_calls':0,'init_calls':0,'rejected':False,'import_rejected':False,
        'public_error_is_fixed':False,'error_contains_sensitive_detail':False,'traceback_contains_sensitive_detail':False,'exception_context_suppressed':False,
        'pool_log_redacted':False,'pool_log_records':0}
    target=Path(os.environ['MOVINGMATCH_PROBE_CHECKOUT']).resolve()
    real_import=builtins.__import__
    missing=set(cfg.get('missing_drivers',[]))
    def import_hook(name,*args,**kwargs):
        if name.split('.')[0] in missing:raise ImportError('SYNTHETIC_DRIVER_UNAVAILABLE')
        return real_import(name,*args,**kwargs)
    phase='import'
    with patch.object(sqlite3,'connect',side_effect=AssertionError('SQLITE_FORBIDDEN')) as sqlite, \
         patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN')) as network, \
         patch.object(builtins,'__import__',new=import_hook):
        try:
            from app import main as application
            assert Path(application.__file__).resolve()==target/'app/main.py'
            report['correct_source']=True
            phase='runtime'
            if cfg.get('runtime_missing'):setattr(application,cfg['runtime_missing'],None)
            fake=MagicMock()
            private_url='postgresql://synthetic-user:SYNTHETIC_PRIVATE_PASSWORD@private-host.invalid/private-db'
            sensitive=(private_url,'SYNTHETIC_PRIVATE_PASSWORD','private-host.invalid',
                       'SYNTHETIC_DRIVER_DETAIL','OSError')
            failure=OSError(private_url+' SYNTHETIC_DRIVER_DETAIL')
            effect=None if cfg['kind']=='configured' else failure
            with patch.object(application,'_PgCompat',return_value=fake,side_effect=effect) as pg, \
                 patch.object(application,'seed_demo_data',side_effect=AssertionError('SEED_FORBIDDEN')) as seed, \
                 patch.object(application,'init_db',side_effect=AssertionError('INIT_FORBIDDEN')) as init, \
                 patch('time.sleep'):
                try:
                    if cfg['kind']=='logging':
                        logger=logging.getLogger('psycopg.pool');stream=io.StringIO()
                        handler=logging.StreamHandler(stream);handler.setFormatter(logging.Formatter('%(message)s'))
                        old_level=logger.level;logger.setLevel(logging.DEBUG);logger.addHandler(handler)
                        try:
                            with patch.object(logger,'propagate',False):
                                logger.warning('error connecting in %r: %s',private_url,failure)
                                try:raise failure
                                except OSError:logger.error('driver detail %s',private_url,exc_info=True,stack_info=True)
                                logger.debug('connection settings %s',private_url)
                            output=stream.getvalue()
                            report['pool_log_redacted']=output=='POSTGRES_POOL_DIAGNOSTIC\n'*3 and not any(value in output for value in sensitive)
                            report['pool_log_records']=len(output.splitlines())
                        finally:
                            logger.removeHandler(handler);logger.setLevel(old_level);handler.close()
                    else:
                        value=application.validate_startup_schema() if cfg.get('entry')=='startup' else application.db()
                        report['selected_pg_object']=value is fake
                except RuntimeError as error:
                    report['rejected']=True
                    report['public_error_is_fixed']=str(error)=='POSTGRES_CONNECTION_UNAVAILABLE; SQLite fallback is forbidden'
                    report['error_contains_sensitive_detail']=any(value in str(error) for value in sensitive)
                    report['traceback_contains_sensitive_detail']=any(value in traceback.format_exc() for value in sensitive)
                    report['exception_context_suppressed']=error.__suppress_context__ and error.__cause__ is None
                finally:
                    report.update(pg_attempts=pg.call_count,seed_calls=seed.call_count,init_calls=init.call_count)
        except RuntimeError:
            report.update(rejected=True,import_rejected=phase=='import')
        finally:
            report.update(sqlite_calls=sqlite.call_count,network_calls=network.call_count,
                db_file_created=(Path(os.environ['DATA_DIR'])/'app.db').exists())
    print(json.dumps(report))


if __name__=='__main__':main()
