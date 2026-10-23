"""Email and billing regressions without live SMTP, Stripe or a database."""
import copy
import hashlib
import hmac
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from functools import wraps
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from flask import Flask, flash, redirect, session, url_for, request, current_app
from runtime.email_identity import canonical_email, email_lookup_candidates
from tests.test_credit_transactions import load_functions


class EmailIdentityTests(TestCase):
    def test_real_query_with_accent_insensitive_collation_rejects_variant(self):
        connection=sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        def fold(value):
            return ''.join(c for c in unicodedata.normalize('NFD',value.lower()) if not unicodedata.combining(c))
        connection.create_collation('accent_insensitive',lambda a,b:(fold(a)>fold(b))-(fold(a)<fold(b)))
        columns=['id INTEGER']+[f'c{i} TEXT' for i in range(1,7)]+['email TEXT COLLATE accent_insensitive']+[f'c{i} TEXT' for i in range(8,16)]
        connection.execute('CREATE TABLE users ('+','.join(columns)+')')
        connection.execute('INSERT INTO users (id,email) VALUES (?,?)',(1,'victim@example.com'))
        self.assertEqual(connection.execute('SELECT id FROM users WHERE email=?',('victim@examplé.com',)).fetchone(),(1,))
        def query(sql,values,fetch_all=False):return connection.execute(sql.replace('%s','?'),values).fetchall()
        ns=load_functions('managers/email_identity.py',dict(canonical_email=canonical_email,
            email_lookup_candidates=email_lookup_candidates,DatabaseManager=SimpleNamespace(execute_query=query)))
        self.assertIsNone(ns['find_user_by_email']('victim@examplé.com'))
        self.assertEqual(ns['find_user_by_email']('victim@example.com')[0],1)

    def test_idn_canonicalization_does_not_remove_accents(self):
        self.assertEqual(canonical_email(' Victim@EXAMPLE.com '), 'victim@example.com')
        self.assertEqual(canonical_email('victim@examplé.com'), 'victim@xn--exampl-gva.com')
        self.assertNotEqual(canonical_email('victim@gmáil.com'), canonical_email('victim@gmail.com'))
        self.assertNotEqual(canonical_email('victim@faß.de'), canonical_email('victim@fass.de'))
        for value in ('', None, 'a@b@c.com', 'a\n@example.com', 'a@localhost', 'a@example.com.'):
            with self.subTest(value=value), self.assertRaises(ValueError):canonical_email(value)

    def test_accent_insensitive_database_candidates_cannot_select_victim(self):
        victim = [None] * 16
        victim[0], victim[7] = 1, 'victim@example.com'
        # Model the dangerous collation by returning the victim for every input.
        database = Mock();database.execute_query.return_value = [victim]
        ns = load_functions('managers/email_identity.py', dict(
            canonical_email=canonical_email, email_lookup_candidates=email_lookup_candidates,
            DatabaseManager=database))
        self.assertIsNone(ns['find_user_by_email']('victim@examplé.com'))
        self.assertIsNone(ns['find_user_by_email']('víctim@example.com'))
        self.assertIs(ns['find_user_by_email']('Victim@EXAMPLE.COM'), victim)
        victim[7] = 'victim@examplé.com'
        self.assertIs(ns['find_user_by_email']('victim@xn--exampl-gva.com'), victim)
        database.execute_query.return_value = [victim, victim]
        self.assertIsNone(ns['find_user_by_email'](victim[7]))

    def test_reset_mail_uses_registered_address_and_unknown_mailboxes_get_no_token(self):
        app = Flask(__name__);app.secret_key = 'test'
        account = [None] * 16;account[0], account[7] = 1, 'Registered@example.com'
        lookup = Mock(return_value=account);issue = Mock(return_value='token');thread = Mock()
        ns = dict(user=SimpleNamespace(route=lambda *a, **k:lambda f:f), request=request,
                  current_app=current_app, flash=flash, redirect=redirect, url_for=lambda *a:'/login',
                  verify_turnstile=lambda *a:True, RECAPTCHA_SECRET_KEY='secret',
                  find_user_by_email=lookup, issue_password_reset=issue, TOKEN_EXPIRATION_TIME=1800,
                  threading=SimpleNamespace(Thread=thread), send_reset_email=Mock())
        load_functions('Routes/AuthenticationHandler.py', ns, {'reset_password'})
        with app.test_request_context('/', method='POST', data={'email':'registered@EXAMPLE.com'}):
            self.assertEqual(ns['reset_password']().status_code, 302)
        self.assertEqual(thread.call_args.kwargs['args'][0], account[7])
        lookup.return_value = None;issue.reset_mock();thread.reset_mock()
        with app.test_request_context('/', method='POST', data={'email':'registered@examplé.com'}):
            self.assertEqual(ns['reset_password']().status_code, 302)
        issue.assert_not_called();thread.assert_not_called()


class ResetTokenTests(TestCase):
    def setUp(self):
        self.invalid = type('InvalidResetToken', (ValueError,), {})
        self.state = dict(user=[1,'victim@example.com','old-hash',77], token=None, used=False, expired=False)
        lock = threading.RLock()
        outer = self
        class Cursor:
            def execute(self, sql, values):
                self.sql = sql;outer.queries.append((sql,values))
                if sql.startswith('UPDATE users'):
                    outer.assertIn('WHERE id=%s',sql)
                    outer.assertEqual(values[1],1)
                    outer.state['user'][2] = values[0]
                if sql.startswith('UPDATE password_reset_tokens'):outer.state['used'] = True
            def fetchone(self):
                user=outer.state['user']
                if outer.state['used'] or outer.state['expired'] or outer.queries[-1][1][0] != outer.state['token']:
                    return None
                return (user[0],outer.stamp,user[1],user[2],user[3])
        @contextmanager
        def transaction():
            with lock:
                before=copy.deepcopy(self.state)
                try:yield Cursor()
                except BaseException:self.state=before;raise
        self.queries=[]
        database=SimpleNamespace(transaction=transaction)
        bcrypt=Mock();bcrypt.hashpw.return_value=b'new-hash'
        self.ns=load_functions('managers/password_resets.py',dict(
            hashlib=hashlib,hmac=hmac,DatabaseManager=database,bcrypt=bcrypt,
            InvalidResetToken=self.invalid), {'_identity_stamp','_token_hash','apply_password_reset'})
        self.ns['ensure_reset_schema']=Mock()
        self.token='A'*64;self.state['token']=self.ns['_token_hash'](self.token)
        self.stamp=self.ns['_identity_stamp'](1,'victim@example.com','old-hash')

    def test_reset_is_by_id_and_token_is_single_use(self):
        update=Mock()
        self.ns['apply_password_reset'](self.token,'new-password',update)
        update.assert_called_once_with(77,'victim@example.com','new-password')
        self.assertEqual(self.state['user'][2],'new-hash')
        with self.assertRaises(self.invalid):self.ns['apply_password_reset'](self.token,'other',update)
        self.assertEqual(update.call_count,1)

    def test_remote_failure_rolls_back_and_preserves_retry(self):
        update=Mock(side_effect=OSError('offline'))
        with self.assertRaises(OSError):self.ns['apply_password_reset'](self.token,'new',update)
        self.assertFalse(self.state['used']);self.assertEqual(self.state['user'][2],'old-hash')
        update.side_effect=None
        self.ns['apply_password_reset'](self.token,'new',update)
        self.assertTrue(self.state['used'])

    def test_expired_and_changed_account_identity_are_rejected(self):
        update=Mock()
        self.state['expired']=True
        with self.assertRaises(self.invalid):self.ns['apply_password_reset'](self.token,'new',update)
        self.state['expired']=False;self.state['user'][1]='changed@example.com'
        with self.assertRaises(self.invalid):self.ns['apply_password_reset'](self.token,'new',update)
        update.assert_not_called()

    def test_concurrent_submission_calls_remote_once(self):
        update=Mock();outcomes=[]
        def submit():
            try:self.ns['apply_password_reset'](self.token,'new',update);outcomes.append('ok')
            except self.invalid:outcomes.append('used')
        threads=[threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(outcomes.count('ok'),1);update.assert_called_once()


class BillingIdentityTests(TestCase):
    def setUp(self):
        self.database=Mock();self.stripe=Mock()
        self.cursor=Mock()
        @contextmanager
        def transaction():yield self.cursor
        self.database.transaction=transaction
        self.ns=load_functions('managers/billing_identity.py',dict(DatabaseManager=self.database,stripe=self.stripe,
            wraps=wraps,session=session,flash=flash,redirect=redirect,url_for=lambda *a:'/',
            _schema_ready=True,HOSTED_URL='https://panel.example.com',hashlib=hashlib,
            canonical_email=canonical_email), {'verified_billing_required','get_stripe_customer'})
        self.ns['ensure_billing_identity_schema']=Mock()
        self.stripe.Subscription.search.return_value.auto_paging_iter.return_value=[]

    def test_unverified_user_cannot_call_billing_view(self):
        app=Flask(__name__);app.secret_key='test'
        view=Mock(return_value='ok');wrapped=self.ns['verified_billing_required'](view)
        with app.test_request_context('/'):
            session['account_id']=7
            for row in (None,(None,)):
                self.database.execute_query.return_value=row
                self.assertEqual(wrapped().status_code,302)
            view.assert_not_called()
            self.database.execute_query.return_value=('verified',)
            self.assertEqual(wrapped(),'ok')

    def test_every_user_billing_route_is_guarded_and_webhook_stays_available(self):
        import ast
        from tests.test_credit_transactions import ROOT
        tree=ast.parse((ROOT/'Routes/Store.py').read_text())
        functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
        for name in ('storepage','create_subscription_checkout_session','billing_portal','create_checkout_session','success','cancel'):
            decorators={node.id for node in functions[name].decorator_list if isinstance(node,ast.Name)}
            self.assertIn('verified_billing_required',decorators,name)
            self.assertIn('login_required',decorators,name)
        decorators={node.id for node in functions['stripe_webhook'].decorator_list if isinstance(node,ast.Name)}
        self.assertNotIn('verified_billing_required',decorators)
        self.assertNotIn('login_required',decorators)

    def test_saved_customer_is_used_without_email_lookup(self):
        self.cursor.fetchone.return_value=('user@example.com','verified',False,'cus_saved')
        self.assertEqual(self.ns['get_stripe_customer'](7),'cus_saved')
        self.stripe.Customer.list.assert_not_called();self.stripe.Customer.create.assert_not_called()
        self.stripe.Subscription.search.assert_not_called()

    def test_new_customer_is_bound_by_user_id_with_idempotency(self):
        self.cursor.fetchone.return_value=('user@example.com','verified',False,None)
        self.stripe.Customer.create.return_value={'id':'cus_new'}
        self.assertEqual(self.ns['get_stripe_customer'](7),'cus_new')
        key=hashlib.sha256(b'https://panel.example.com').hexdigest()[:32]
        self.stripe.Customer.create.assert_called_once_with(email='user@example.com',metadata={'panel_user_id':'7','panel_origin':'https://panel.example.com'},idempotency_key=f'panel-customer-{key}-7')
        self.assertEqual(self.cursor.execute.call_args.args[1],('cus_new',7))
        self.stripe.Customer.list.assert_not_called()

    def test_legacy_subscription_migrates_using_account_metadata(self):
        self.cursor.fetchone.return_value=('user@example.com','verified',False,None)
        self.stripe.Subscription.search.return_value.auto_paging_iter.return_value=[dict(customer='cus_old',metadata={'user_id':'7','user_email':'user@example.com'})]
        self.assertEqual(self.ns['get_stripe_customer'](7),'cus_old')
        self.stripe.Customer.create.assert_not_called();self.stripe.Customer.list.assert_not_called()

    def test_wrong_owner_or_unverified_customer_creation_is_rejected(self):
        for row in (None,('user@example.com',None,False,None),('user@example.com','verified',True,None)):
            self.cursor.fetchone.return_value=row
            with self.assertRaises(ValueError):self.ns['get_stripe_customer'](7)
        self.stripe.Customer.create.assert_not_called()
        self.cursor.fetchone.return_value=('user@example.com','verified',False,None)
        self.stripe.Subscription.search.return_value.auto_paging_iter.return_value=[dict(customer='cus_other',metadata={'user_id':'8'})]
        with self.assertRaises(ValueError):self.ns['get_stripe_customer'](7)
        self.stripe.Customer.create.assert_not_called()
