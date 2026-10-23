"""Authentication latency regressions without network calls or a live database."""
import threading
import sys
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest
import requests
from flask import Flask, request, flash, current_app, redirect
from runtime import auth_http
from runtime.email_identity import canonical_email
from tests.test_credit_transactions import load_functions

class DependencyTests(unittest.TestCase):
    def test_connections_are_reused_per_thread_and_posts_are_not_retried(self):
        client=auth_http.AuthRequests()
        with patch.object(auth_http.requests,'Session') as factory:
            client.post('https://example.invalid/a',timeout=(2,5))
            client.get('https://example.invalid/b',timeout=(2,5))
            self.assertEqual(factory.call_count,1)
            self.assertEqual(factory.return_value.request.call_count,2)
            thread=threading.Thread(target=lambda:client.get('https://example.invalid/c'))
            thread.start();thread.join()
            self.assertEqual(factory.call_count,2)
            factory.return_value.request.side_effect=requests.Timeout()
            with self.assertRaises(requests.Timeout):client.post('https://example.invalid/a')
            self.assertEqual(factory.return_value.request.call_count,4)
    def test_captcha_tokens_are_never_cached_and_failure_stays_closed(self):
        with patch.object(auth_http,'auth_requests') as api:
            api.post.return_value.json.return_value={'success':True}
            for _ in range(2):self.assertTrue(auth_http.verify_turnstile('token','secret'))
            self.assertEqual(api.post.call_count,2)
            self.assertEqual(api.post.call_args.kwargs['timeout'],(2,5))
            api.post.return_value.json.return_value={'success':'true'}
            self.assertFalse(auth_http.verify_turnstile('token','secret'))
            api.post.return_value.json.return_value=[]
            self.assertFalse(auth_http.verify_turnstile('token','secret'))
            api.post.reset_mock()
            self.assertFalse(auth_http.verify_turnstile(None,'secret'))
            api.post.assert_not_called()
    def test_blocklist_reuses_one_download_for_concurrent_signups(self):
        entries=auth_http.BlockedEmailList()
        with patch.object(auth_http,'auth_requests') as api:
            api.get.return_value.text='blocked.example\n\nTEMP.EXAMPLE\n'
            threads=[threading.Thread(target=entries.get) for _ in range(15)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            self.assertEqual(api.get.call_count,1)
            self.assertEqual(entries.get(),('blocked.example','temp.example'))
            entries.expires=0;api.get.side_effect=requests.Timeout()
            self.assertEqual(entries.get(),('blocked.example','temp.example'))
            for _ in range(10):entries.get()
            self.assertEqual(api.get.call_count,2)
    def test_cold_blocklist_outage_does_not_repeat_wait_for_every_signup(self):
        entries=auth_http.BlockedEmailList()
        with patch.object(auth_http,'auth_requests') as api:
            api.get.side_effect=requests.Timeout()
            for _ in range(10):self.assertEqual(entries.get(),())
            api.get.assert_called_once()

class LoginTests(unittest.TestCase):
    def test_success_uses_one_activity_write_and_still_checks_password_and_suspension(self):
        account=[None]*16
        account[0]=7;account[1]='A';account[2]='member';account[7]='a@example.com';account[9]='hash';account[15]=False
        database=Mock();database.execute_query.side_effect=[None,None]
        bcrypt=Mock();bcrypt.checkpw.return_value=True
        alt=Mock(return_value={'allowed':True})
        ns=dict(find_user_by_email=Mock(return_value=account),__package__='managers',DatabaseManager=database,bcrypt=bcrypt,webhook_log=Mock(),logging=logging)
        load_functions('managers/authentication.py',ns,{'login'})
        with patch.dict(sys.modules,{'managers.alt_detection':SimpleNamespace(check_login_ip=alt)}):
            self.assertIs(ns['login']('a@example.com','password','8.8.8.8'),account)
        writes=[call for call in database.execute_query.call_args_list if call.args[0].startswith('UPDATE')]
        self.assertEqual(len(writes),1)
        self.assertIn('last_seen = NOW(), ip = %s',writes[0].args[0])
        bcrypt.checkpw.assert_called_once_with(b'password',b'hash')
        alt.assert_called_once()
        account[15]=True;database.execute_query.side_effect=[None];alt.reset_mock()
        self.assertEqual(ns['login']('a@example.com','password','8.8.8.8'),'suspended')
        alt.assert_not_called()

class RegistrationIdentityTests(unittest.TestCase):
    def test_registration_uses_database_identity_instead_of_reusing_deleted_ids(self):
        database=Mock()
        api=Mock();api.post.return_value.json.return_value={'attributes':{'id':77}}
        thread=Mock();thread.return_value.join.return_value=b'hashed'
        ns=dict(canonical_email=canonical_email,DatabaseManager=database,auth_requests=api,blocked_email_list=Mock(get=lambda:()),bcrypt=Mock(),
                ThreadWithReturnValue=thread,webhook_log=Mock(),alt_detection_enabled=lambda:False,
                requires_manual_server_approval=lambda _:False,HEADERS={},PTERODACTYL_URL='https://example.invalid/')
        load_functions('managers/authentication.py',ns,{'register'})
        self.assertEqual(ns['register']('a@example.com','password','name','8.8.8.8')['attributes']['id'],77)
        database.execute_query.assert_called_once()
        sql,values=database.execute_query.call_args.args
        self.assertIn('(name, email, password, pterodactyl_id,',sql)
        self.assertNotIn('password, id,',sql)
        self.assertEqual(values,('name','a@example.com',b'hashed',77,'8.8.8.8',5,'member'))

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.app=Flask(__name__);self.app.secret_key='test'
        self.api=Mock();self.api.get.return_value.json.return_value={'attributes':dict(username='a',email='a@example.com',first_name='A',last_name='A')}
        self.cache=Mock();self.cache.get.return_value='valid-token'
        self.database=Mock();self.bcrypt=Mock();self.bcrypt.hashpw.return_value=b'hashed'
        self.invalid=type('InvalidResetToken',(ValueError,),{})
        def apply(token,password,update_panel):
            if token != 'valid-token':raise self.invalid()
            update_panel(7,'a@example.com',password)
        self.apply=Mock(side_effect=apply)
        self.ns=dict(apply_password_reset=self.apply,InvalidResetToken=self.invalid,user=SimpleNamespace(route=lambda *a,**kw:lambda f:f),request=request,flash=flash,
                     cache=self.cache,bcrypt=self.bcrypt,get_ptero_id=lambda _: (7,),DatabaseManager=self.database,
                     auth_requests=self.api,requests=requests,HEADERS={},PTERODACTYL_URL='https://example.invalid/',
                     redirect=redirect,url_for=lambda *a,**kw:'/login',render_template=lambda *a,**kw:'Reset')
        load_functions('Routes/AuthenticationHandler.py',self.ns,{'reset_password_confirm'})
    def submit(self,token='valid-token'):
        with self.app.test_request_context('/',method='POST',data={'email':'a@example.com','password':'new-password','confirm_password':'new-password'}):
            return self.ns['reset_password_confirm'](token)
    def test_reset_checks_remote_success_before_local_update_and_consumes_token(self):
        result=self.submit()
        self.assertEqual(result.status_code,302)
        self.assertEqual(self.apply.call_args.args[:2],('valid-token','new-password'))
        self.assertEqual(self.api.patch.call_args.kwargs['timeout'],(3,10))
    def test_remote_failure_keeps_local_password_and_reset_token(self):
        self.api.patch.return_value.raise_for_status.side_effect=requests.HTTPError()
        self.assertEqual(self.submit()[1],503)
        self.database.execute_query.assert_not_called();self.cache.delete.assert_not_called()
    def test_invalid_token_never_hashes_or_contacts_panel(self):
        self.submit('invalid')
        self.bcrypt.hashpw.assert_not_called();self.api.get.assert_not_called();self.api.patch.assert_not_called()
    def test_captcha_outage_cannot_queue_reset_email_or_create_account(self):
        for name in ('reset_password','register_user'):
            ns=dict(self.ns,current_app=current_app,verify_turnstile=Mock(side_effect=requests.Timeout()),
                    RECAPTCHA_SECRET_KEY='test',register=Mock(),generate_reset_token=Mock())
            load_functions('Routes/AuthenticationHandler.py',ns,{name})
            with self.app.test_request_context('/',method='POST',data={'g-recaptcha-response':'token'}):
                self.assertEqual(ns[name]()[1],503)
            ns['register'].assert_not_called();ns['generate_reset_token'].assert_not_called()

if __name__=='__main__':unittest.main()
