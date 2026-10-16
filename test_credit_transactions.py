"""Exercise production credit functions against a transactional SQLite adapter.

SQLite BEGIN IMMEDIATE emulates account serialization; these tests verify
transaction boundaries and failures, not MySQL's row-lock implementation.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from mysql.connector import IntegrityError
from flask import Flask, Blueprint, request, session, flash, redirect, url_for

ROOT = Path(__file__).resolve().parent


def load_functions(path, namespace, names=None):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and (names is None or n.name in names)]
    exec(compile(tree, path, "exec"), namespace)
    return namespace


class CreditTransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'credits.sqlite')
        self.fail_role = False
        self.engines = [('users', 'InnoDB'), ('ledger', 'InnoDB')]
        self.sql = []
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, credits REAL, role TEXT);
                INSERT INTO users VALUES (1, 'user@example.com', 10, 'member');
                CREATE TABLE stripe_processed_checkouts (pay_id TEXT PRIMARY KEY);
                CREATE TABLE stripe_processed_invoices (invoice_id TEXT PRIMARY KEY);
            """)
        owner = self

        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def execute(self, sql, params):
                owner.sql.append(sql)
                if owner.fail_role and "SET role" in sql:
                    raise RuntimeError('Injected role-write failure')
                try:
                    return self.cursor.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), params)
                except sqlite3.IntegrityError as exc:
                    raise IntegrityError(str(exc), errno=1062) from exc

            def fetchone(self):
                return self.cursor.fetchone()

        class Database:
            @staticmethod
            @contextmanager
            def transaction():
                db = sqlite3.connect(owner.path, timeout=15)
                try:
                    db.execute('BEGIN IMMEDIATE')
                    yield Cursor(db.cursor())
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
                finally:
                    db.close()

            @staticmethod
            def execute_query(sql, *args, **kwargs):
                if sql.startswith('SELECT TABLE_NAME'):
                    return owner.engines
                if not sql.startswith('CREATE TABLE IF NOT EXISTS'):
                    raise AssertionError(sql)

        self.ns = load_functions('managers/credit_manager.py', {
            'DatabaseManager': Database, 'webhook_log': Mock(),
        }, {'_credit_amount', '_add_credits_in_transaction', 'add_credits', 'remove_credits'})
        load_functions('managers/payment_manager.py', self.ns)
        self.ns['IntegrityError'] = IntegrityError

    def scalar(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchone()[0]

    def balance(self):
        return self.scalar('SELECT credits FROM users')

    def test_concurrent_debits_cannot_spend_balance_twice(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.ns['remove_credits']('user@example.com', 7), range(2)))
        self.assertEqual(results.count(None), 1)
        self.assertEqual(results.count('SUSPEND'), 1)
        self.assertEqual(self.balance(), 3)
        self.assertTrue(any('SELECT credits' in sql and 'FOR UPDATE' in sql for sql in self.sql))

    def test_concurrent_additions_and_debits_do_not_overwrite_each_other(self):
        def change(i):
            if i % 2:
                self.ns['add_credits']('user@example.com', 2, False)
            else:
                self.ns['remove_credits']('user@example.com', 1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(change, range(16)))
        self.assertEqual(self.balance(), 18)

    def test_scheduled_billing_debits_live_balance_and_suspends_unaffordable_server(self):
        servers = [{'attributes': {'id': i, 'user': 7, 'name': f'Server {i}', 'suspended': False}}
                   for i in (1, 2)]
        api = Mock()
        api.get.side_effect = [
            Mock(status_code=200, json=lambda: {'data': servers}),
            Mock(status_code=200, json=lambda: {'attributes': {'email': 'user@example.com'}}),
        ]
        threads = Mock()
        self.ns.update(
            safe_requests=api, PTERODACTYL_URL='https://example.invalid/', HEADERS={},
            convert_to_product=lambda _: {'price': 5040},
            ThreadWithReturnValue=Mock(), threading=threads,
            send_email=Mock(), suspend_server=Mock(), current_app=Mock(),
        )
        load_functions('managers/credit_manager.py', self.ns, {'use_credits'})
        self.ns['use_credits']()
        self.assertEqual(self.balance(), 3)
        threads.Thread.assert_called_once_with(target=self.ns['suspend_server'],
            args=(2, "Insufficient credits to pay for this server.", "billing"))

    def test_concurrent_checkout_and_invoice_replays_credit_once(self):
        for kind, table in [('checkout', 'stripe_processed_checkouts'), ('invoice', 'stripe_processed_invoices')]:
            with self.subTest(kind=kind):
                before = self.balance()
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda _: self.ns['credit_payment'](
                        'user@example.com', 25, 'same-payment', kind), range(8)))
                self.assertEqual(results.count(True), 1)
                self.assertEqual(results.count(False), 7)
                self.assertEqual(self.balance(), before + 25)
                self.assertEqual(self.scalar(f'SELECT COUNT(*) FROM {table}'), 1)

    def test_failure_after_balance_update_rolls_back_marker_balance_and_role(self):
        for kind, table in [('checkout', 'stripe_processed_checkouts'), ('invoice', 'stripe_processed_invoices')]:
            with self.subTest(kind=kind):
                before = self.balance()
                self.fail_role = True
                with self.assertRaisesRegex(RuntimeError, 'Injected'):
                    self.ns['credit_payment']('user@example.com', 25, 'retry-me', kind)
                self.assertEqual(self.balance(), before)
                self.assertEqual(self.scalar(f'SELECT COUNT(*) FROM {table}'), 0)
                self.fail_role = False
                self.assertTrue(self.ns['credit_payment']('user@example.com', 25, 'retry-me', kind))
                self.assertEqual(self.balance(), before + 25)
                self.assertEqual(self.scalar('SELECT role FROM users'), 'client')

    def test_missing_recipient_leaves_payment_retryable(self):
        with self.assertRaises(ValueError):
            self.ns['credit_payment']('missing@example.com', 25, 'missing-user', 'checkout')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM stripe_processed_checkouts'), 0)
        self.assertEqual(self.balance(), 10)

    def test_invalid_amounts_and_missing_account_fail_closed(self):
        for amount in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.ns['remove_credits']('user@example.com', amount)
            with self.assertRaises(ValueError):
                self.ns['add_credits']('user@example.com', amount)
        self.assertEqual(self.ns['remove_credits']('missing@example.com', 1), 'SUSPEND')
        self.assertIsNone(self.ns['remove_credits']('user@example.com', 0))
        self.assertEqual(self.balance(), 10)

    def test_nontransactional_tables_are_rejected(self):
        self.engines = [('users', 'InnoDB'), ('ledger', 'MyISAM')]
        with self.assertRaisesRegex(RuntimeError, 'InnoDB'):
            self.ns['credit_payment']('user@example.com', 25, 'not-safe', 'checkout')
        self.assertEqual(self.balance(), 10)

    def store_client(self):
        app = Flask(__name__)
        app.secret_key = 'test'
        app.testing = True
        user = Blueprint('user', __name__)
        user.add_url_rule('/', endpoint='index', view_func=lambda: 'Dashboard')
        app.register_blueprint(user)
        stripe = Mock()
        checkout = {'id': 'checkout-1', 'mode': 'payment', 'payment_status': 'paid',
                    'customer_email': 'user@example.com',
                    'line_items': {'data': [{'price': {'id': 'price-1'}}]}}
        stripe.checkout.Session.retrieve.return_value = checkout
        stripe.Webhook.construct_event.return_value = {
            'type': 'checkout.session.completed', 'data': {'object': checkout}}
        self.ns.update(store=Blueprint('store', __name__), login_required=lambda f: f,
                       request=request, session=session, flash=flash, redirect=redirect,
                       url_for=url_for, stripe=stripe, STRIPE_WEBHOOK_SECRET='test',
                       products=[{'price_link': 'price-1', 'price': 25}],
                       SUBSCRIPTION_CREDIT_PRICES={})
        load_functions('Routes/Store.py', self.ns, {'success', 'stripe_webhook'})
        app.register_blueprint(self.ns['store'], url_prefix='/store')
        client = app.test_client()
        with client.session_transaction() as state:
            state['email'] = 'user@example.com'
            state['pay_id'] = 'checkout-1'
        return client, stripe

    def test_success_page_and_webhook_share_one_committed_credit(self):
        client, _ = self.store_client()
        self.assertEqual(client.post('/store/stripe_webhook').status_code, 200)
        self.assertEqual(client.get('/store/success').status_code, 302)
        self.assertEqual(self.balance(), 35)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM stripe_processed_checkouts'), 1)

    def test_page_and_webhook_failures_leave_checkout_retryable(self):
        client, _ = self.store_client()
        self.fail_role = True
        self.assertEqual(client.get('/store/success').status_code, 503)
        with client.session_transaction() as state:
            self.assertEqual(state['pay_id'], 'checkout-1')
        self.assertEqual(client.post('/store/stripe_webhook').status_code, 500)
        self.assertEqual(self.balance(), 10)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM stripe_processed_checkouts'), 0)
        self.fail_role = False
        self.assertEqual(client.get('/store/success').status_code, 302)
        self.assertEqual(self.balance(), 35)

    def test_invoice_webhook_replay_and_failure_retry(self):
        client, stripe = self.store_client()
        stripe.Webhook.construct_event.return_value = {
            'type': 'invoice.payment_succeeded', 'data': {'object': {'id': 'invoice-1'}}}
        stripe.Invoice.retrieve.return_value = {'subscription': 'subscription-1'}
        stripe.Subscription.retrieve.return_value = {
            'metadata': {'user_email': 'user@example.com', 'credits_per_month': '25'}}
        self.fail_role = True
        self.assertEqual(client.post('/store/stripe_webhook').status_code, 500)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM stripe_processed_invoices'), 0)
        self.fail_role = False
        self.assertEqual(client.post('/store/stripe_webhook').status_code, 200)
        self.assertEqual(client.post('/store/stripe_webhook').status_code, 200)
        self.assertEqual(self.balance(), 35)


class TransactionContextTests(unittest.TestCase):
    def test_commit_failure_rolls_back_and_closes_resources(self):
        tree = ast.parse((ROOT / 'managers/database_manager.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        cls.bases = []
        cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'transaction']
        ns = {'contextmanager': contextmanager}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'database_manager.py', 'exec'), ns)
        connection, cursor = Mock(), Mock()
        connection.commit.side_effect = RuntimeError('Commit failed')
        ns['DatabaseManager'].get_connection = Mock(return_value=(connection, cursor))
        with self.assertRaisesRegex(RuntimeError, 'Commit failed'):
            with ns['DatabaseManager'].transaction() as value:
                self.assertIs(value, cursor)
        connection.rollback.assert_called_once()
        cursor.close.assert_called_once()
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
