"""Server-side reason validation must hold even when the modal is bypassed."""

from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, request, session, jsonify, flash, redirect
from test_credit_transactions import load_functions


class AdminReasonTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.cursor = Mock()
        @contextmanager
        def transaction(): yield self.cursor
        self.database = Mock()
        self.database.transaction = transaction
        self.ns = load_functions('managers/suspension_manager.py', {
            'DatabaseManager': self.database, 'queue_suspension_email': Mock(),
            'clear_suspension_email_keys': Mock(), 'kick_suspension_email_delivery': Mock(),
        })
        self.ns.update(admin=SimpleNamespace(route=lambda *a, **kw: lambda f: f),
                       admin_required=lambda f: f, request=request, session=session, jsonify=jsonify,
                       flash=flash, redirect=redirect, url_for=lambda *a, **kw: '/', webhook_log=Mock(),
                       get_server_information=lambda _: {'attributes': {'id': 1, 'name': 'Test', 'suspended': False}},
                       suspend_server=Mock(), unsuspend_server=Mock())
        load_functions('Routes/admin/users.py', self.ns, {'admin_toggle_suspension'})
        load_functions('Routes/admin/servers.py', self.ns, {'admin_toggle_server_suspension'})

    def test_user_endpoint_rejects_empty_reason_without_changing_state(self):
        account = [7, 'User'] + [None] * 13 + [0]
        self.database.execute_query.return_value = tuple(account)
        for reason in ('', '   '):
            with self.app.test_request_context('/', method='POST', data={'suspended': '1', 'reason': reason}):
                session['email'] = 'admin@example.com'
                _, status = self.ns['admin_toggle_suspension']('7')
                self.assertEqual(status, 400)
        self.cursor.execute.assert_not_called()

    def test_server_endpoint_rejects_empty_reason_without_api_call(self):
        for reason in ('', '   '):
            with self.app.test_request_context('/', method='POST', data={'action': 'suspend', 'reason': reason}):
                session['email'] = 'admin@example.com'
                _, status = self.ns['admin_toggle_server_suspension']('1')
                self.assertEqual(status, 400)
        self.ns['suspend_server'].assert_not_called()

    def test_server_endpoint_passes_valid_reason_and_manual_source(self):
        with self.app.test_request_context('/', method='POST', data={'action': 'suspend', 'reason': '  Abuse report  '}):
            session['email'] = 'admin@example.com'
            self.assertEqual(self.ns['admin_toggle_server_suspension']('1').status_code, 302)
        self.ns['suspend_server'].assert_called_once_with('1', 'Abuse report', 'manual')

    def test_user_reason_and_flag_are_written_in_same_transaction(self):
        self.cursor.fetchone.return_value = (7, 'user@example.com')
        self.ns['set_user_suspension'](7, True, 'Abuse report', actor='admin@example.com')
        calls = self.cursor.execute.call_args_list
        self.assertIn('FOR UPDATE', calls[0].args[0])
        self.assertEqual(calls[1].args[1], (True, 7))
        self.assertEqual(calls[2].args[1], (7, 'Abuse report', 'manual', 'admin@example.com'))


if __name__ == '__main__': unittest.main()
