"""Cold stats loads, background retries, paged aggregates and node-only inventory."""

from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import sqlite3
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, Blueprint, request, jsonify, redirect, url_for, flash
import requests

from display_cache import DisplayCache
from stats_snapshot import StatsSnapshot
from test_credit_transactions import load_functions


def wait_for_snapshot(snapshot):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        result = snapshot.get()
        if not result['refreshing']:
            return result
        time.sleep(.005)
    raise AssertionError('Snapshot worker did not finish')


class SnapshotTests(unittest.TestCase):
    def test_cold_and_concurrent_reads_do_not_wait_or_duplicate_refresh(self):
        release = Event()
        loader = Mock(side_effect=lambda: (release.wait(2), {'total_users': 10})[1])
        snapshot = StatsSnapshot(loader)
        try:
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: snapshot.get(), range(8)))
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(all(result['data'] is None and result['refreshing'] for result in results))
            loader.assert_called_once()
        finally:
            release.set()
        result = wait_for_snapshot(snapshot)
        self.assertEqual(result['data']['total_users'], 10)
        result['data']['total_users'] = 99
        self.assertEqual(snapshot.get()['data']['total_users'], 10)
        loader.assert_called_once()

    def test_failed_refresh_preserves_last_good_values_and_backs_off(self):
        loader = Mock(return_value={'total_users': 10})
        snapshot = StatsSnapshot(loader)
        snapshot.get()
        wait_for_snapshot(snapshot)
        snapshot.updated = time.monotonic() - 100
        loader.side_effect = requests.ReadTimeout('Panel offline')
        snapshot.get()
        result = wait_for_snapshot(snapshot)
        self.assertTrue(result['error'])
        self.assertEqual(result['data'], {'total_users': 10})
        for _ in range(10): snapshot.get()
        self.assertEqual(loader.call_count, 2)
        snapshot.retry_at = 0
        loader.side_effect = None
        loader.return_value = {'total_users': 11}
        snapshot.get()
        self.assertEqual(wait_for_snapshot(snapshot)['data']['total_users'], 11)

    def test_first_failure_and_thread_start_failure_are_retryable(self):
        snapshot = StatsSnapshot(Mock(side_effect=RuntimeError('unavailable')))
        snapshot.get()
        result = wait_for_snapshot(snapshot)
        self.assertTrue(result['error'])
        self.assertIsNone(result['data'])
        snapshot = StatsSnapshot(Mock())
        with patch('stats_snapshot.Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('cannot start')
            result = snapshot.get()
        self.assertTrue(result['error'])
        self.assertFalse(result['refreshing'])


class AggregationTests(unittest.TestCase):
    def test_counts_use_two_queries_and_all_server_pages_are_aggregated(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript("CREATE TABLE users(role TEXT, credits REAL); CREATE TABLE tickets(id INT); "
                             "CREATE TABLE ticket_comments(id INT); INSERT INTO users VALUES "
                             "('client',10),('member',20),('admin',1000); INSERT INTO tickets VALUES (1); "
                             "INSERT INTO ticket_comments VALUES (1),(2);")
            calls = []
            def query(sql):
                calls.append(sql)
                return db.execute(sql).fetchone()
            servers = [dict(attributes=dict(id=1, price=0, plan='Free')),
                       dict(attributes=dict(id=2, price=10, plan='Paid')),
                       dict(attributes=dict(id=2, price=10, plan='Paid')),
                       dict(attributes=dict(id=3))]
            ns = load_functions('Routes/admin/stats.py', {
                'DatabaseManager': SimpleNamespace(execute_query=query),
                'iter_statistics_servers': lambda: iter(servers),
                'convert_to_product': lambda server: {'price': server['attributes']['price'], 'name': server['attributes']['plan']},
            }, {'load_platform_stats', 'shorten_number'})
            result = ns['load_platform_stats']()
        self.assertEqual(len(calls), 2)
        self.assertEqual((result['total_users'], result['total_clients']), (3, 1))
        self.assertEqual(result['total_credits_circulation'], '30.00')
        self.assertEqual((result['total_tickets'], result['total_ticket_messages']), (1, 2))
        self.assertEqual((result['total_servers'], result['free_servers'], result['paid_servers']), (3, 1, 1))
        self.assertEqual(result['chart_labels'], ['Free', 'Paid', 'Unknown'])
        self.assertEqual(result['chart_values'], [1, 1, 1])

    def api_functions(self, api):
        return load_functions('managers/server_manager.py', {
            'safe_requests': api, 'HEADERS': {}, 'PTERODACTYL_URL': 'https://example.invalid/',
            'time': time, 'display_cache': DisplayCache(),
        }, {'iter_statistics_servers', 'get_node_servers'})

    def test_statistics_streams_all_api_pages_in_small_batches(self):
        api = Mock()
        api.get.side_effect = [Mock(json=lambda: {'data': [{'id': 1}], 'meta': {'pagination': {'total_pages': 2}}}),
                               Mock(json=lambda: {'data': [{'id': 2}], 'meta': {'pagination': {'total_pages': 2}}})]
        ns = self.api_functions(api)
        self.assertEqual(list(ns['iter_statistics_servers']()), [{'id': 1}, {'id': 2}])
        self.assertEqual(api.get.call_count, 2)
        self.assertIn('page=2&per_page=100', api.get.call_args.args[0])
        self.assertLessEqual(api.get.call_args.kwargs['timeout'][1], 30)

    def test_failed_later_page_never_becomes_a_partial_successful_snapshot(self):
        api = Mock()
        api.get.side_effect = [Mock(json=lambda: {'data': [{'id': 1}], 'meta': {'pagination': {'total_pages': 2}}}),
                               requests.ReadTimeout('second page failed')]
        ns = self.api_functions(api)
        with self.assertRaises(requests.ReadTimeout): list(ns['iter_statistics_servers']())

    def test_node_inventory_is_targeted_cached_and_fresh_for_actions(self):
        api = Mock()
        payload = {'attributes': {'relationships': {'servers': {'data': [{'attributes': {'node': 7, 'id': 1}}]}}}}
        api.get.return_value.json.return_value = payload
        ns = self.api_functions(api)
        for _ in range(2): self.assertEqual(len(ns['get_node_servers'](7, use_cache=True)), 1)
        api.get.assert_called_once_with('https://example.invalid/api/application/nodes/7?include=servers',
                                        headers={}, timeout=(5, 30))
        ns['get_node_servers'](7)
        self.assertEqual(api.get.call_count, 2)
        payload['attributes']['relationships']['servers']['data'][0]['attributes']['node'] = 8
        with self.assertRaises(ValueError): ns['get_node_servers'](7)


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        admin = Blueprint('admin', __name__)
        def protect(function):
            @wraps(function)
            def guarded(*a, **kw):
                if request.headers.get('Test-Admin') == 'no': return 'Forbidden', 403
                return function(*a, **kw)
            return guarded
        self.snapshot = Mock()
        self.snapshot.get.return_value = {'data': None, 'refreshing': True, 'error': False, 'stale': True}
        self.node_servers = Mock(return_value=[{'attributes': {'id': n, 'node': 7}} for n in range(45)])
        self.threads = Mock()
        self.ns = dict(admin=admin, admin_required=protect, platform_stats=self.snapshot,
                       render_template=lambda template, **context: jsonify(context), jsonify=jsonify,
                       request=request, redirect=redirect, url_for=url_for, flash=flash, requests=requests,
                       get_nodes=lambda **kw: [{'node_id': 7, 'name': 'Node 7'}], get_node_servers=self.node_servers,
                       threading=self.threads, do_transfers=Mock())
        load_functions('Routes/admin/stats.py', self.ns, {'admin_stats', 'admin_stats_data'})
        load_functions('Routes/admin/nodes.py', self.ns, {'nodes', 'node', 'transfer_servers'})
        self.app.register_blueprint(admin, url_prefix='/admin')
        self.client = self.app.test_client()

    def test_stats_page_renders_loading_state_without_waiting_for_inventory(self):
        result = self.client.get('/admin/stats')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['stats'], {})
        self.assertEqual(self.client.get('/admin/stats/data').status_code, 202)

    def test_stats_data_returns_cached_totals_or_a_retryable_failure(self):
        self.snapshot.get.return_value.update(data={'total_users': 1})
        response = self.client.get('/admin/stats/data')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.snapshot.get.return_value.update(data=None, refreshing=False, error=True)
        self.assertEqual(self.client.get('/admin/stats/data').status_code, 503)

    def test_new_stats_json_endpoint_requires_admin_before_reading_snapshot(self):
        self.assertEqual(self.client.get('/admin/stats/data', headers={'Test-Admin': 'no'}).status_code, 403)
        self.snapshot.get.assert_not_called()

    def test_node_pages_render_twenty_servers_and_keep_total_for_transfers(self):
        data = self.client.get('/admin/node/7?page=2').json
        self.assertEqual(len(data['servers']), 20)
        self.assertEqual(data['servers'][0]['attributes']['id'], 20)
        self.assertEqual((data['total_servers'], data['total_pages'], data['current_page']), (45, 3, 2))
        self.node_servers.assert_called_once_with(7, use_cache=True)
        self.assertEqual(self.client.get('/admin/node/7?page=999').json['current_page'], 3)
        self.assertEqual(self.client.get('/admin/node/7?page=text').json['current_page'], 1)

    def test_node_timeout_returns_503_and_transfers_use_fresh_inventory(self):
        self.node_servers.side_effect = requests.ReadTimeout('Panel offline')
        self.assertEqual(self.client.get('/admin/node/7').status_code, 503)
        self.node_servers.side_effect = None
        self.node_servers.reset_mock()
        self.assertEqual(self.client.post('/admin/node/7/transfer', data={'num_servers': 30, 'target_node': 8}).status_code, 302)
        self.node_servers.assert_called_once_with(7)
        self.assertEqual(self.threads.Thread.call_args.kwargs['args'][1:], (30, 8))


if __name__ == '__main__':
    unittest.main()
