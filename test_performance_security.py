"""Bounded caches, pooled connections, fresh permissions and login abuse guards."""

import ast
import __future__
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import datetime
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Blueprint, request, session, g, has_request_context, flash, redirect, url_for
from flask_limiter import Limiter
import mysql.connector
from werkzeug.datastructures import MultiDict

from display_cache import DisplayCache
from request_security import client_ip, peer_ip, login_limit_key, login_credentials
from test_credit_transactions import load_functions


class DisplayCacheTests(unittest.TestCase):
    def test_parallel_misses_fetch_once_and_return_independent_values(self):
        cache = DisplayCache()
        def fetch():
            time.sleep(.02)
            return {'data': [1]}
        loader = Mock(side_effect=fetch)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: cache.get_or_load(('owner', 7), loader), range(8)))
        self.assertEqual(loader.call_count, 1)
        results[0]['data'].append(9)
        self.assertEqual(results[1]['data'], [1])
        self.assertEqual(cache.get_or_load(('owner', 8), loader), {'data': [1]})
        self.assertEqual(loader.call_count, 2)

    def test_expiry_eviction_and_failed_loads(self):
        cache = DisplayCache(max_entries=2)
        with patch('display_cache.monotonic', return_value=0):
            cache.get_or_load('a', lambda: 1, ttl=1)
        with patch('display_cache.monotonic', return_value=2):
            self.assertEqual(cache.get_or_load('a', lambda: 2), 2)
        cache.get_or_load('b', lambda: 3)
        cache.get_or_load('c', lambda: 4)
        self.assertEqual(len(cache.entries), 2)
        self.assertNotIn('a', cache.entries)
        absent = Mock(return_value=None)
        for _ in range(2): cache.get_or_load('missing', absent)
        self.assertEqual(absent.call_count, 2)
        with self.assertRaises(RuntimeError):
            cache.get_or_load('bad', Mock(side_effect=RuntimeError('API unavailable')))
        self.assertNotIn('bad', cache.entries)

    def test_invalidation_during_fetch_does_not_repopulate_old_data(self):
        cache = DisplayCache()
        def old_inventory():
            cache.clear()  # A mutation finishes while this read is in flight.
            return {'version': 1}
        cache.get_or_load('inventory', old_inventory)
        self.assertEqual(cache.get_or_load('inventory', lambda: {'version': 2}), {'version': 2})


class DatabasePerformanceTests(unittest.TestCase):
    def test_server_page_fetches_details_once_and_always_checks_fresh_owner(self):
        info = {'attributes': {'user': 7, 'suspended': False}}
        details = Mock(return_value=info)
        inventory = Mock(return_value={'attributes': {'relationships': {'servers': {'data': []}}}})
        ns = load_functions('Routes/Servers.py', dict(
            servers=SimpleNamespace(route=lambda *a, **kw: lambda f: f), login_required=lambda f: f,
            get_user_verification_ptero_id_and_credits=lambda _: (True, 7, 10),
            get_server_information=details, display_servers=inventory, session=session, products=[],
            get_nodes=lambda: [], convert_to_product=lambda _: {}, render_template=lambda *a, **kw: 'OK'), {'server'})
        app = Flask(__name__)
        app.secret_key = 'test'
        with app.test_request_context('/'):
            session['email'] = 'a@example.com'
            self.assertEqual(ns['server']('123'), 'OK')
            details.assert_called_once_with('123')
            inventory.reset_mock()
            info['attributes']['user'] = 8
            self.assertIn("don't own", ns['server']('123'))
            inventory.assert_not_called()

    def test_index_migration_skips_existing_equivalent_indexes(self):
        ns = load_functions('scripts/add_performance_indexes.py', {
            'INDEXES': [('users', 'ptero_idx', ('pterodactyl_id',))]}, {'apply_indexes'})
        cursor = Mock()
        cursor.fetchall.return_value = [('other_index_name', 'pterodactyl_id')]
        ns['apply_indexes'](cursor)
        self.assertEqual(cursor.execute.call_count, 1)
        cursor.fetchall.return_value = []
        ns['apply_indexes'](cursor)
        self.assertIn('ALTER TABLE `users` ADD INDEX `ptero_idx`', cursor.execute.call_args.args[0])

    def test_automatic_indexes_do_not_prevent_startup_when_permissions_are_missing(self):
        import logging
        migration = Mock(side_effect=mysql.connector.Error('ALTER denied', errno=1142))
        ns = load_functions('scripts/add_performance_indexes.py', {
            'add_indexes': migration, 'mysql': mysql, 'logging': logging,
            '__name__': 'index_migration_test',
        }, {'ensure_performance_indexes'})
        with self.assertLogs('index_migration_test', level='WARNING'):
            ns['ensure_performance_indexes']()
        migration.assert_called_once()

    def test_connections_reuse_pool_and_keep_process_and_database_separate(self):
        node = next(n for n in ast.parse(Path('managers/database_manager.py').read_text()).body
                    if isinstance(n, ast.ClassDef))
        factory = Mock()
        ns = dict(BaseManager=object, contextmanager=contextmanager, os=os, threading=threading,
                  config=SimpleNamespace(DATABASE_POOL_SIZE=4), MySQLConnectionPool=factory,
                  HOST='localhost', USER='test', PASSWORD='test', DATABASE='panel')
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<database>', 'exec',
                     flags=__future__.annotations.compiler_flag), ns)
        db = ns['DatabaseManager']
        for _ in range(3):
            connection, cursor = db.get_connection()
            cursor.close()
            connection.close()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(factory.call_args.kwargs['pool_size'], 4)
        self.assertTrue(factory.call_args.kwargs['pool_reset_session'])
        db.get_connection('other')
        self.assertEqual(factory.call_count, 2)
        with patch('os.getpid', return_value=999999): db.get_connection()
        self.assertEqual(factory.call_count, 3)
        connection = factory.return_value.get_connection.return_value
        connection.cursor.side_effect = RuntimeError('cursor failed')
        connection.close.reset_mock()
        with self.assertRaises(RuntimeError): db.get_connection()
        connection.close.assert_called_once()
        connection.cursor.side_effect = None
        cursor = connection.cursor.return_value
        cursor.close.side_effect = RuntimeError('cleanup failed')
        connection.close.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            with db.transaction():
                pass
        connection.close.assert_called_once()

    def test_role_cache_is_only_per_request_and_activity_updates_are_throttled(self):
        database = SimpleNamespace(execute_query=Mock(return_value=('admin',)))
        ns = load_functions('managers/user_manager.py', dict(
            DatabaseManager=database, g=g, has_request_context=has_request_context, datetime=datetime),
            {'get_role', 'is_admin', 'is_support', 'update_last_seen'})
        app = Flask(__name__)
        with app.test_request_context('/'):
            self.assertTrue(ns['is_admin']('a@example.com'))
            self.assertFalse(ns['is_support']('a@example.com'))
        self.assertEqual(database.execute_query.call_count, 1)
        database.execute_query.return_value = ('member',)
        with app.test_request_context('/'):
            self.assertFalse(ns['is_admin']('a@example.com'))
        self.assertEqual(database.execute_query.call_count, 2)
        ns['update_last_seen']('a@example.com')
        sql, params = database.execute_query.call_args.args
        self.assertIn('last_seen < %s', sql)
        self.assertEqual((params[0] - params[2]).total_seconds(), 60)

    def test_admin_list_batches_reasons_in_one_join(self):
        database = SimpleNamespace(execute_query=Mock(side_effect=[(2,), [
            ('A', 0, 'member', 'a@example.com', 1, 1, 7, 'Abuse'),
            ('B', 0, 'member', 'b@example.com', 1, 2, 8, None),
        ]]))
        render = Mock(return_value='OK')
        ns = load_functions('Routes/admin/users.py', dict(
            admin=SimpleNamespace(route=lambda *a, **kw: lambda f: f),
            admin_required=lambda f: f, DatabaseManager=database, request=request,
            render_template=render), {'users'})
        with Flask(__name__).test_request_context('/'):
            self.assertEqual(ns['users'](), 'OK')
        self.assertEqual(database.execute_query.call_count, 2)
        self.assertIn('LEFT JOIN user_suspensions', database.execute_query.call_args.args[0])
        self.assertEqual(render.call_args.kwargs['users'][0]['suspension_reason'], 'Abuse')


class LoginGuardTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.limiter = Limiter(key_func=client_ip, storage_uri='memory://', headers_enabled=True)
        self.limiter.init_app(self.app)
        self.user = Blueprint('user', __name__)
        self.user.add_url_rule('/', endpoint='index', view_func=lambda: 'Dashboard')
        self.limiter.limit('60 per hour', methods=['POST'])(self.user)
        self.login = Mock(return_value=None)
        self.api = Mock()
        self.api.post.return_value.json.return_value = {'success': True}
        self.ns = dict(user=self.user, limiter=self.limiter, request=request, session=session,
                       flash=flash, redirect=redirect, url_for=url_for,
                       login_credentials=login_credentials, DEBUG_FRONTEND_MODE=False,
                       peer_ip=peer_ip, login_limit_key=login_limit_key,
                       RECAPTCHA_SECRET_KEY='test', RECAPTCHA_SITE_KEY='test', requests=self.api,
                       _get_client_ip=client_ip, login=self.login,
                       render_template=lambda *a, **kw: 'Login')
        load_functions('Routes/AuthenticationHandler.py', self.ns, {'login_user'})
        self.app.register_blueprint(self.user)
        self.client = self.app.test_client()

    def test_malformed_input_is_rejected_before_captcha_and_credentials(self):
        for data in ({}, {'email': 'text', 'password': 'test'},
                     {'email': 'user@example.com', 'password': ''},
                     {'email': 'a@example.com', 'password': 'x' * 2000},
                     MultiDict([('email', 'a@example.com'), ('email', 'b@example.com'), ('password', 'x')])):
            self.assertEqual(self.client.post('/login', data=data).status_code, 400)
        self.assertEqual(self.client.post('/login', json={'email': [], 'password': {}}).status_code, 400)
        self.assertEqual(self.client.post('/login', data='x' * 17000, content_type='application/x-www-form-urlencoded').status_code, 400)
        self.assertEqual(self.client.post('/login/garbage', data={'email': 'text'}).status_code, 404)
        self.api.post.assert_not_called()
        self.login.assert_not_called()

    def test_missing_captcha_and_captcha_timeout_do_not_touch_account_database(self):
        self.assertEqual(self.client.post('/login', data={'email': 'a@example.com', 'password': 'x'}).status_code, 400)
        self.api.post.side_effect = __import__('requests').Timeout('timeout')
        self.api.RequestException = __import__('requests').RequestException
        response = self.client.post('/login', data={'email': 'a@example.com', 'password': 'x', 'g-recaptcha-response': 'token'})
        self.assertEqual(response.status_code, 503)
        self.login.assert_not_called()

    def test_cookie_and_header_rotation_cannot_reset_ip_rate_limit(self):
        for number in range(21):
            response = self.app.test_client().post('/login', data={'email': 'text', 'password': 'x'},
                headers={'X-Forwarded-For': f'198.51.100.{number}', 'CF-Connecting-IP': f'198.51.100.{number}'})
            self.assertEqual(response.status_code, 400 if number < 20 else 429)
        self.assertIn('Retry-After', response.headers)
        self.api.post.assert_not_called()
        self.login.assert_not_called()
        for _ in range(25): self.assertEqual(self.client.get('/login').status_code, 200)

    def test_existing_proxy_headers_work_without_affecting_connection_identity(self):
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '10.0.0.1'},
                headers={'X-Forwarded-For': '192.0.2.99, 198.51.100.7, 10.0.0.2', 'CF-Connecting-IP': '192.0.2.1'}):
            self.assertEqual(client_ip(), '192.0.2.1')
            self.assertEqual(peer_ip(), '10.0.0.1')
        with self.app.test_request_context('/', environ_base={'REMOTE_ADDR': '10.0.0.1'}, headers={'X-Forwarded-For': 'text'}):
            self.assertEqual(client_ip(), '10.0.0.1')

    def test_distinct_accounts_share_proxy_without_sharing_account_limit(self):
        for number in range(21):
            response = self.client.post('/login', data={'email': f'person{number}', 'password': 'x'})
            self.assertEqual(response.status_code, 400)

    def test_rotating_accounts_and_headers_still_hits_connection_limit(self):
        for number in range(301):
            response = self.client.post('/login', data={'email': f'person{number}', 'password': 'x'},
                headers={'CF-Connecting-IP': f'198.51.{number // 256}.{number % 256}'})
            self.assertEqual(response.status_code, 400 if number < 300 else 429)
        self.api.post.assert_not_called()

    def test_login_reaches_credentials_only_after_valid_fields_and_captcha(self):
        response = self.client.post('/login', data={'email': 'a@example.com', 'password': 'x',
                                                   'g-recaptcha-response': 'token'})
        self.assertEqual(response.status_code, 302)
        self.login.assert_called_once_with('a@example.com', 'x', '127.0.0.1')
        self.assertEqual(self.api.post.call_args.kwargs['timeout'], 5)


if __name__ == '__main__':
    unittest.main()
