import os
import sys
import types
import unittest
from unittest.mock import patch, Mock

# Local test runner may not have google-auth installed; CI installs requirements.txt.
# Stub only the import surface so MIME / HTTP behavior can still be unit-tested offline.
if 'google.oauth2.credentials' not in sys.modules:
    google=types.ModuleType('google'); oauth2=types.ModuleType('google.oauth2'); credentials=types.ModuleType('google.oauth2.credentials')
    auth=types.ModuleType('google.auth'); transport=types.ModuleType('google.auth.transport'); requests_mod=types.ModuleType('google.auth.transport.requests')
    class DummyCredentials:
        def __init__(self,*a,**k): self.token=None; self.valid=False
        def refresh(self,*a,**k): self.token='dummy'; self.valid=True
    class DummyRequest: pass
    credentials.Credentials=DummyCredentials; requests_mod.Request=DummyRequest
    sys.modules.update({'google':google,'google.oauth2':oauth2,'google.oauth2.credentials':credentials,'google.auth':auth,'google.auth.transport':transport,'google.auth.transport.requests':requests_mod})

from app.services import gmail_service

class GmailServiceTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{
            'GOOGLE_CLIENT_ID':'client-id',
            'GOOGLE_CLIENT_SECRET':'client-secret',
            'GOOGLE_REFRESH_TOKEN':'refresh-token',
            'GOOGLE_GMAIL_USER':'victor.huang@moving-match.com',
            'EMAIL_FROM_ADDRESS':'lib@moving-match.com',
            'EMAIL_FROM_NAME':'圖書物流配送暨電子簽收管理系統',
        },clear=False)
        self.env.start()
    def tearDown(self): self.env.stop()

    def test_mime_to_cc_bcc_chinese_and_attachment(self):
        msg=gmail_service.build_mime_message(['a@example.com'],'中文主旨','純文字','<b>中文</b>',['c@example.com'],['b@example.com'],[('中文報表.xlsx',b'123','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')],'daily:2026-09-03')
        self.assertIn('a@example.com',msg['To'])
        self.assertIn('c@example.com',msg['Cc'])
        self.assertIn('b@example.com',msg['Bcc'])
        self.assertIn('lib@moving-match.com',msg['From'])
        self.assertTrue(any(p.get_filename()=='中文報表.xlsx' for p in msg.walk()))

    @patch('app.services.gmail_service._get_credentials')
    @patch('app.services.gmail_service.requests.post')
    def test_send_success(self,post,getcreds):
        getcreds.return_value=Mock(token='access-token')
        post.return_value=Mock(status_code=200,json=lambda:{'id':'gmail-message-id'},text='')
        r=gmail_service.send_email(['a@example.com'],'測試','hello')
        self.assertTrue(r['success']); self.assertEqual(r['provider'],'gmail_api'); self.assertEqual(r['message_id'],'gmail-message-id')

    @patch('app.services.gmail_service.time.sleep',lambda x:None)
    @patch('app.services.gmail_service._get_credentials')
    @patch('app.services.gmail_service.requests.post')
    def test_retry_transient_then_success(self,post,getcreds):
        getcreds.return_value=Mock(token='access-token')
        bad=Mock(status_code=503,json=lambda:{'error':{'message':'temporary'}},text='temporary')
        good=Mock(status_code=200,json=lambda:{'id':'ok'},text='')
        post.side_effect=[bad,good]
        r=gmail_service.send_email(['a@example.com'],'測試','hello')
        self.assertTrue(r['success']); self.assertEqual(post.call_count,2)

    @patch('app.services.gmail_service._get_credentials')
    @patch('app.services.gmail_service.requests.post')
    def test_sender_unauthorized_no_retry(self,post,getcreds):
        getcreds.return_value=Mock(token='access-token')
        post.return_value=Mock(status_code=403,json=lambda:{'error':{'message':'Sender address not authorized'}},text='Sender address not authorized')
        r=gmail_service.send_email(['a@example.com'],'測試','hello')
        self.assertFalse(r['success']); self.assertIn('寄件權限',r['error']); self.assertEqual(post.call_count,1)

if __name__=='__main__': unittest.main()
