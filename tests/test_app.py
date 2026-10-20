import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from werkzeug.security import generate_password_hash


class PortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        os.environ['DATABASE_PATH'] = str(Path(cls.tmpdir.name) / 'test.sqlite3')
        os.environ['SECRET_KEY'] = 'test-secret-key'
        os.environ['ADMIN_PASSWORD_HASH'] = generate_password_hash('test-password')
        from app import app, recent_attempts
        cls.app = app
        cls.attempts = recent_attempts
        cls.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        os.environ.pop('FORMSPREE_FORM_ID', None)
        os.environ.pop('CONTACT_DELIVERY', None)
        self.attempts.clear()
        with sqlite3.connect(os.environ['DATABASE_PATH']) as db:
            db.execute('DELETE FROM messages')
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['csrf_token'] = 'token'
        self.payload = dict(name='Jane Doe', email='jane@example.com', message='Hello, I am interested in your projects.', csrf_token='token')

    def test_homepage(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Rahul Choudhary', response.data)

    def test_explicit_local_test_mode_and_admin(self):
        os.environ['CONTACT_DELIVERY'] = 'local'
        response = self.client.post('/contact', data=self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'not emailed', response.data)
        self.assertEqual(self.client.get('/admin').status_code, 302)
        self.assertEqual(self.client.post('/admin/login', data={'password': 'test-password', 'csrf_token': 'token'}).status_code, 302)
        response = self.client.get('/admin')
        self.assertIn(b'Jane Doe', response.data)
        self.assertIn(b'Hello, I am interested', response.data)
        os.environ.pop('CONTACT_DELIVERY', None)

    def test_missing_formspree_config_fails_closed_without_saving(self):
        response = self.client.post('/contact', data=self.payload)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'not configured', response.data)
        with sqlite3.connect(os.environ['DATABASE_PATH']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_formspree_success_mocks_real_https_delivery(self):
        os.environ['FORMSPREE_FORM_ID'] = 'abc123xyz'
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"ok":true}'
        with patch('app.urlopen') as mock_send:
            mock_send.return_value.__enter__.return_value = fake_response
            response = self.client.post('/contact', data=self.payload)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Message submitted', response.data)
            mock_send.assert_called_once()
            request_obj = mock_send.call_args[0][0]
            self.assertEqual(request_obj.full_url, 'https://formspree.io/f/abc123xyz')
            self.assertEqual(request_obj.get_method(), 'POST')
            self.assertEqual(json.loads(request_obj.data.decode('utf-8')), {
                'name': 'Jane Doe',
                'email': 'jane@example.com',
                'message': 'Hello, I am interested in your projects.',
            })
        with sqlite3.connect(os.environ['DATABASE_PATH']) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_formspree_rejection_never_claims_success(self):
        os.environ['FORMSPREE_FORM_ID'] = 'abc123xyz'
        with patch('app.urlopen', side_effect=HTTPError('https://formspree.io/f/abc123xyz', 422, 'Bad field', {}, None)):
            response = self.client.post('/contact', data=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertIn(b'could not accept', response.data)

    def test_formspree_rate_limit_and_network_failure(self):
        os.environ['FORMSPREE_FORM_ID'] = 'abc123xyz'
        with patch('app.urlopen', side_effect=HTTPError('https://formspree.io/f/abc123xyz', 429, 'Too many', {}, None)):
            response = self.client.post('/contact', data=self.payload)
        self.assertEqual(response.status_code, 429)
        with patch('app.urlopen', side_effect=URLError('Network unreachable')):
            response = self.client.post('/contact', data=self.payload)
        self.assertEqual(response.status_code, 502)
        self.assertIn(b'Could not send', response.data)

    def test_invalid_input_and_csrf(self):
        self.assertEqual(self.client.post('/contact', data={'name': 'x'}).status_code, 403)
        self.assertEqual(self.client.post('/contact', data={'name': 'x', 'email': 'bad', 'message': 'short', 'csrf_token': 'token'}).status_code, 400)

    def test_bad_form_id_cannot_redirect_request_elsewhere(self):
        os.environ['FORMSPREE_FORM_ID'] = 'https://untrusted.example/submit'
        with patch('app.urlopen') as mocked:
            response = self.client.post('/contact', data=self.payload)
            self.assertEqual(response.status_code, 503)
            mocked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
