import os
import base64
import json
import time
import threading
import hashlib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import quote

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleAuthRequest

GMAIL_SEND_SCOPE = 'https://www.googleapis.com/auth/gmail.send'
TOKEN_URI = 'https://oauth2.googleapis.com/token'
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_credential_lock = threading.Lock()
_credentials = None


def gmail_config():
    return {
        'client_id': os.getenv('GOOGLE_CLIENT_ID','').strip(),
        'client_secret': os.getenv('GOOGLE_CLIENT_SECRET','').strip(),
        'refresh_token': os.getenv('GOOGLE_REFRESH_TOKEN','').strip(),
        'gmail_user': (os.getenv('GOOGLE_GMAIL_USER') or 'victor.huang@moving-match.com').strip(),
        'from_address': (os.getenv('EMAIL_FROM_ADDRESS') or 'lib@moving-match.com').strip(),
        'from_name': (os.getenv('EMAIL_FROM_NAME') or '圖書物流配送暨電子簽收管理系統').strip(),
    }


def gmail_diagnostics():
    cfg=gmail_config()
    checks={
        'GOOGLE_CLIENT_ID': bool(cfg['client_id']),
        'GOOGLE_CLIENT_SECRET': bool(cfg['client_secret']),
        'GOOGLE_REFRESH_TOKEN': bool(cfg['refresh_token']),
        'GOOGLE_GMAIL_USER': bool(cfg['gmail_user']),
        'EMAIL_FROM_ADDRESS': bool(cfg['from_address']),
        'EMAIL_FROM_NAME': bool(cfg['from_name']),
    }
    return {
        'provider':'gmail_api',
        'checks': checks,
        'missing':[k for k,v in checks.items() if not v],
        'configured': all(checks.values()),
        'oauth_configured': checks['GOOGLE_CLIENT_ID'] and checks['GOOGLE_CLIENT_SECRET'] and checks['GOOGLE_REFRESH_TOKEN'],
    }


def _sanitize_error(value):
    text=str(value or '')
    cfg=gmail_config()
    for secret in (cfg['client_secret'],cfg['refresh_token']):
        if secret:
            text=text.replace(secret,'[REDACTED]')
    text=re_sub_bearer(text)
    return text[:2000]


def re_sub_bearer(text):
    import re
    return re.sub(r'Bearer\\s+[-A-Za-z0-9._~+/=]+','Bearer [REDACTED]',text,flags=re.I)


def _get_credentials():
    global _credentials
    cfg=gmail_config()
    diag=gmail_diagnostics()
    if not diag['configured']:
        raise RuntimeError('Gmail API 尚未完整設定：'+', '.join(diag['missing']))
    with _credential_lock:
        if _credentials is None:
            _credentials=Credentials(
                token=None,
                refresh_token=cfg['refresh_token'],
                token_uri=TOKEN_URI,
                client_id=cfg['client_id'],
                client_secret=cfg['client_secret'],
                scopes=[GMAIL_SEND_SCOPE],
            )
        if not _credentials.valid:
            try:
                _credentials.refresh(GoogleAuthRequest())
            except Exception as exc:
                msg=_sanitize_error(exc)
                low=msg.lower()
                if 'invalid_grant' in low:
                    raise RuntimeError('Refresh token invalid (invalid_grant)')
                if 'invalid_client' in low:
                    raise RuntimeError('OAuth authentication failed (invalid_client)')
                raise RuntimeError('OAuth authentication failed: '+msg)
        return _credentials


def _normalize_addresses(value):
    if not value: return []
    if isinstance(value,str): return [value]
    return [str(x).strip() for x in value if str(x).strip()]


def build_mime_message(to, subject, body_text=None, body_html=None, cc=None, bcc=None, attachments=None, delivery_identifier=None):
    cfg=gmail_config()
    to_list=_normalize_addresses(to); cc_list=_normalize_addresses(cc); bcc_list=_normalize_addresses(bcc)
    if not (to_list or cc_list or bcc_list):
        raise ValueError('至少需要一位 Email 收件人')
    msg=EmailMessage()
    msg['Subject']=str(subject or '')
    msg['From']=formataddr((cfg['from_name'],cfg['from_address']))
    if to_list: msg['To']=', '.join(to_list)
    if cc_list: msg['Cc']=', '.join(cc_list)
    if bcc_list: msg['Bcc']=', '.join(bcc_list)
    # Stable Message-ID makes application retries easier to identify in mail systems/logs.
    if delivery_identifier:
        digest=hashlib.sha256(delivery_identifier.encode('utf-8')).hexdigest()[:32]
        domain=(cfg['from_address'].split('@',1)[1] if '@' in cfg['from_address'] else 'moving-match.com')
        msg['Message-ID']=f'<{digest}@{domain}>'
    else:
        domain=(cfg['from_address'].split('@',1)[1] if '@' in cfg['from_address'] else None)
        msg['Message-ID']=make_msgid(domain=domain)
    if body_text is None and body_html is None: body_text=''
    if body_text is not None: msg.set_content(str(body_text),charset='utf-8')
    if body_html is not None:
        if body_text is None: msg.set_content('此郵件包含 HTML 內容。',charset='utf-8')
        msg.add_alternative(str(body_html),subtype='html',charset='utf-8')
    for item in (attachments or []):
        if isinstance(item,dict):
            filename=item.get('filename'); data=item.get('data'); mime=item.get('mime_type') or item.get('mime') or 'application/octet-stream'
        else:
            filename,data,mime=item
        if isinstance(data,str): data=data.encode('utf-8')
        maintype,subtype=(mime.split('/',1)+['octet-stream'])[:2] if '/' in mime else ('application','octet-stream')
        msg.add_attachment(data or b'',maintype=maintype,subtype=subtype,filename=str(filename or 'attachment'))
    return msg


def _friendly_http_error(status, payload, text):
    detail=''
    try:
        err=(payload or {}).get('error',{})
        detail=err.get('message') or ''
        reasons=[x.get('reason','') for x in err.get('errors',[]) if isinstance(x,dict)]
        if reasons: detail=(detail+' '+' '.join(reasons)).strip()
    except Exception:
        pass
    detail=_sanitize_error(detail or text or f'HTTP {status}')
    low=detail.lower()
    cfg=gmail_config()
    if status==401: return 'OAuth authentication failed (401)'
    if status==403:
        if 'scope' in low or 'insufficient' in low: return 'Gmail API insufficient_scope (403)'
        if cfg['from_address'].lower()!=cfg['gmail_user'].lower():
            return f'{cfg["from_address"]} 尚未取得 Google Workspace 寄件權限。'
        return 'Gmail API permission denied (403): '+detail
    if status==400:
        if any(x in low for x in ('from header','send as','sender','delegat','alias')) and cfg['from_address'].lower()!=cfg['gmail_user'].lower():
            return f'{cfg["from_address"]} 尚未取得 Google Workspace 寄件權限。'
        if 'invalid_grant' in low: return 'Refresh token invalid (invalid_grant)'
        if 'invalid_client' in low: return 'OAuth authentication failed (invalid_client)'
    if status==429: return 'Gmail API quota exceeded / rate limited (429)'
    return f'Google Gmail API HTTP {status}: {detail}'


def send_email(to, subject, body_text=None, body_html=None, cc=None, bcc=None, attachments=None, delivery_identifier=None, timeout=20):
    cfg=gmail_config()
    try:
        msg=build_mime_message(to,subject,body_text,body_html,cc,bcc,attachments,delivery_identifier)
        raw=base64.urlsafe_b64encode(msg.as_bytes()).decode('ascii')
        creds=_get_credentials()
        url='https://gmail.googleapis.com/gmail/v1/users/'+quote(cfg['gmail_user'],safe='')+'/messages/send'
        delays=[0,1,2,4]
        last_error=None
        for attempt in range(4):
            if delays[attempt]: time.sleep(delays[attempt])
            try:
                resp=requests.post(url,headers={'Authorization':'Bearer '+creds.token,'Content-Type':'application/json'},json={'raw':raw},timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error='Google API temporarily unavailable: '+_sanitize_error(exc)
                if attempt<3: continue
                return {'success':False,'provider':'gmail_api','message_id':None,'error':last_error}
            try: payload=resp.json()
            except Exception: payload={}
            if 200 <= resp.status_code < 300:
                return {'success':True,'provider':'gmail_api','message_id':payload.get('id'),'error':None}
            err=_friendly_http_error(resp.status_code,payload,resp.text)
            last_error=err
            if resp.status_code in _RETRY_STATUSES and attempt<3:
                continue
            return {'success':False,'provider':'gmail_api','message_id':None,'error':err}
        return {'success':False,'provider':'gmail_api','message_id':None,'error':last_error or 'Gmail API send failed'}
    except Exception as exc:
        return {'success':False,'provider':'gmail_api','message_id':None,'error':_sanitize_error(exc)}
