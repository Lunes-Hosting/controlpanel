"""Exercise indexed log pagination, count reuse and complete detail views."""

import json
import sqlite3
from types import SimpleNamespace
import unittest

from flask import Flask, Blueprint, request, jsonify, flash, redirect, url_for

from runtime.display_cache import DisplayCache
from tests.test_credit_transactions import load_functions


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE activity_logs(id INTEGER PRIMARY KEY, create_time TEXT, content TEXT); '
                              'CREATE INDEX logs_time ON activity_logs(create_time, id);')
        self.long_message = 'Large message ' * 1000
        self.db.executemany('INSERT INTO activity_logs VALUES (?, ?, ?)', [
            (number, '2026-10-16 12:00:00', json.dumps({'status': 'Info', 'message':
                self.long_message if number == 85 else f'Log {number}'})) for number in range(1, 86)
        ])
        self.queries = []
        def query(sql, params=(), fetch_all=False):
            self.queries.append((sql, params))
            cursor = self.db.execute(sql.replace('%s', '?'), params)
            return cursor.fetchall() if fetch_all else cursor.fetchone()
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        admin = Blueprint('admin', __name__)
        self.ns = load_functions('Routes/admin/activity_logs.py', dict(
            admin=admin, admin_required=lambda f: f, request=request, json=json,
            DatabaseManager=SimpleNamespace(execute_query=query),
            log_count_cache=DisplayCache(max_entries=64),
            render_template=lambda template, **context: jsonify(context),
            flash=flash, redirect=redirect, url_for=url_for,
        ))
        self.app.register_blueprint(admin, url_prefix='/admin')
        self.client = self.app.test_client()

    def test_page_contains_twenty_rows_in_deterministic_newest_order(self):
        result = self.client.get('/admin/activity_logs').json
        self.assertEqual([row['id'] for row in result['logs']], list(range(85, 65, -1)))
        self.assertEqual(result['total_logs'], 85)
        self.assertEqual(result['total_pages'], 5)
        result = self.client.get('/admin/activity_logs?page=2').json
        self.assertEqual([row['id'] for row in result['logs']], list(range(65, 45, -1)))
        self.assertEqual(len([sql for sql, _ in self.queries if 'COUNT(*)' in sql]), 1)

    def test_page_query_uses_covering_order_index_before_loading_bodies(self):
        self.client.get('/admin/activity_logs?page=4')
        sql, params = self.queries[-1]
        inner = sql.split('JOIN (', 1)[1].split(') page_rows', 1)[0]
        self.assertNotIn('content', inner)
        self.assertEqual(params, (20, 60))
        plan = self.db.execute('EXPLAIN QUERY PLAN ' + sql.replace('%s', '?'), params).fetchall()
        self.assertTrue(any('COVERING INDEX logs_time' in row[3] for row in plan), plan)

    def test_previews_are_small_and_details_keep_complete_content(self):
        preview = self.client.get('/admin/activity_logs').json['logs'][0]
        self.assertEqual(len(preview['message']), 501)
        self.assertNotIn('raw_content', preview)
        detail = self.client.get('/admin/activity_logs/view/85').json['log']
        self.assertEqual(detail['message'], self.long_message)
        self.assertEqual(json.loads(detail['raw_content'])['message'], self.long_message)

    def test_search_filters_database_rows_and_counts_are_isolated(self):
        result = self.client.get('/admin/activity_logs?search=Log+1').json
        self.assertEqual(result['total_logs'], 11)
        self.assertTrue(all('Log 1' in row['message'] for row in result['logs']))
        result = self.client.get('/admin/activity_logs?search=Log+2').json
        self.assertEqual(result['total_logs'], 11)
        self.assertTrue(all('Log 2' in row['message'] for row in result['logs']))
        self.assertEqual(len([sql for sql, _ in self.queries if 'COUNT(*)' in sql]), 2)
        self.assertEqual(self.client.get("/admin/activity_logs?search='OR+1=1").json['logs'], [])

    def test_bad_and_out_of_range_pages_are_clamped(self):
        for page in ('text', '-1', '0'):
            self.assertEqual(self.client.get('/admin/activity_logs?page=' + page).json['current_page'], 1)
        result = self.client.get('/admin/activity_logs?page=999').json
        self.assertEqual(result['current_page'], 5)
        self.assertEqual([row['id'] for row in result['logs']], [5, 4, 3, 2, 1])

    def test_non_object_json_and_plain_logs_do_not_crash(self):
        for content in ('plain text', 'null', '[1, 2]', '"a string"', None):
            self.db.execute('UPDATE activity_logs SET content=? WHERE id=85', (content,))
            self.assertEqual(self.client.get('/admin/activity_logs').status_code, 200)
            self.assertEqual(self.client.get('/admin/activity_logs/view/85').status_code, 200)

    def test_latest_entries_are_not_cached_with_the_count(self):
        self.client.get('/admin/activity_logs')
        self.db.execute('INSERT INTO activity_logs VALUES (86, ?, ?)',
                        ('2026-10-16 13:00:00', '{"message":"New event"}'))
        self.assertEqual(self.client.get('/admin/activity_logs').json['logs'][0]['id'], 86)


if __name__ == '__main__':
    unittest.main()
