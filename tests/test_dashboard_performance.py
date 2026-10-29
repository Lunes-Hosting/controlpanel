"""Regression coverage for automatic asset delivery and dashboard isolation."""
import gzip
from collections import OrderedDict
from threading import Lock
from runtime.display_cache import DisplayCache
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from flask import Flask, Blueprint, session, request, jsonify, render_template
from hashlib import sha256
from runtime.web_performance import install_performance, asset_version
from runtime.stats_snapshot import StatsSnapshot
from tests.test_credit_transactions import ROOT, load_functions


class DashboardTemplatePaginationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(ROOT / 'templates'))
        self.app.secret_key = 'test'
        self.app.testing = True
        self.app.url_build_error_handlers.append(lambda error, endpoint, values: '/unused/' + endpoint)
        self.state = dict(data=dict(server_count=45, monthly_usage=100,
            servers=[{'attributes': dict(id=i, name=f'Server {i}', identifier=str(i),
                limits={'memory':512,'disk':3000,'cpu':100}, feature_limits={})} for i in range(1,46)]),
            refreshing=False, error=False)
        ns = dict(user=Blueprint('user', __name__), login_required=lambda f:f,
            request=request, session=session, render_template=render_template, jsonify=jsonify,
            account_get_information=lambda _: (100, 7, 'User', True, False),
            dashboard_inventory=lambda *a:self.state, products=[], SUBSCRIPTION_CREDIT_PRICES={}, sha256=sha256)
        load_functions('Routes/AuthenticationHandler.py', ns, {'index','dashboard_server_page','dashboard_servers_data'})
        self.app.register_blueprint(ns['user'])
        self.app.add_url_rule('/servers/<int:server_id>', endpoint='servers.server', view_func=lambda server_id:'Server')
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state.update(email='user@example.com', account_id=1)

    def test_page_two_renders_actual_template_and_correct_inventory_url(self):
        result = self.client.get('/?server_page=2')
        self.assertEqual(result.status_code,200)
        self.assertIn('data-dashboard-inventory-url="/dashboard/servers?server_page=2"',result.text)
        self.assertIn('href="/servers/21"',result.text)
        self.assertNotIn('href="/servers/1"',result.text)
        self.assertIn('Page 2 of 3',result.text)

    def test_default_last_and_invalid_pages_render(self):
        for query, expected in (('',1),('?server_page=3',3),('?server_page=invalid',1),
                                ('?server_page=-1',1),('?server_page=0',1),('?server_page=999',3)):
            with self.subTest(query=query):
                result=self.client.get('/'+query)
                self.assertEqual(result.status_code,200)
                self.assertIn(f'Page {expected} of 3',result.text)
                self.assertIn(f'data-dashboard-inventory-url="/dashboard/servers?server_page={expected}"',result.text)

    def test_cold_inventory_preserves_page_two_for_background_load(self):
        self.state.update(data=None,refreshing=True)
        result=self.client.get('/?server_page=2')
        self.assertEqual(result.status_code,200)
        self.assertIn('data-dashboard-inventory-url="/dashboard/servers?server_page=2"',result.text)

    def test_background_response_renders_page_two_with_previous_and_next(self):
        result=self.client.get('/dashboard/servers?server_page=2',headers={'X-Dashboard-Account':'1'})
        self.assertEqual(result.status_code,200)
        self.assertIn('Page 2 of 3',result.json['html'])
        self.assertIn('/?server_page=1#servers-heading',result.json['html'])
        self.assertIn('/?server_page=3#servers-heading',result.json['html'])

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'app.css'
        self.source.write_bytes(b'body{color:red}' * 200)
        self.packed = self.root / 'app.css.gz'
        self.packed.write_bytes(gzip.compress(self.source.read_bytes()))
        self.app = Flask(__name__, static_folder=str(self.root), static_url_path='/static')
        self.app.secret_key = 'test'
        install_performance(self.app)
        @self.app.route('/')
        def home():
            session['email'] = 'a@example.com'
            return '<p>Example</p>' * 200
        self.client = self.app.test_client()
    def tearDown(self): self.temp.cleanup()
    def test_gzip_and_versioned_cache_and_conditional_request(self):
        url = '/static/app.css?v=' + asset_version(self.app, 'app.css')
        response = self.client.get(url, headers={'Accept-Encoding':'gzip'},buffered=True)
        self.assertEqual(gzip.decompress(response.data), self.source.read_bytes())
        self.assertIn('immutable', response.headers['Cache-Control'])
        response2 = self.client.get(url, headers={'Accept-Encoding':'gzip', 'If-None-Match':response.headers['ETag']})
        self.assertEqual(response2.status_code,304)
        response2.close()
        identity = self.client.get(url, headers={'Accept-Encoding':'gzip;q=0'},buffered=True)
        self.assertNotIn('Content-Encoding', identity.headers)
        self.assertEqual(identity.data, self.source.read_bytes())
        self.assertNotIn('immutable', self.client.get('/static/app.css?v=old', buffered=True).headers['Cache-Control'])
    def test_stale_or_corrupt_sidecar_is_never_served(self):
        self.source.write_bytes(b'body{color:blue}' * 200)
        response = self.client.get('/static/app.css',headers={'Accept-Encoding':'gzip'},buffered=True)
        self.assertEqual(response.data,self.source.read_bytes())
        self.assertNotIn('Content-Encoding',response.headers)
        self.packed.write_bytes(b'corrupt')
        self.assertEqual(self.client.get('/static/app.css',headers={'Accept-Encoding':'gzip'},buffered=True).status_code,200)
    def test_authenticated_html_is_private_and_compressed(self):
        response=self.client.get('/',headers={'Accept-Encoding':'gzip'},buffered=True)
        self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertIn('private',response.headers['Cache-Control'])
        self.assertEqual(gzip.decompress(response.data),b'<p>Example</p>' * 200)
    def test_static_requests_skip_session_storage(self):
        delegate=self.app.session_interface.delegate
        with patch.object(delegate, 'open_session', side_effect=AssertionError('Session storage accessed')):
            response = self.client.get('/static/app.css')
            self.assertEqual(response.status_code,200)
            response.close()
    def test_unchanged_session_and_logout(self):
        @self.app.route('/unchanged')
        def unchanged(): return session.get('email')
        @self.app.route('/logout')
        def logout(): session.clear(); return 'Bye'
        self.client.get('/')
        self.assertNotIn('Set-Cookie',self.client.get('/unchanged').headers)
        self.assertIn('Set-Cookie',self.client.get('/logout').headers)

class DashboardTests(unittest.TestCase):
    def test_background_queue_failure_is_retryable_and_bounded(self):
        submit=Mock(side_effect=RuntimeError('Busy'))
        snapshot=StatsSnapshot(lambda: 'unused',submit=submit)
        self.assertTrue(snapshot.get()['error'])
        self.assertFalse(snapshot.get()['refreshing'])
        self.assertEqual(submit.call_count,1)
    def test_inventory_is_owned_trimmed_and_refreshes_without_blocking(self):
        payload={'attributes':{'id':7,'relationships':{'servers':{'data':[{'attributes':{
            'id':1,'user':7,'name':'Example','environment':{'SECRET':'hidden'},'limits':{},'feature_limits':{}}}]}}}}
        jobs=[]
        ns=dict(improve_list_servers=Mock(return_value=payload),convert_to_product=lambda _: {'price':10},
                _snapshots=OrderedDict(),_lock=Lock(),display_cache=DisplayCache(),StatsSnapshot=StatsSnapshot,
                _submit=jobs.append)
        load_functions('managers/dashboard_inventory.py',ns,{'load_dashboard_inventory','dashboard_inventory'})
        first=ns['dashboard_inventory'](1,7)
        self.assertTrue(first['refreshing']);self.assertIsNone(first['data'])
        ns['dashboard_inventory'](1,7);self.assertEqual(len(jobs),1)
        jobs.pop()()
        ready=ns['dashboard_inventory'](1,7)
        self.assertEqual(ready['data']['monthly_usage'],10)
        self.assertNotIn('environment',ready['data']['servers'][0]['attributes'])
        ns['dashboard_inventory'](2,8);self.assertEqual(len(jobs),1)
        ns['display_cache'].clear()
        self.assertTrue(ns['dashboard_inventory'](1,7)['refreshing'])
        payload['attributes']['relationships']['servers']['data'][0]['attributes']['user']=8
        with self.assertRaises(ValueError):ns['load_dashboard_inventory'](7)

    def test_pagination_and_account_binding(self):
        app=Flask(__name__);app.secret_key='test'
        state={'data':{'servers':list(range(45)), 'server_count':45,'monthly_usage':100},'error':False,'refreshing':False}
        ns=dict(user=SimpleNamespace(route=lambda *a,**kw:lambda f:f),login_required=lambda f:f,
                request=request,session=session,jsonify=jsonify,dashboard_inventory=Mock(return_value=state),
                account_get_information=lambda _: (100,7,'A',True,False),render_template=lambda *a,**kw:str(kw['servers']))
        load_functions('Routes/AuthenticationHandler.py',ns,{'dashboard_server_page','dashboard_servers_data'})
        self.assertEqual(ns['dashboard_server_page'](state,2)['servers'],list(range(20,40)))
        self.assertEqual(ns['dashboard_server_page'](state,999)['current_server_page'],3)
        with app.test_request_context('/dashboard/servers',headers={'X-Dashboard-Account':'8'}):
            session.update(account_id=7,email='a@example.com')
            self.assertEqual(ns['dashboard_servers_data']()[1],409)
            ns['dashboard_inventory'].assert_not_called()
        with app.test_request_context('/dashboard/servers',headers={'X-Dashboard-Account':'7'}):
            session.update(account_id=7,email='a@example.com')
            response,status=ns['dashboard_servers_data']()
            self.assertEqual(status,200)
            self.assertEqual(response.get_json()['credits'],100)
            self.assertIn('no-store',response.headers['Cache-Control'])
            ns['dashboard_inventory'].assert_called_once_with(7,7)

if __name__=='__main__':unittest.main()
