"""Account-bound verification, replay protection and session-switch regressions."""
import hashlib
import hmac
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import wraps
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests
from flask import Flask, Blueprint, request, session, flash, current_app, redirect, url_for
from runtime.email_identity import canonical_email
from tests.test_credit_transactions import load_functions


class VerificationDatabase:
    def __init__(self):
        self.lock=threading.RLock();self.fail_mark=False;self.queries=[]
        self.connection=sqlite3.connect(':memory:',check_same_thread=False,isolation_level=None)
        self.connection.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT,email_verified_at TEXT);
            INSERT INTO users VALUES(1,'user@example.com',NULL);
            INSERT INTO users VALUES(2,'verified@example.com','2026-01-01');
            CREATE TABLE email_verification_tokens(token_hash TEXT PRIMARY KEY,user_id INTEGER,
                email_snapshot TEXT,expires_at TEXT,used_at TEXT);
        ''')
    @contextmanager
    def transaction(self):
        with self.lock:
            raw=self.connection.cursor();self.connection.execute('BEGIN')
            def execute(sql,values):
                self.queries.append((sql,values))
                if self.fail_mark and sql.startswith('UPDATE email_verification_tokens'):
                    raise RuntimeError('Token write failed')
                sql=sql.replace('%s','?').replace(' FOR UPDATE','')
                sql=sql.replace('DATE_ADD(UTC_TIMESTAMP(), INTERVAL ? SECOND)',"datetime('now', '+' || ? || ' seconds')")
                return raw.execute(sql.replace('UTC_TIMESTAMP()',"datetime('now')"),values)
            try:
                yield SimpleNamespace(execute=execute,fetchone=raw.fetchone)
                self.connection.commit()
            except BaseException:
                self.connection.rollback();raise
            finally:raw.close()


class VerificationFixture(unittest.TestCase):
    def setUp(self):
        self.db=VerificationDatabase();self.addCleanup(self.db.connection.close)
        self.invalid=type('InvalidVerificationToken',(ValueError,),{})
        self.ns=load_functions('managers/email_verification.py',dict(DatabaseManager=self.db,hashlib=hashlib,
            hmac=hmac,secrets=secrets,canonical_email=canonical_email,InvalidVerificationToken=self.invalid))
        self.ns['ensure_verification_schema']=lambda:None
    def verified(self,user_id=1):
        return self.db.connection.execute('SELECT email_verified_at FROM users WHERE id=?',(user_id,)).fetchone()[0]


class VerificationTokenTests(VerificationFixture):
    def test_issued_token_is_hashed_and_bound_to_id_and_registered_email(self):
        token,email=self.ns['issue_email_verification'](1)
        row=self.db.connection.execute('SELECT token_hash,user_id,email_snapshot FROM email_verification_tokens').fetchone()
        self.assertEqual(email,'user@example.com')
        self.assertNotEqual(row[0],token)
        self.assertEqual(row,(hashlib.sha256(token.encode()).hexdigest(),1,email))

    def test_other_verified_account_cannot_use_or_consume_token(self):
        token,_=self.ns['issue_email_verification'](1)
        with self.assertRaises(PermissionError):self.ns['validate_email_verification'](token,2,consume=True)
        self.assertIsNone(self.verified())
        self.assertEqual(self.verified(2),'2026-01-01')
        self.ns['validate_email_verification'](token,1,consume=True)
        self.assertIsNotNone(self.verified())
        self.assertTrue(all('WHERE email' not in query for query,_ in self.db.queries))

    def test_get_does_not_consume_and_post_is_single_use(self):
        token,_=self.ns['issue_email_verification'](1)
        self.ns['validate_email_verification'](token,1)
        self.assertIsNone(self.verified())
        self.ns['validate_email_verification'](token,1,consume=True)
        with self.assertRaises(self.invalid):self.ns['validate_email_verification'](token,1,consume=True)

    def test_resend_invalidates_old_token_and_verified_accounts_get_none(self):
        first,_=self.ns['issue_email_verification'](1)
        second,_=self.ns['issue_email_verification'](1)
        with self.assertRaises(self.invalid):self.ns['validate_email_verification'](first,1,consume=True)
        self.ns['validate_email_verification'](second,1,consume=True)
        self.assertIsNone(self.ns['issue_email_verification'](1))
        self.assertIsNone(self.ns['issue_email_verification'](2))

    def test_changed_email_including_accent_variants_invalidates_link(self):
        token,_=self.ns['issue_email_verification'](1)
        self.db.connection.execute("UPDATE users SET email='user@examplé.com' WHERE id=1")
        with self.assertRaises(self.invalid):self.ns['validate_email_verification'](token,1,consume=True)
        self.assertIsNone(self.verified())

    def test_expired_unknown_and_malformed_tokens_never_verify(self):
        token,_=self.ns['issue_email_verification'](1)
        self.db.connection.execute("UPDATE email_verification_tokens SET expires_at='2000-01-01'")
        for value in (token,'Z'*64,'bad',None,'\u00e9'*64):
            with self.subTest(value=value),self.assertRaises(self.invalid):
                self.ns['validate_email_verification'](value,1,consume=True)
        self.assertIsNone(self.verified())

    def test_concurrent_redemptions_commit_once(self):
        token,_=self.ns['issue_email_verification'](1)
        def submit(_):
            try:self.ns['validate_email_verification'](token,1,consume=True);return True
            except self.invalid:return False
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(submit,range(8)))
        self.assertEqual(sum(results),1)

    def test_failed_token_consumption_rolls_back_account_verification(self):
        token,_=self.ns['issue_email_verification'](1)
        self.db.fail_mark=True
        with self.assertRaises(RuntimeError):self.ns['validate_email_verification'](token,1,consume=True)
        self.assertIsNone(self.verified())
        self.db.fail_mark=False
        self.ns['validate_email_verification'](token,1,consume=True)
        self.assertIsNotNone(self.verified())


class VerificationRouteTests(VerificationFixture):
    def setUp(self):
        super().setUp()
        self.app=Flask(__name__);self.app.secret_key='test'
        self.captcha=Mock(return_value=True);self.thread=Mock()
        self.ns.update(user=Blueprint('user',__name__),wraps=wraps,
            session_is_valid=lambda:'account_id' in session,update_last_seen=Mock(),
            request=request,session=session,flash=flash,current_app=current_app,
            redirect=redirect,url_for=url_for,render_template=lambda *a,**kw:'Verification form',
            verify_turnstile=self.captcha,requests=requests,RECAPTCHA_SITE_KEY='site',RECAPTCHA_SECRET_KEY='secret',
            TOKEN_EXPIRATION_TIME=1800,threading=SimpleNamespace(Thread=self.thread),send_verification_email=Mock())
        self.ns['user'].add_url_rule('/',endpoint='index',view_func=lambda:'Dashboard')
        self.ns['user'].add_url_rule('/login',endpoint='login_user',view_func=lambda:'Login')
        load_functions('managers/authentication.py',self.ns,{'login_required'})
        load_functions('Routes/AuthenticationHandler.py',self.ns,{'verify_email','resend_confirmation_email'})
        self.app.register_blueprint(self.ns['user']);self.client=self.app.test_client()
        self.token,_=self.ns['issue_email_verification'](1)
        self.login(1)
    def login(self,user_id):
        with self.client.session_transaction() as state:
            state.update(account_id=user_id,email='untrusted-stale-session@example.com')
    def submit(self):return self.client.post('/verify_email/'+self.token,data={'g-recaptcha-response':'captcha','email':'verified@example.com','user_id':'2'})

    def test_wrong_account_is_rejected_on_both_get_and_post_without_captcha(self):
        self.login(2)
        self.assertEqual(self.client.get('/verify_email/'+self.token).status_code,403)
        self.assertEqual(self.submit().status_code,403)
        self.captcha.assert_not_called();self.assertIsNone(self.verified())
        self.login(1)
        self.assertEqual(self.submit().status_code,302)
        self.assertIsNotNone(self.verified())

    def test_logged_out_user_is_sent_to_login_with_verification_path_preserved(self):
        with self.client.session_transaction() as state:state.clear()
        result=self.client.get('/verify_email/'+self.token)
        self.assertEqual(result.status_code,302)
        self.assertIn('/login?next=/verify_email/'+self.token,result.location)
        self.assertIsNone(self.verified())

    def test_switching_accounts_after_opening_form_cannot_verify(self):
        self.assertEqual(self.client.get('/verify_email/'+self.token).status_code,200)
        self.login(2)
        self.assertEqual(self.submit().status_code,403)
        self.assertIsNone(self.verified())

    def test_get_is_read_only_and_forged_form_target_is_ignored(self):
        self.assertEqual(self.client.get('/verify_email/'+self.token).status_code,200)
        self.assertIsNone(self.verified())
        self.assertEqual(self.submit().location,'/')
        self.assertIsNotNone(self.verified());self.assertEqual(self.verified(2),'2026-01-01')
        self.assertEqual(self.submit().status_code,400)

    def test_failed_captcha_and_outage_keep_token_retryable(self):
        self.captcha.return_value=False
        self.assertEqual(self.submit().status_code,400)
        self.captcha.side_effect=requests.Timeout()
        self.assertEqual(self.submit().status_code,503)
        self.assertIsNone(self.verified())
        self.captcha.side_effect=None;self.captcha.return_value=True
        self.assertEqual(self.submit().status_code,302)

    def test_resend_uses_database_mailbox_not_session_and_skips_verified_user(self):
        self.assertEqual(self.client.get('/resend_confirmation_email').status_code,302)
        self.assertEqual(self.thread.call_args.kwargs['args'][0],'user@example.com')
        self.thread.reset_mock();self.login(2)
        self.assertEqual(self.client.get('/resend_confirmation_email').status_code,302)
        self.thread.assert_not_called()


if __name__=='__main__':unittest.main()
