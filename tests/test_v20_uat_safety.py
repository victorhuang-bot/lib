"""Reject unsafe remote targets before opening any socket."""
import os
import unittest
from unittest.mock import patch

from app.uat_init import HOST, DATABASE, ROLE, guarded_conninfo


class UATTargetTests(unittest.TestCase):
    def test_health_exposes_uat_form_mode_only_in_uat(self):
        import test_v194 as fixture
        from app import main
        with patch.dict(os.environ, {'MOVINGMATCH_V20_UAT':'1'}):
            self.assertTrue(main.health()['uat'])
        with patch.dict(os.environ, {'MOVINGMATCH_V20_UAT':'0'}):
            self.assertNotIn('uat', main.health())

    def test_attestation_and_request_guard_require_pinned_service_and_verified_revision(self):
        from fastapi.testclient import TestClient
        import test_v194
        from app import main
        from app.uat_serve import RENDER_UAT_SERVICE, UAT_COMMIT_ENV
        expected = 'a' * 40
        headers = {'X-MovingMatch-UAT-Commit': expected}
        with patch.dict(os.environ, {
            'MOVINGMATCH_V20_UAT': '1', 'RENDER_SERVICE_ID': RENDER_UAT_SERVICE,
            'RENDER_GIT_COMMIT': expected,
        }):
            client = TestClient(main.app)
            self.addCleanup(client.close)
            with patch.dict(os.environ, {UAT_COMMIT_ENV: ''}):
                self.assertNotIn('uat_target', client.get('/health').json())
                self.assertEqual(client.get('/health', headers=headers).status_code, 412)
            with patch.dict(os.environ, {UAT_COMMIT_ENV: expected}):
                response = client.get('/health', headers=headers)
                self.assertEqual(response.json()['uat_target']['commit'], expected)
                self.assertEqual(response.json()['uat_target']['database'], DATABASE)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                rejected = client.post('/api/auth/login', headers={
                    'X-MovingMatch-UAT-Commit': 'b' * 40})
                self.assertEqual(rejected.status_code, 412)
                with patch.dict(os.environ, {'RENDER_SERVICE_ID': 'other'}):
                    self.assertEqual(client.get('/health', headers=headers).status_code, 412)

    def test_uat_startup_does_not_provision_standard_account(self):
        import test_v194 as fixture
        from app import main
        from unittest.mock import Mock
        connection=Mock()
        with patch.dict(os.environ,{'MOVINGMATCH_V20_UAT':'1'}), \
             patch.object(main,'db',return_value=connection), \
             patch('app.schema.validate_schema'), patch.object(main,'empty_boxes_enabled',return_value=False):
            main.validate_startup_schema()
        connection.execute.assert_not_called()
        connection.commit.assert_not_called()

    def url(self, **overrides):
        from psycopg.conninfo import make_conninfo
        return make_conninfo(**({'host': HOST, 'dbname': DATABASE, 'user': ROLE,
                                'password': 'synthetic-only', 'sslmode': 'require'} | overrides))

    def test_pinned_target_requires_verified_tls(self):
        from psycopg.conninfo import conninfo_to_dict
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(conninfo_to_dict(guarded_conninfo(self.url()))['sslmode'], 'verify-full')

    def test_other_targets_and_connection_overrides_rejected(self):
        variants = ({'host':'production.neon.tech'}, {'dbname':'neondb'}, {'user':'other'},
                    {'host':HOST+',production.neon.tech'}, {'hostaddr':'127.0.0.1'},
                    {'service':'production'}, {'options':'-c search_path=other'},
                    {'sslmode':'disable'}, {'sslmode':'prefer'}, {'port':'5433'},
                    {'sslrootcert':'/tmp/untrusted.crt'}, {'passfile':'/tmp/passwords'})
        with patch.dict(os.environ, {}, clear=True):
            for changes in variants:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    guarded_conninfo(self.url(**changes))

    def test_libpq_environment_rejected(self):
        for key in ('PGHOST', 'PGHOSTADDR', 'PGSERVICE', 'PGOPTIONS', 'PGPASSWORD'):
            with patch.dict(os.environ, {key:'synthetic'}, clear=True), self.assertRaises(ValueError):
                guarded_conninfo(self.url())


if __name__ == '__main__':
    unittest.main()
