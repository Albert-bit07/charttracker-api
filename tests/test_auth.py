import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app.main import create_app


TOKEN = 'test-only-token-' + 'x' * 32


class AuthenticationTests(unittest.TestCase):
    def test_all_job_routes_reject_invalid_credentials_without_accessing_jobs(self):
        jobs = Mock()
        jobs.list.return_value = []
        jobs.create.return_value = {'id': 'id', 'status': 'active'}
        jobs.cancel.return_value = 'cancelled'
        with patch.dict(os.environ, {'TRACKER_API_TOKEN': TOKEN}), TestClient(create_app(lambda: jobs)) as client:
            for authorization in (None, 'Basic ' + TOKEN, 'Bearer wrong', 'Bearer'):
                for method, path in [('get', '/api/track'), ('post', '/api/track'), ('delete', '/api/track/id')]:
                    with self.subTest(authorization=authorization, method=method):
                        headers = {} if authorization is None else {'Authorization': authorization}
                        response = client.request(method, path, headers=headers, json={
                            'ticker': 'AAPL', 'buy_or_sell': 'buy', 'target_price': 100, 'entry_price': 90})
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.headers['WWW-Authenticate'], 'Bearer')
                        self.assertNotIn(TOKEN, response.text)
            jobs.create.assert_not_called()
            jobs.list.assert_not_called()
            jobs.cancel.assert_not_called()

    def test_token_is_loaded_at_startup_and_correct_token_allows_access(self):
        jobs = Mock()
        jobs.list.return_value = []
        with patch.dict(os.environ, {'TRACKER_API_TOKEN': TOKEN}), TestClient(create_app(lambda: jobs)) as client:
            os.environ['TRACKER_API_TOKEN'] = 'changed-' + TOKEN
            response = client.get('/api/track', headers={'Authorization': 'Bearer ' + TOKEN})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])
            jobs.list.assert_called_once()

    def test_bad_configuration_fails_before_opening_database_or_recovering_jobs(self):
        for token in (None, '', 'short', ' ' * 40, 'x' * 129, 'é' * 40):
            with self.subTest(token=token), patch.dict(os.environ, {}, clear=True):
                if token is not None:
                    os.environ['TRACKER_API_TOKEN'] = token
                factory = Mock()
                with self.assertRaisesRegex(ValueError, 'TRACKER_API_TOKEN'):
                    with TestClient(create_app(factory)):
                        pass
                factory.assert_not_called()
