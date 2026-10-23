"""Regression checks for account isolation, quotas, suspensions and rewards."""

import datetime
import hashlib
import hmac
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import secrets
import sqlite3
from threading import Lock, Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, session, request, redirect, url_for, flash, current_app, g
from mysql.connector import IntegrityError

from runtime.bandwidth import creation_limits
from tests.test_credit_transactions import load_functions
from tests import test_credit_transactions as credit_tests


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.app.testing = True
        self.account = [7, 'User', 'member', 10, 2, 101, None, 'user@example.com', None, 'hash-1']
        self.suspended = False
        self.ns = load_functions('managers/account_sessions.py', {
            'hashlib': hashlib, 'hmac': hmac, 'session': session, 'current_app': current_app, 'g': g,
            'DatabaseManager': SimpleNamespace(execute_query=lambda *a: (
                self.account[0], self.account[9], self.account[5], self.suspended, self.account[2], self.account[3], self.account[1], None)),
        })

    def test_login_discards_previous_identity_and_rotates_session(self):
        regenerate = Mock()
        self.app.session_interface.regenerate = regenerate
        with self.app.test_request_context('/'):
            session.update(email='old@example.com', pterodactyl_id=(999,), project_id=44, pay_id='old-payment')
            self.ns['establish_session']('user@example.com', self.account)
            self.assertEqual(session['pterodactyl_id'], (101,))
            self.assertEqual(session['account_id'], 7)
            self.assertNotIn('project_id', session)
            self.assertNotIn('pay_id', session)
            regenerate.assert_called_once()
            self.assertTrue(self.ns['session_is_valid']())

    def test_password_change_revokes_old_cookie_but_accepts_fresh_login(self):
        with self.app.test_request_context('/'):
            self.ns['establish_session']('user@example.com', self.account)
            self.account[9] = 'new-password-hash'
            self.assertFalse(self.ns['session_is_valid']())
            self.assertNotIn('email', session)
            self.ns['establish_session']('user@example.com', self.account)
            self.assertTrue(self.ns['session_is_valid']())

    def test_suspension_and_legacy_sessions_fail_closed(self):
        with self.app.test_request_context('/'):
            session['email'] = 'user@example.com'
            self.assertFalse(self.ns['session_is_valid']())
            self.ns['establish_session']('user@example.com', self.account)
            self.suspended = True
            self.assertFalse(self.ns['session_is_valid']())

    def test_authoritative_panel_id_overwrites_stale_session_value(self):
        with self.app.test_request_context('/'):
            self.ns['establish_session']('user@example.com', self.account)
            session['pterodactyl_id'] = (999,)
            self.assertTrue(self.ns['session_is_valid']())
            self.assertEqual(session['pterodactyl_id'], (101,))

    def test_all_auth_decorators_reject_invalidated_session(self):
        ns = dict(session_is_valid=lambda: False, wraps=wraps, request=request,
                  redirect=redirect, url_for=lambda *a, **kw: '/login')
        load_functions('managers/authentication.py', ns, {'login_required', 'admin_required', 'support_required'})
        with self.app.test_request_context('/'):
            for name in ('login_required', 'admin_required', 'support_required'):
                protected = Mock()
                self.assertEqual(ns[name](protected)().status_code, 302)
                protected.assert_not_called()


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.lock = Lock()
        self.releases = 0
        self.acquired = True
        owner = self

        class Cursor:
            def execute(self, sql, params):
                if 'GET_LOCK' in sql:
                    self.row = (1 if owner.acquired and owner.lock.acquire(timeout=5) else 0,)
                else:
                    owner.lock.release()
                    owner.releases += 1

            def fetchone(self): return self.row
            def close(self): pass

        def query(sql, params):
            if 'SELECT role' in sql: return ('member',)
            return (datetime.datetime.now() - datetime.timedelta(days=1),)

        database = SimpleNamespace(get_connection=lambda: (Mock(), Cursor()), execute_query=query)
        self.owned = [{'attributes': {'id': 1, 'user': 7, 'limits': {'memory': 512}}}]
        self.fail = False
        self.reservations = {}

        def post(url, **kw):
            if 'siteverify' in url: return Mock(json=lambda: {'success': True})
            if self.fail: raise RuntimeError('Injected API failure')
            self.owned.append({'attributes': {'id': len(self.owned) + 1, 'user': 7, 'limits': {'memory': 512}}})
            self.assertEqual(kw['json']['user'], 7)
            return Mock(json=lambda: {'attributes': {'id': len(self.owned)}})

        def ledger_query(sql, params=None, **kwargs):
            if sql.startswith('SELECT request_id'):
                return [(key, record[0]) for key, record in self.reservations.items() if record[1] == 'pending']
            if sql.startswith('INSERT INTO server_creation_requests'):
                self.reservations[params[0]] = (None, 'pending')
                return
            if "SET state='observed'" in sql:
                self.reservations[params[0]] = (self.reservations[params[0]][0], 'observed')
                return
            if sql.startswith('UPDATE server_creation_requests SET server_id'):
                self.reservations[params[2]] = (params[0], params[1])
                return
            return query(sql, params)

        database.execute_query = ledger_query

        self.ns = load_functions('managers/server_creation_lock.py', {
            'DatabaseManager': database, 'session': session, 'wraps': wraps,
            'uuid4': __import__('uuid').uuid4,
        })
        self.ns.update(
            creation_limits=creation_limits, display_cache=SimpleNamespace(clear=Mock()),
            servers=SimpleNamespace(route=lambda *a, **kw: lambda f: f), login_required=lambda f: f,
            request=request, session=session, flash=flash, redirect=lambda x: x, url_for=lambda *a, **kw: '/servers/' + str(kw['server_id']) if a[0] == 'servers.server' else '/',
            get_user_verification_status=lambda _: True, _has_server_creation_approval=lambda _: True,
            get_ptero_id=lambda _: (7,), improve_list_servers=lambda _: {'attributes': {'relationships': {'servers': {'data': list(self.owned)}}}},
            DatabaseManager=database, requests=SimpleNamespace(post=post),
            RECAPTCHA_SECRET_KEY='test', PTERODACTYL_URL='https://example.invalid/', HEADERS={},
            datetime=datetime, secrets=secrets,
            get_eggs=lambda: [{'egg_id': 1, 'docker_image': 'test', 'startup': 'test'}],
            safe_requests=SimpleNamespace(get=lambda *a, **kw: Mock(json=lambda: {'data': [{'attributes': {'id': 1, 'assigned': False}}]})),
            products=[{'id': 1, 'price': 10, 'enabled': True, 'limits': {'memory': 512}, 'product_limits': {}}],
            remove_credits=Mock(return_value=None), check_if_user_suspended=lambda _: False, webhook_log=Mock(),
        )
        load_functions('Routes/Servers.py', self.ns, {'create_server_submit'})

    def test_successful_creation_opens_created_server(self):
        destination=self.submit()
        self.assertEqual(destination,'/servers/2')

    def submit(self):
        with self.app.test_request_context('/', method='POST', data={'name': 'Test', 'node_id': '1', 'egg_id': '1', 'plan': '1'}):
            session.update(email='user@example.com', account_id=7, pterodactyl_id=(999,))
            return self.ns['create_server_submit']()

    def test_concurrent_requests_cannot_exceed_two_server_quota(self):
        start = Barrier(3)
        def concurrent(_):
            start.wait(timeout=5)
            return self.submit()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(concurrent, range(3)))
        self.assertEqual(len(self.owned), 2)
        self.assertEqual(self.releases, 3)
        self.assertEqual(self.ns['remove_credits'].call_count, 1)

    def test_lock_timeout_prevents_creation_and_debit(self):
        self.acquired = False
        self.assertEqual(self.submit()[1], 409)
        self.assertEqual(len(self.owned), 1)
        self.ns['remove_credits'].assert_not_called()

    def test_lock_is_released_after_api_exception(self):
        self.fail = True
        with self.assertRaises(RuntimeError): self.submit()
        self.assertEqual(self.releases, 1)
        self.fail = False
        self.submit()
        # The first remote request may still complete. Its pending slot blocks
        # a second creation until inventory confirms or staff reconcile it.
        self.assertEqual(len(self.owned), 1)
        self.assertEqual(self.releases, 2)

    def test_definitive_rejection_releases_pending_reservation(self):
        original = self.ns['requests'].post
        def reject(url, **kwargs):
            if 'siteverify' in url: return Mock(json=lambda: {'success': True})
            return Mock(json=lambda: {'errors': [{'detail': 'Node is full'}]})
        self.ns['requests'] = SimpleNamespace(post=reject)
        self.ns['add_credits'] = Mock()
        self.submit()
        self.assertEqual([r[1] for r in self.reservations.values()], ['failed'])
        self.ns['requests'] = SimpleNamespace(post=original)
        self.submit()
        self.assertEqual(len(self.owned), 2)

    def test_inventory_reconciles_a_completed_ambiguous_request(self):
        self.fail = True
        with self.assertRaises(RuntimeError): self.submit()
        key = next(iter(self.reservations))
        self.owned.append({'attributes': {'id': 2, 'user': 7, 'external_id': key, 'limits': {'memory': 512}}})
        self.fail = False
        self.submit()
        self.assertEqual(self.reservations[key][1], 'observed')
        self.assertEqual(len(self.owned), 2)

    def test_inventory_failure_blocks_creation(self):
        self.ns['improve_list_servers'] = lambda _: None
        self.assertEqual(self.submit()[1], 503)
        self.assertEqual(len(self.owned), 1)
        self.ns['remove_credits'].assert_not_called()


class SuspensionTests(unittest.TestCase):
    def setUp(self):
        self.cursor = Mock()
        @contextmanager
        def transaction(): yield self.cursor
        self.api = Mock()
        self.api.post.return_value.status_code = 204
        self.ns = load_functions('managers/suspension_manager.py', {})
        self.ns.update(DatabaseManager=SimpleNamespace(transaction=transaction, execute_query=Mock(return_value=(7, 'user@example.com'))), requests=self.api,
                       creation_limits=creation_limits, display_cache=SimpleNamespace(clear=Mock()),
                       PTERODACTYL_URL='https://example.invalid/', HEADERS={}, datetime=datetime,
                       get_server_information=lambda _: {'attributes': {'user': 7}},
                       queue_suspension_email=Mock(), clear_suspension_email_keys=Mock(),
                       kick_suspension_email_delivery=Mock())
        load_functions('managers/server_manager.py', self.ns, {'suspend_server', 'unsuspend_server'})

    def test_empty_and_whitespace_reasons_are_rejected_before_writes(self):
        for reason in ('', '  ', None, 'x' * 1001):
            with self.assertRaises(ValueError): self.ns['suspend_server'](1, reason)
            with self.assertRaises(ValueError): self.ns['set_user_suspension'](7, True, reason)
        self.api.post.assert_not_called()
        self.cursor.execute.assert_not_called()

    def test_manual_server_reason_is_persisted_after_successful_api_call(self):
        self.cursor.fetchone.side_effect = [('unknown', False), ('user@example.com',)]
        self.assertEqual(self.ns['suspend_server'](1, '  Abuse report  '), 204)
        self.assertTrue(any(call.args[1] == ('Abuse report', 'manual', 1)
                            for call in self.cursor.execute.call_args_list))
        self.ns['queue_suspension_email'].assert_called_once_with(
            self.cursor, 'server', 1, 'user@example.com', 'Abuse report', label=None, source='manual')

    def test_automatic_suspension_does_not_replace_manual_reason(self):
        self.cursor.fetchone.return_value = ('manual', True)
        self.assertEqual(self.ns['suspend_server'](1, 'Insufficient credits', 'billing'), 409)
        self.api.post.assert_not_called()

    def test_missing_owner_is_rejected_before_remote_suspension(self):
        self.ns['DatabaseManager'].execute_query.return_value = None
        with self.assertRaisesRegex(ValueError, 'owner account'):
            self.ns['suspend_server'](1, 'Abuse report')
        self.api.post.assert_not_called()
        self.cursor.execute.assert_not_called()

    def test_manual_unknown_and_inactive_records_are_not_auto_resumed(self):
        for row in [('manual', True), ('unknown', False), ('billing', False)]:
            self.cursor.fetchone.return_value = row
            self.assertEqual(self.ns['unsuspend_server'](1, automatic=True), 409)
        self.api.post.assert_not_called()

    def test_banned_and_missing_accounts_are_not_auto_resumed(self):
        for account in [(1, None), None]:
            self.cursor.fetchone.side_effect = [('billing', True), account]
            self.assertEqual(self.ns['unsuspend_server'](1, automatic=True), 409)
        self.api.post.assert_not_called()

    def test_active_billing_account_can_auto_resume(self):
        self.cursor.fetchone.side_effect = [('billing', True), (0, None)]
        self.assertEqual(self.ns['unsuspend_server'](1, automatic=True), 204)
        self.api.post.assert_called_once()

    def test_inactivity_recovery_requires_a_recent_login(self):
        self.cursor.fetchone.side_effect = [('inactivity', True), (0, datetime.datetime.now() - datetime.timedelta(days=16))]
        self.assertEqual(self.ns['unsuspend_server'](1, automatic=True), 409)
        self.api.post.assert_not_called()
        self.cursor.fetchone.side_effect = [('inactivity', True), (0, datetime.datetime.now())]
        self.assertEqual(self.ns['unsuspend_server'](1, automatic=True), 204)

    def test_scheduler_never_resumes_manual_or_unknown_suspensions(self):
        server = {'attributes': {'id': 1, 'user': 7, 'name': 'Test', 'suspended': True}}
        for record in (None, {'active': True, 'source': 'manual'}, {'active': False, 'source': 'billing'}):
            api = Mock()
            api.get.side_effect = [Mock(status_code=200, json=lambda: {'data': [server]}),
                                   Mock(status_code=200, json=lambda: {'attributes': {'email': 'user@example.com'}})]
            threads = Mock()
            self.ns.update(safe_requests=api, threading=threads, webhook_log=Mock(),
                           get_credits=lambda _: 100, convert_to_product=lambda _: {'price': 720},
                           check_if_user_suspended=lambda _: 0,
                           get_server_suspension=lambda _: record)
            load_functions('managers/credit_manager.py', self.ns, {'check_to_unsuspend'})
            self.ns['check_to_unsuspend']()
            threads.Thread.assert_not_called()


class BumpRewardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = credit_tests.CreditTransactionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with sqlite3.connect(self.fixture.path) as db:
            db.executescript("ALTER TABLE users ADD COLUMN discord_id INTEGER; ALTER TABLE users ADD COLUMN suspended INTEGER DEFAULT 0; UPDATE users SET discord_id=77; CREATE TABLE discord_bump_rewards (message_id INTEGER PRIMARY KEY, guild_id INTEGER, discord_id INTEGER);")
        db.close()
        self.ns = self.fixture.ns
        self.ns.update(DISCORD_GUILD_ID=123, IntegrityError=IntegrityError)
        load_functions('managers/bump_rewards.py', self.ns, {'award_bump'})

    def test_duplicate_events_credit_once_and_keep_member_role(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.ns['award_bump'](44, 123, 77), range(6)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.fixture.balance(), 14)
        self.assertEqual(self.fixture.scalar('SELECT role FROM users'), 'member')

    def test_foreign_guild_and_banned_user_receive_no_reward(self):
        self.assertFalse(self.ns['award_bump'](44, 999, 77))
        with sqlite3.connect(self.fixture.path) as db: db.execute('UPDATE users SET suspended=1')
        db.close()
        self.assertFalse(self.ns['award_bump'](44, 123, 77))
        self.assertEqual(self.fixture.balance(), 10)

    def test_failed_credit_rolls_back_reward_marker_for_retry(self):
        original = self.ns['_add_credits_in_transaction']
        self.ns['_add_credits_in_transaction'] = Mock(side_effect=RuntimeError('Credit failed'))
        with self.assertRaises(RuntimeError): self.ns['award_bump'](44, 123, 77)
        self.assertEqual(self.fixture.scalar('SELECT COUNT(*) FROM discord_bump_rewards'), 0)
        self.ns['_add_credits_in_transaction'] = original
        self.assertTrue(self.ns['award_bump'](44, 123, 77))


if __name__ == '__main__': unittest.main()
