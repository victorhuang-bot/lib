"""Atomic, previewable postponement of unstarted document prefills.

No network, email or migration operations. The application supplies its existing
DB adapter, calendar, authentication and audit helpers.
"""
from datetime import date, timedelta
import hashlib
import json
import re
from fastapi import HTTPException

PREFIX = 'SCHEDULE_POSTPONED:'


def _fingerprint(row):
    # Hashes bind the preview to the entire row without exposing its contents.
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest() if row else None


def _for_update(app):
    return ' FOR UPDATE' if app.USE_POSTGRES else ''


def _unstarted(app, c, row):
    if row['status'] not in ('WAITING_SECRETARY', 'WAITING_DRIVER'):
        raise HTTPException(409, f"配送 #{row['id']} 已開始或已取消，無法順延")
    fields = ('outbound_original', 'outbound_final', 'inbound_final',
              'document_return_final', 'branch_signed_at', 'branch_signature',
              'driver_confirmed_at', 'receipt_at', 'correction_signature',
              'corrected_at', 'admin_closed_at', 'voided_at', 'voided_by')
    if any(row[k] is not None for k in fields) or row['note_final'] or row['signer_name'] or row['correction_signer_name']:
        raise HTTPException(409, f"配送 #{row['id']} 已有司機操作或簽收資料，無法順延")
    if c.execute('SELECT 1 FROM delivery_items WHERE delivery_id=?', (row['id'],)).fetchone():
        raise HTTPException(409, f"配送 #{row['id']} 已有品項，請先人工確認")
    if c.execute('SELECT 1 FROM delivery_driver_assignments WHERE delivery_id=?', (row['id'],)).fetchone():
        raise HTTPException(409, f"配送 #{row['id']} 已指派支援司機，請先人工確認")
    _route_open(app, c, row['service_date'], row['daily_route_id'])


def _route_open(app, c, day, route_id):
    if app.is_report_locked(c, day):
        raise HTTPException(409, f'{day} 日報已鎖定')
    route = c.execute('SELECT * FROM daily_routes WHERE id=?'+_for_update(app), (route_id,)).fetchone()
    if route and route['status'] != 'ACTIVE':
        raise HTTPException(409, f'{day} 路線已開始或關閉，無法變更排程')
    if route and any(route[k] for k in ('driver_signature', 'driver_signed_at',
                                       'secretary_signature', 'secretary_signed_at')):
        raise HTTPException(409, f'{day} 路線已簽名，無法變更排程')
    if c.execute('SELECT 1 FROM route_segment_signatures WHERE daily_route_id=?', (route_id,)).fetchone():
        raise HTTPException(409, f'{day} 已有司機區段簽名，無法變更排程')


def _plan(app, c, dates, et, bid, manual_target_date=None):
    if et == 'ADD':
        return []
    sql = "SELECT * FROM deliveries WHERE service_date>=? AND service_date<=? AND status<>'VOID'"
    args = [dates[0], dates[-1]]
    if et == 'STOP':
        sql += ' AND branch_id=?'
        args.append(bid)
    sources = c.execute(sql+' ORDER BY service_date,branch_id,id'+_for_update(app), args).fetchall()
    targets = {}
    for source in sources:
        _unstarted(app, c, source)
        qty = source['document_final'] if source['document_final'] is not None else source['document_original']
        if qty is None:
            continue
        branch = c.execute('SELECT * FROM branches WHERE id=?'+_for_update(app), (source['branch_id'],)).fetchone()
        if not branch:
            raise HTTPException(409, '來源分館不存在，請先人工確認')
        route = c.execute('SELECT active FROM routes WHERE id=?', (branch['route_id'],)).fetchone()
        if not branch['active'] or not route or not route['active']:
            raise HTTPException(409, '分館或路線已停用，請先人工處理預填公文')
        if manual_target_date:
            day = manual_target_date
            if not app.manual_document_workday(c, day, branch['id']):
                raise HTTPException(409, f'{day} 不是有效公文補送工作日或分館已停送')
        else:
            target_day = date.fromisoformat(source['service_date'])
            for _ in range(366):
                target_day += timedelta(days=1)
                day = target_day.isoformat()
                # This entire request takes precedence over pre-existing ADD days.
                if day in dates or not app.branch_expected_on(c, branch, day):
                    continue
                break
            else:
                raise HTTPException(409, '一年內找不到可順延配送日')
        key = (branch['id'], day)
        if key not in targets:
            existing = c.execute('SELECT * FROM deliveries WHERE service_date=? AND branch_id=?'+_for_update(app), key[::-1]).fetchone()
            if existing:
                _unstarted(app, c, existing)
            else:
                if app.is_report_locked(c, day):
                    raise HTTPException(409, f'{day} 日報已鎖定')
                dr = c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=?', (day, branch['route_id'])).fetchone()
                if dr: _route_open(app, c, day, dr['id'])
            prior = (existing['document_final'] if existing['document_final'] is not None else existing['document_original']) if existing else None
            if prior is not None and (prior < 0 or prior > 2147483647):
                raise HTTPException(409, '目標公文數量超出可處理範圍，請人工確認')
            targets[key] = {'branch_id': branch['id'], 'branch_code': branch['code'],
                            'branch_name': branch['name'], 'target_date': day,
                            'target_id': existing['id'] if existing else None,
                            'target_version': existing['row_version'] if existing else None,
                            'target_fingerprint': _fingerprint(existing),
                            'branch_fingerprint': _fingerprint(branch),
                            'existing_qty': prior, 'sources': [], 'total': prior or 0}
        item = targets[key]
        item['sources'].append({'id': source['id'], 'date': source['service_date'],
                                'qty': qty, 'version': source['row_version'],
                                'fingerprint': _fingerprint(source)})
        item['total'] += qty
        if item['total'] > 2147483647 or qty < 0:
            raise HTTPException(409, '順延數量超出可處理範圍，請人工確認')
    plan = list(targets.values())
    for item in plan:
        item['conflict'] = item['existing_qty'] is not None or len(item['sources']) > 1
    return plan


def save_range(app, user, payload):
    et = str(payload.get('exception_type') or '').upper()
    original_type = et
    manual = payload.get('manual_target_date')
    if manual is not None:
        if original_type != 'TYPHOON':
            raise HTTPException(400, '手動公文補送日期僅適用颱風停送')
        if not isinstance(manual, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', manual):
            raise HTTPException(400, '手動補送日期格式須為 YYYY-MM-DD')
        try: date.fromisoformat(manual)
        except ValueError: raise HTTPException(400, '手動補送日期無效')
    reason = str(payload.get('reason') or '').strip()
    if et == 'TYPHOON':
        et = 'CLOSED_ALL'
        reason = reason or '颱風停送'
    if et not in ('CLOSED_ALL', 'STOP', 'ADD'):
        raise HTTPException(400, '類型錯誤')
    dates = app.date_span(str(payload.get('start_date') or '').strip(), str(payload.get('end_date') or '').strip())
    if dates[0] < app.today():
        raise HTTPException(409, '不可變更過去日期的配送排程')
    if manual and manual <= dates[-1]:
        raise HTTPException(409, '手動補送日期必須晚於整段停送期間')
    policy = payload.get('conflict_policy', 'reject')
    if policy not in ('reject', 'sum'):
        raise HTTPException(400, '數量衝突處理方式錯誤')
    try: bid = int(payload.get('branch_id') or 0)
    except (ValueError, TypeError): raise HTTPException(400, '分館格式錯誤')
    c = app.db()
    try:
        app.lock_schedule_writes(c)
        if manual:
            if not app.manual_document_workday(c, manual) or app.is_report_locked(c, manual):
                raise HTTPException(409, '手動補送日期為週末、休館／停送日或已鎖定日')
        if et != 'CLOSED_ALL':
            branch = c.execute('SELECT * FROM branches WHERE id=? AND active=1', (bid,)).fetchone()
            if not branch: raise HTTPException(404, '找不到有效分館')
        for day in dates:
            if app.is_report_locked(c, day): raise HTTPException(409, f'{day} 日報已鎖定')
            if et == 'CLOSED_ALL':
                routes = c.execute('SELECT id FROM daily_routes WHERE service_date=?', (day,)).fetchall()
            else:
                routes = c.execute('SELECT id FROM daily_routes WHERE service_date=? AND route_id=?', (day, branch['route_id'])).fetchall()
            for route in routes: _route_open(app, c, day, route['id'])
            if et == 'ADD':
                if c.execute('SELECT 1 FROM global_closures WHERE service_date=?', (day,)).fetchone():
                    raise HTTPException(409, f'{day} 為全館停送日，請先取消全館停送')
                row = c.execute('SELECT * FROM deliveries WHERE service_date=? AND branch_id=?', (day, bid)).fetchone()
                if row:
                    if row['status'] == 'VOID':
                        raise HTTPException(409, '請先取消原停送設定；已順延公文不會自動移回')
                    _unstarted(app, c, row)
        plan = _plan(app, c, dates, et, bid, manual)
        digest = hashlib.sha256(json.dumps({'dates': dates, 'type': original_type, 'branch': bid,
            'manual_target_date': manual, 'reason': reason, 'plan': plan}, sort_keys=True).encode()).hexdigest()
        response = {'ok': True, 'days': len(dates), 'dates': dates, 'plan': plan,
                    'manual_target_date': manual, 'plan_token': digest,
                    'has_conflicts': any(x['conflict'] for x in plan)}
        if payload.get('preview') is True:
            return dict(response, preview=True)
        if manual and not payload.get('plan_token'):
            raise HTTPException(409, '手動補送必須先預覽並確認後儲存')
        if payload.get('plan_token') and payload['plan_token'] != digest:
            raise HTTPException(409, '配送資料已變動，請重新預覽順延結果')
        if response['has_conflicts'] and policy != 'sum':
            raise HTTPException(409, '目標日已有公文或多日順延至同日，請先預覽並明確確認相加')
        for item in plan:
            if manual:
                # Persist the extra delivery in the existing schedule model. Both
                # prefill and every future day rebuild honor this ADD exception.
                previous = c.execute('SELECT * FROM delivery_exceptions WHERE service_date=? AND branch_id=?',
                                     (manual, item['branch_id'])).fetchone()
                if not previous:
                    c.execute("""INSERT INTO delivery_exceptions(service_date,branch_id,exception_type,reason,created_by,created_at)
                        VALUES(?,?,'ADD',?,?,?)""", (manual, item['branch_id'], '颱風公文手動補送：'+reason, user['id'], app.now()))
                    app.audit(c, 'USER', user['id'], user['role'], 'ADD_MANUAL_DOCUMENT_DAY', 'BRANCH', item['branch_id'],
                              after={'service_date': manual, 'exception_type': 'ADD'}, reason=reason)
            target = app.ensure_prefill_delivery(c, item['target_date'], item['branch_id'])
            _unstarted(app, c, target)
            before = dict(target)
            c.execute("""UPDATE deliveries SET document_original=COALESCE(document_original,?),
                document_final=?,status='WAITING_DRIVER',row_version=row_version+1 WHERE id=?""",
                (item['total'], item['total'], target['id']))
            app.audit(c, 'USER', user['id'], user['role'], 'POSTPONE_DOCUMENT_TARGET', 'DELIVERY', target['id'],
                      before={'document_original': before['document_original'], 'document_final': before['document_final']},
                      after=item, reason=reason)
            for source in item['sources']:
                changed = c.execute("""UPDATE deliveries SET status='VOID',document_final=NULL,voided_at=?,voided_by=?,
                    void_reason=?,row_version=row_version+1 WHERE id=? AND row_version=? AND status IN ('WAITING_SECRETARY','WAITING_DRIVER')""",
                    (app.now(), user['id'], PREFIX+str(target['id']), source['id'], source['version']))
                if changed.rowcount != 1:
                    raise HTTPException(409, '來源資料已變更，請重新預覽')
                app.audit(c, 'USER', user['id'], user['role'], 'POSTPONE_DOCUMENT_SOURCE', 'DELIVERY', source['id'],
                          before=source, after={'target_id': target['id'], 'target_date': item['target_date'], 'status': 'VOID'}, reason=reason)
        for day in dates:
            if et == 'CLOSED_ALL':
                c.execute("""INSERT INTO global_closures(service_date,reason,created_by,created_at) VALUES(?,?,?,?)
                    ON CONFLICT(service_date) DO UPDATE SET reason=excluded.reason,created_by=excluded.created_by,created_at=excluded.created_at""", (day, reason, user['id'], app.now()))
            else:
                c.execute("""INSERT INTO delivery_exceptions(service_date,branch_id,exception_type,reason,created_by,created_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(service_date,branch_id) DO UPDATE SET exception_type=excluded.exception_type,
                    reason=excluded.reason,created_by=excluded.created_by,created_at=excluded.created_at""", (day, bid, et, reason, user['id'], app.now()))
            app.audit(c, 'USER', user['id'], user['role'], 'SET_SCHEDULE_RANGE', 'DAY', day,
                      after={'exception_type': et, 'branch_id': bid, 'reason': reason, 'conflict_policy': policy,
                             'manual_target_date': manual})
            app.rebuild_service_date(c, day, commit=False)
        c.commit()
        return response
    except Exception:
        c.rollback()
        raise
    finally:
        # Preview intentionally has no durable writes, even when the DB takes a lock.
        c.close()


def cancel_exception(app, user, eid):
    global_entry = eid.startswith('G')
    try: ident = int(eid[1:] if global_entry else eid)
    except ValueError: raise HTTPException(400, '設定編號錯誤')
    table = 'global_closures' if global_entry else 'delivery_exceptions'
    c = app.db()
    try:
        app.lock_schedule_writes(c)
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (ident,)).fetchone()
        if not row: raise HTTPException(404, '找不到設定')
        day = row['service_date']
        if day < app.today() or app.is_report_locked(c, day):
            raise HTTPException(409, '歷史或已鎖定日期不可取消排程')
        routes = c.execute('SELECT id FROM daily_routes WHERE service_date=?', (day,)).fetchall()
        for route in routes: _route_open(app, c, day, route['id'])
        c.execute(f'DELETE FROM {table} WHERE id=?', (ident,))
        args = [day, PREFIX+'%']
        sql = "SELECT * FROM deliveries WHERE service_date=? AND status='VOID' AND void_reason LIKE ?"
        if not global_entry:
            sql += ' AND branch_id=?'
            args.append(row['branch_id'])
        for source in c.execute(sql, args).fetchall():
            branch = c.execute('SELECT * FROM branches WHERE id=?', (source['branch_id'],)).fetchone()
            if app.branch_expected_on(c, branch, day):
                changed = c.execute("""UPDATE deliveries SET status='WAITING_SECRETARY',document_original=NULL,document_final=NULL,
                    voided_at=NULL,voided_by=NULL,void_reason=NULL,row_version=row_version+1 WHERE id=?""", (source['id'],))
                app.audit(c,'USER',user['id'],user['role'],'REOPEN_POSTPONED_EMPTY','DELIVERY',source['id'],
                          before={'document_original':source['document_original'],'void_reason':source['void_reason']},
                          after={'document_original':None,'document_final':None},reason='取消停送；已順延公文保留於目標日，不自動移回')
        app.audit(c, 'USER', user['id'], user['role'], 'DELETE_SCHEDULE_EXCEPTION', 'DAY', day, before=dict(row))
        app.rebuild_service_date(c, day, commit=False)
        c.commit()
        return {'ok': True, 'documents_moved_back': False}
    except Exception:
        c.rollback()
        raise
    finally: c.close()
