"""Ticket route and migration regressions without live panel services."""

import ast
import datetime
from pathlib import Path
import time
import unittest
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

from flask import Flask, Blueprint, abort, flash, redirect, request, session, url_for
from jinja2 import Environment, FileSystemLoader
from typing import Optional


ROOT = Path(__file__).resolve().parent


def load_functions(path, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    exec(compile(tree, path, "exec"), namespace)
    return namespace


class TicketLabelTests(unittest.TestCase):
    def test_discord_name_is_short_but_lookup_uses_full_uuid(self):
        ticket_id = str(uuid4())
        short = load_functions('managers/ticket_ids.py', {})['short_ticket_id']
        self.assertEqual(short(ticket_id), ticket_id[:8])
        self.assertEqual(short(123), '123')
        database = Mock()
        database.execute_query.return_value = ('waiting',)
        ns = load_functions('discord_bot/ticket_sync.py', {
            'discord': Mock(), 'Optional': Optional, 'Tuple': tuple,
            'DatabaseManager': database, 'short_ticket_id': short,
        })
        self.assertEqual(ns['_resolve_ticket_channel_name'](ticket_id), f'ticket-{ticket_id[:8]}-waiting')
        self.assertEqual(database.execute_query.call_args.args[1], (ticket_id,))

    def test_panel_labels_are_short_but_links_and_forms_keep_full_uuid(self):
        env = Environment(loader=FileSystemLoader(ROOT / 'templates'))
        env.globals.update(
            url_for=lambda endpoint, **kw: '/test/' + str(kw.get('ticket_id', '')),
            get_flashed_messages=lambda **kw: [], session={}, config={}, request=Mock(),
        )
        ticket_id = str(uuid4())
        row = (ticket_id, 7, 'Help', 'open', datetime.datetime.now(), 'waiting', None, False)
        for template, context in [
            ('tickets.html', {'tickets': [row]}),
            ('admin/tickets.html', {'tickets': [row], 'filter': 'all'}),
            ('ticket.html', {'info': dict(id=ticket_id, author='User', title='Help', status='open',
                                        created_at=datetime.datetime.now()), 'messages': []}),
        ]:
            with self.subTest(template=template):
                rendered = env.get_template(template).render(**context)
                self.assertIn(f'#{ticket_id[:8]}<', rendered)
                self.assertNotIn(f'#{ticket_id}<', rendered)
                self.assertIn(f'/test/{ticket_id}', rendered)


class TicketMigrationTests(unittest.TestCase):
    def test_widens_all_relationships_and_can_be_repeated(self):
        columns = {('tickets', 'id'): ('int', None),
                   ('ticket_comments', 'ticket_id'): ('int', None),
                   ('ticket_discord_channels', 'ticket_id'): ('int', None)}
        alterations = []

        def query(sql, params=None):
            if sql.startswith('SELECT'):
                return columns.get(params)
            alterations.append(sql)
            for key in columns:
                if f'`{key[0]}`' in sql:
                    columns[key] = ('varchar', 36)

        database = Mock(execute_query=Mock(side_effect=query))
        migrate = load_functions('managers/ticket_ids.py', {
            'DatabaseManager': database,
        })['ensure_ticket_id_schema']
        migrate()
        self.assertEqual(len(alterations), 3)
        self.assertTrue(all('VARCHAR(36) NOT NULL' in sql for sql in alterations))
        self.assertTrue(all('UPDATE ' not in sql and 'DELETE ' not in sql for sql in alterations))
        migrate()
        self.assertEqual(len(alterations), 3)

    def test_optional_discord_table_can_be_absent(self):
        database = Mock()
        database.execute_query.side_effect = [('varchar', 36), ('varchar', 36), None]
        load_functions('managers/ticket_ids.py', {
            'DatabaseManager': database,
        })['ensure_ticket_id_schema']()
        self.assertEqual(database.execute_query.call_count, 3)

    def test_required_table_missing_stops_startup(self):
        database = Mock()
        database.execute_query.return_value = None
        migrate = load_functions('managers/ticket_ids.py', {
            'DatabaseManager': database,
        })['ensure_ticket_id_schema']
        with self.assertRaisesRegex(RuntimeError, 'tickets.id'):
            migrate()

    def test_older_comment_schema_gets_auto_increment_once(self):
        database = Mock()
        database.execute_query.side_effect = [('',), None, ('auto_increment',)]
        migrate = load_functions('managers/ticket_ids.py', {'DatabaseManager': database})['ensure_comment_auto_increment']
        migrate()
        migrate()
        alterations = [call for call in database.execute_query.call_args_list if call.args[0].startswith('ALTER')]
        self.assertEqual(len(alterations), 1)


class ConcurrentCommentTests(unittest.TestCase):
    def test_concurrent_panel_replies_receive_distinct_database_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'tickets.sqlite')
            ticket_id = str(uuid4())
            with closing(sqlite3.connect(path)) as database:
                database.executescript('CREATE TABLE tickets (id TEXT PRIMARY KEY, user_id INTEGER, status TEXT, reply_status TEXT, last_reply TEXT); CREATE TABLE ticket_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, user_id INTEGER, ticketcomment TEXT, created_at TEXT);')
                database.execute('INSERT INTO tickets (id,user_id,status) VALUES (?,7,\'open\')', (ticket_id,))
                database.commit()

            def query(sql, params=None, **kwargs):
                with closing(sqlite3.connect(path, timeout=15)) as database:
                    cursor = database.execute(sql.replace('%s', '?').replace('NOW()', 'CURRENT_TIMESTAMP'), params or ())
                    result = cursor.fetchone() if sql.startswith('SELECT') else None
                    database.commit()
                    return result

            app = Flask(__name__)
            app.secret_key = 'test'
            ns = load_functions('Routes/Tickets.py', {
                'tickets': SimpleNamespace(route=lambda *a, **kw: lambda f: f),
                'login_required': lambda f: f, 'DatabaseManager': SimpleNamespace(execute_query=query),
                'session': session, 'request': request, 'abort': abort,
                'redirect': lambda x: x, 'url_for': lambda *a, **kw: '/',
                'get_id': lambda _: (7,), 'get_name': lambda _: ('User',),
                'is_admin': lambda _: False, 'is_support': lambda _: False,
                'time': time, 'datetime': datetime,
                'webhook_log': Mock(), 'schedule_ticket_message': Mock(),
                'schedule_ticket_channel_status_update': Mock(),
            })

            def reply(i):
                with app.test_request_context('/', method='POST', data={'message': f'Reply {i}'}):
                    session['email'] = 'user@example.com'
                    return ns['add_message_submit'](ticket_id)

            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(reply, range(16)))
            with closing(sqlite3.connect(path)) as database:
                count, distinct = database.execute('SELECT COUNT(*), COUNT(DISTINCT id) FROM ticket_comments').fetchone()
                self.assertEqual((count, distinct), (16, 16))


class TicketRouteTests(unittest.TestCase):
    def setUp(self):
        self.database = Mock()
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.app.testing = True
        self.ns = load_functions('Routes/Tickets.py', {
            'tickets': Blueprint('tickets', __name__),
            'login_required': lambda function: function,
            'DatabaseManager': self.database, 'uuid4': uuid4,
            'request': request, 'session': session, 'abort': abort, 'flash': flash,
            'redirect': redirect, 'url_for': url_for, 'datetime': datetime, 'time': time,
            'get_id': Mock(return_value=(7,)), 'get_name': Mock(return_value=('User',)),
            'is_admin': Mock(return_value=False), 'is_support': Mock(return_value=False),
            'webhook_log': Mock(), 'render_template': Mock(return_value='ticket rendered'),
            'schedule_ticket_channel_creation': Mock(), 'schedule_ticket_message': Mock(),
            'schedule_ticket_channel_deletion': Mock(), 'schedule_ticket_channel_status_update': Mock(),
        })
        self.app.register_blueprint(self.ns['tickets'], url_prefix='/tickets')
        self.client = self.app.test_client()
        with self.client.session_transaction() as state:
            state['email'] = 'user@example.com'

    def test_new_ticket_uses_uuid_in_database_redirect_and_discord(self):
        self.database.execute_query.return_value = None
        response = self.client.post('/tickets/create/submit', data={'title': 'Help', 'message': 'Hello'})
        self.assertEqual(response.status_code, 302)
        ticket_id = response.location.rsplit('/', 1)[1]
        self.assertEqual(UUID(ticket_id).version, 4)
        calls = self.database.execute_query.call_args_list
        insert = next(call for call in calls if 'INSERT INTO tickets ' in call.args[0])
        self.assertEqual(insert.args[1][0], ticket_id)
        comment = next(call for call in calls if 'INSERT INTO ticket_comments ' in call.args[0])
        self.assertEqual(comment.args[1][0], ticket_id)
        self.assertNotIn('(id,', comment.args[0])
        self.assertEqual(self.ns['schedule_ticket_channel_creation'].call_args.args[0], ticket_id)
        self.assertFalse(any('FROM tickets ORDER BY id' in call.args[0] for call in calls))

    def test_uuid_and_legacy_tickets_support_view_reply_and_close(self):
        for ticket_id in (str(uuid4()), '123'):
            with self.subTest(ticket_id=ticket_id):
                self.database.execute_query.side_effect = [
                    (7, 'User', 'member'), (ticket_id, 7, 'Help', 'open', None), [],
                ]
                self.assertEqual(self.client.get(f'/tickets/{ticket_id}').status_code, 200)
                self.database.execute_query.side_effect = [(7, 'open'), None, None]
                response = self.client.post(f'/tickets/message/submit/{ticket_id}', data={'message': 'Reply'})
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.endswith(ticket_id))
                self.database.execute_query.side_effect = [
                    (7, 'User', 'member'), (ticket_id, 7, 'Help', 'open', None), None,
                ]
                self.assertEqual(self.client.post(f'/tickets/toggle/{ticket_id}').status_code, 302)
                self.ns['schedule_ticket_channel_deletion'].assert_called_with(ticket_id, 'Closed via web panel')

    def test_uuid_does_not_bypass_ticket_ownership(self):
        ticket_id = str(uuid4())
        self.database.execute_query.return_value = (99, 'open')
        response = self.client.post(f'/tickets/message/submit/{ticket_id}', data={'message': 'Unauthorized'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.database.execute_query.call_count, 1)
        self.ns['schedule_ticket_message'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
