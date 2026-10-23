"""Verify authorization at the creation POST, independently of the form page."""

import datetime
from functools import wraps
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, Blueprint, request, session, flash, redirect, url_for

from runtime.bandwidth import creation_limits
from tests.test_credit_transactions import load_functions


class ServerVerificationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.app.testing = True
        user = Blueprint('user', __name__)
        user.add_url_rule('/', endpoint='index', view_func=lambda: 'Dashboard')
        user.add_url_rule('/login', endpoint='login_user', view_func=lambda: 'Login')
        self.app.register_blueprint(user)
        self.ns = dict(wraps=wraps, request=request, session=session,
                       redirect=redirect, url_for=url_for, update_last_seen=Mock(),
                       session_is_valid=lambda: 'email' in session)
        load_functions('managers/authentication.py', self.ns, {'login_required'})
        api = Mock()
        api.post.side_effect = [Mock(json=lambda: {'success': True}),
                                Mock(json=lambda: {'attributes': {'id': 1}})]
        database = Mock()
        database.execute_query.side_effect = [('client',), (None,), ('client',)]
        self.ns.update(
            creation_limits=creation_limits, display_cache=SimpleNamespace(clear=Mock()),
            servers=Blueprint('servers', __name__), flash=flash,
            serialized_server_creation=lambda f: f,
            pending_server_creations=lambda *a: 0,
            reserve_server_creation=lambda *a: 'test-request', finish_server_creation=Mock(),
            get_user_verification_status=Mock(return_value=False),
            _has_server_creation_approval=Mock(return_value=True),
            requests=api, DatabaseManager=database,
            RECAPTCHA_SECRET_KEY='test', PTERODACTYL_URL='https://example.invalid/', HEADERS={},
            get_ptero_id=lambda _: (7,), secrets=secrets, datetime=datetime,
            improve_list_servers=lambda _: {'attributes': {'relationships': {'servers': {'data': []}}}},
            get_eggs=lambda: [{'egg_id': 1, 'docker_image': 'test', 'startup': 'test'}],
            safe_requests=SimpleNamespace(get=Mock(return_value=Mock(json=lambda: {
                'data': [{'attributes': {'assigned': False, 'id': 1}}]}))),
            products=[{'id': 1, 'enabled': True, 'price': 0, 'limits': {'memory': 128}, 'product_limits': {}}],
            remove_credits=Mock(return_value=None), check_if_user_suspended=lambda _: False,
            add_credits=Mock(), webhook_log=Mock(),
        )
        self.ns['servers'].add_url_rule('/<int:server_id>', endpoint='server', view_func=lambda server_id: 'Server')
        load_functions('Routes/Servers.py', self.ns, {'create_server_submit'})
        self.app.register_blueprint(self.ns['servers'], url_prefix='/servers')
        self.client = self.app.test_client()
        self.form = {'name': 'Test', 'node_id': '1', 'egg_id': '1', 'plan': '1',
                     'g-recaptcha-response': 'test'}

    def login(self):
        with self.client.session_transaction() as state:
            state['email'] = 'user@example.com'
            state['account_id'] = 7
            state['pterodactyl_id'] = (7,)

    def test_unverified_post_is_blocked_before_external_calls_or_debit(self):
        self.login()
        response = self.client.post('/servers/create/submit', data=self.form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/')
        self.ns['requests'].post.assert_not_called()
        self.ns['remove_credits'].assert_not_called()
        self.ns['safe_requests'].get.assert_not_called()

    def test_verified_post_can_create_server(self):
        self.login()
        self.ns['get_user_verification_status'].return_value = True
        response = self.client.post('/servers/create/submit', data=self.form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, '/servers/1')
        self.assertEqual(self.ns['requests'].post.call_count, 2)
        self.assertTrue(self.ns['requests'].post.call_args.args[0].endswith('/api/application/servers'))

    def test_unauthenticated_post_remains_blocked(self):
        response = self.client.post('/servers/create/submit', data=self.form)
        self.assertIn('/login', response.location)
        self.ns['requests'].post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
