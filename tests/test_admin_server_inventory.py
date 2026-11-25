"""Admin inventory pagination and the production read-timeout regression."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, Blueprint, request, flash, get_flashed_messages
import requests

from runtime.display_cache import DisplayCache
from tests.test_credit_transactions import load_functions


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.response = self.api.get.return_value
        self.response.json.return_value = {'data': [{'attributes': {'id': 21, 'name': 'Test'}}],
                                           'meta': {'pagination': {'total': 1000, 'total_pages': 50}}}
        self.full_inventory = Mock(return_value={'data': []})
        self.ns = load_functions('managers/server_manager.py', {
            'requests': requests, 'safe_requests': self.api, 'display_cache': DisplayCache(),
            'improve_list_servers': self.full_inventory,
            'PTERODACTYL_URL': 'https://example.invalid/', 'HEADERS': {},
        }, {'display_servers', 'display_server_page'})
        self.app = Flask(__name__)
        admin = Blueprint('admin', __name__)
        self.ns.update(admin=admin, admin_required=lambda f: f, request=request,
                       render_template=lambda template, **kw: kw)
        load_functions('Routes/admin/servers.py', self.ns, {'admin_servers'})
        self.app.register_blueprint(admin, url_prefix='/admin')
        self.client = self.app.test_client()

    def test_visible_page_is_requested_and_cached_without_loading_all_servers(self):
        for _ in range(2):
            response = self.client.get('/admin/servers?page=2')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['current_page'], 2)
            self.assertEqual(response.json['total_pages'], 50)
            self.assertEqual(response.json['total_servers'], 1000)
        self.api.get.assert_called_once_with(
            'https://example.invalid/api/application/servers?page=2&per_page=20',
            headers={}, timeout=(5, 30))
        self.full_inventory.assert_not_called()

    def test_timeout_returns_retryable_unavailable_response_instead_of_500(self):
        self.api.get.side_effect = requests.ReadTimeout('read timeout')
        response = self.client.get('/admin/servers')
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'try again', response.data)

    def test_full_inventory_search_timeout_is_also_handled(self):
        self.full_inventory.side_effect = requests.ReadTimeout('read timeout')
        self.assertEqual(self.client.get('/admin/servers?search=test').status_code, 503)
        self.full_inventory.assert_called_once_with(None, timeout=(5, 30))

    def test_search_preserves_matching_by_id_and_name(self):
        self.full_inventory.return_value = {'data': [
            {'attributes': {'id': 42, 'name': 'Minecraft'}},
            {'attributes': {'id': 53, 'name': 'Python'}},
        ]}
        for search in ('42', 'mineCRAFT'):
            response = self.client.get('/admin/servers?search=' + search)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['total_servers'], 1)
            self.assertEqual(response.json['servers'][0]['attributes']['id'], 42)
        self.api.get.assert_not_called()

    def test_bad_pagination_response_returns_503(self):
        self.response.json.return_value = {'errors': ['Unavailable']}
        self.assertEqual(self.client.get('/admin/servers').status_code, 503)

    def test_http_error_returns_503(self):
        self.response.raise_for_status.side_effect = requests.HTTPError('upstream unavailable')
        self.assertEqual(self.client.get('/admin/servers').status_code, 503)

    def test_invalid_and_out_of_range_pages_are_clamped(self):
        self.assertEqual(self.client.get('/admin/servers?page=nonsense').json['current_page'], 1)
        self.assertEqual(self.client.get('/admin/servers?page=-3').json['current_page'], 1)
        response = self.client.get('/admin/servers?page=999')
        self.assertEqual(response.json['current_page'], 50)
        self.assertIn('page=50&per_page=20', self.api.get.call_args.args[0])


class AdminDeletionTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.delete = Mock(return_value=True)
        self.lookup = Mock(return_value={'attributes': {'id': 21, 'name': 'Test'}})
        self.log = Mock()
        self.ns = load_functions('Routes/admin/servers.py', dict(
            admin=SimpleNamespace(route=lambda *a, **kw: lambda f: f), admin_required=lambda f: f,
            session={'email': 'admin@example.com'}, flash=flash, redirect=lambda target: target,
            url_for=lambda endpoint, **kw: endpoint, webhook_log=self.log,
            delete_server=self.delete, get_server_information=self.lookup,
        ), {'admin_delete_server'})

    def submit(self):
        with self.app.test_request_context('/', method='POST'):
            self.assertEqual(self.ns['admin_delete_server']('21'), 'admin.admin_servers')
            return get_flashed_messages()

    def test_deletion_is_recorded_with_the_admin_and_server(self):
        self.assertEqual(self.submit(), [])
        self.delete.assert_called_once_with('21')
        self.log.assert_called_once_with('Admin admin@example.com deleted server Test (ID: 21)', database_log=True)

    def test_failed_deletion_is_shown_to_the_admin_and_not_recorded_as_done(self):
        self.delete.return_value = False
        self.assertEqual(self.submit(), ['Failed to delete server. Its node may be unreachable.'])
        self.log.assert_not_called()

    def test_missing_server_is_reported_without_a_delete_request(self):
        self.lookup.return_value = None
        self.assertEqual(self.submit(), ['Server not found'])
        self.delete.assert_not_called()
        self.log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
