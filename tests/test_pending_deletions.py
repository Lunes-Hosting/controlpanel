"""Pending deletions remove leftover servers and never retry a failure every run."""

import ast
import datetime
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import requests

from tests.test_credit_transactions import ROOT, load_functions

MAINTENANCE = 'managers/maintenance.py'


def load_assignments(path, namespace, names):
    """Run selected module-level assignments so the tests use production values."""
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)]
    exec(compile(tree, path, 'exec'), namespace)


class Clock:
    """Stands in for the datetime module so a test decides what time it is."""
    timedelta = datetime.timedelta

    def __init__(self):
        self.current = datetime.datetime(2026, 11, 25, 6, 30)
        self.datetime = SimpleNamespace(now=lambda: self.current)

    def advance(self, **delta):
        self.current += datetime.timedelta(**delta)


class Database:
    """The tables the deletion job touches, behind the DatabaseManager interface."""

    def __init__(self):
        self.failing = None
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT UNIQUE, pterodactyl_id INTEGER, last_seen TEXT);
            CREATE TABLE pending_deletions (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, deletion_requested_time TEXT);
            CREATE TABLE tickets (id TEXT PRIMARY KEY, user_id INTEGER);
            CREATE TABLE ticket_comments (id INTEGER PRIMARY KEY, user_id INTEGER);
        """)

    def __call__(self):
        return self

    def execute_query(self, query, values=None, fetch_all=False):
        if self.failing and query.startswith(self.failing):
            raise RuntimeError('Injected database failure')
        values = tuple(str(value) if isinstance(value, datetime.datetime) else value for value in values or ())
        cursor = self.connection.execute(query.replace('%s', '?'), values)
        if query.strip().upper().startswith('SELECT'):
            return cursor.fetchall() if fetch_all else cursor.fetchone()
        self.connection.commit()

    def count(self, table):
        return self.connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


class Panel:
    """Pterodactyl as the job sees it: a user cannot be deleted while it owns servers."""

    def __init__(self):
        self.users = {}
        self.unreachable_servers = set()
        self.status = None
        self.offline = False
        self.calls = 0

    def request(self):
        self.calls += 1
        if self.offline:
            raise requests.ConnectionError('Panel offline')

    def improve_list_servers(self, pterodactyl_id):
        self.request()
        if pterodactyl_id not in self.users:
            return None
        return {'attributes': {'relationships': {'servers': {'data': [
            {'attributes': {'id': server_id, 'name': f'Server {server_id}'}}
            for server_id in sorted(self.users[pterodactyl_id])]}}}}

    def delete_server(self, server_id):
        self.request()
        if server_id in self.unreachable_servers:
            return False
        for servers in self.users.values():
            servers.discard(server_id)
        return True

    def delete(self, url, **kwargs):
        self.request()
        pterodactyl_id = int(url.rsplit('/', 1)[1])
        if self.status:
            return Mock(status_code=self.status)
        if pterodactyl_id not in self.users:
            return Mock(status_code=404)
        if self.users[pterodactyl_id]:
            return Mock(status_code=400)
        del self.users[pterodactyl_id]
        return Mock(status_code=204)


class PendingDeletionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.database = Database()
        self.addCleanup(self.database.connection.close)
        self.panel = Panel()
        self.log = Mock()
        self.ns = {
            'DatabaseManager': self.database, 'datetime': self.clock, 'webhook_log': self.log,
            'requests': SimpleNamespace(delete=self.panel.delete),
            'improve_list_servers': self.panel.improve_list_servers, 'delete_server': self.panel.delete_server,
            'PTERODACTYL_URL': 'https://panel.invalid/', 'HEADERS': {},
        }
        load_assignments(MAINTENANCE, self.ns, {'PENDING_DELETION_RETRY_INTERVAL', '_pending_deletion_next_check'})
        load_functions(MAINTENANCE, self.ns, {'sync_users_script', 'delete_remaining_servers'})
        load_functions('managers/user_manager.py', self.ns, {'get_ptero_id'})

    def account(self, user_id, servers=(), requested_days_ago=16):
        email = f'user{user_id}@example.com'
        self.database.execute_query('INSERT INTO users (id, email, pterodactyl_id) VALUES (%s, %s, %s)',
                                    (user_id, email, 1000 + user_id))
        self.database.execute_query('INSERT INTO pending_deletions (email, deletion_requested_time) VALUES (%s, %s)',
                                    (email, self.clock.current - datetime.timedelta(days=requested_days_ago)))
        self.panel.users[1000 + user_id] = set(servers)
        return email

    def sync(self, runs=1):
        """Run the job as the scheduler does, once a minute."""
        for _ in range(runs):
            self.ns['sync_users_script']()
            self.clock.advance(minutes=1)

    def messages(self, status=None):
        return [call.args[0] for call in self.log.call_args_list
                if status is None or call.kwargs.get('status') == status]

    def assert_retried_once_per_interval(self, email, repair):
        """A deletion that keeps failing is attempted, and reported, once per interval."""
        interval = self.ns['PENDING_DELETION_RETRY_INTERVAL']
        attempt = f'Processing pending deletion for {email} after 15 days'
        self.sync()
        self.assertEqual(self.messages().count(attempt), 1)
        self.assertEqual(len(self.messages(status=2)), 1)
        reported, calls = len(self.messages()), self.panel.calls

        self.sync(runs=120)
        self.assertEqual(len(self.messages()), reported)
        self.assertEqual(self.panel.calls, calls)
        self.assertEqual(self.database.count('pending_deletions'), 1)

        self.clock.advance(seconds=interval.total_seconds())
        self.sync(runs=120)
        self.assertEqual(self.messages().count(attempt), 2)
        self.assertEqual(len(self.messages(status=2)), 2)

        repair()
        self.clock.advance(seconds=interval.total_seconds())
        self.sync()
        self.assertEqual(self.messages()[-1], f'Successfully processed pending deletion for {email}')
        self.assertEqual(self.panel.users, {})
        self.assertEqual(self.database.count('users'), 0)
        self.assertEqual(self.database.count('pending_deletions'), 0)
        self.assertEqual(self.ns['_pending_deletion_next_check'], {})

    def test_leftover_servers_are_deleted_so_the_account_can_be_deleted(self):
        email = self.account(1, servers={501, 502})
        self.database.execute_query("INSERT INTO tickets VALUES ('ticket', 1)")
        self.database.execute_query('INSERT INTO ticket_comments VALUES (1, 1)')
        self.sync()
        self.assertEqual(self.panel.users, {})
        for table in ('users', 'pending_deletions', 'tickets', 'ticket_comments'):
            self.assertEqual(self.database.count(table), 0, table)
        self.assertEqual(self.messages(), [
            f'Processing pending deletion for {email} after 15 days',
            f'Deleted server Server 501 (ID: 501) due to pending account deletion of {email}',
            f'Deleted server Server 502 (ID: 502) due to pending account deletion of {email}',
            f'Successfully processed pending deletion for {email}'])
        self.assertEqual(self.ns['_pending_deletion_next_check'], {})

        self.log.reset_mock()
        self.sync(runs=5)
        self.log.assert_not_called()

    def test_servers_are_kept_until_the_deletion_is_due(self):
        self.account(1, servers={501}, requested_days_ago=14)
        self.sync(runs=5)
        self.assertEqual(self.panel.users, {1001: {501}})
        self.assertEqual(self.database.count('users'), 1)
        self.assertEqual(self.database.count('pending_deletions'), 1)
        self.assertEqual(self.panel.calls, 0)
        self.log.assert_not_called()

    def test_server_that_cannot_be_deleted_is_named_and_retried_once_per_interval(self):
        email = self.account(1, servers={501, 502})
        self.panel.unreachable_servers.add(502)
        self.assert_retried_once_per_interval(email, repair=self.panel.unreachable_servers.clear)
        self.assertEqual(self.messages(status=2)[0],
                         f'Failed to delete {email} from Pterodactyl - Status: 400 (servers that could not be deleted: 502)')

    def test_panel_error_is_retried_once_per_interval(self):
        email = self.account(1)
        self.panel.status = 500
        self.assert_retried_once_per_interval(email, repair=lambda: setattr(self.panel, 'status', None))
        self.assertEqual(self.messages(status=2)[0], f'Failed to delete {email} from Pterodactyl - Status: 500')

    def test_unreachable_panel_is_retried_once_per_interval(self):
        email = self.account(1, servers={501})
        self.panel.offline = True
        self.assert_retried_once_per_interval(email, repair=lambda: setattr(self.panel, 'offline', False))
        self.assertEqual(self.messages(status=2)[0], f'Network error deleting {email} from Pterodactyl: Panel offline')

    def test_local_cleanup_failure_is_retried_once_per_interval(self):
        email = self.account(1)
        self.database.failing = 'DELETE FROM users'
        self.assert_retried_once_per_interval(email, repair=lambda: setattr(self.database, 'failing', None))
        self.assertEqual(self.messages(status=2)[0],
                         f'Error processing pending deletion for {email}: Injected database failure')

    def test_deletions_waiting_for_a_retry_do_not_hold_up_other_accounts(self):
        for user_id in range(1, 51):
            self.account(user_id, servers={500 + user_id})
        self.panel.unreachable_servers.update(range(501, 551))
        self.account(51)
        self.sync()
        self.assertEqual(len(self.messages(status=2)), 50)
        self.assertEqual(self.database.count('users'), 51)

        self.log.reset_mock()
        self.sync()
        self.assertEqual(self.messages(), ['Processing pending deletion for user51@example.com after 15 days',
                                           'Successfully processed pending deletion for user51@example.com'])
        self.assertEqual(self.database.count('users'), 50)
        self.assertNotIn(1051, self.panel.users)

    def test_accounts_that_are_already_gone_are_cleaned_up_without_waiting(self):
        missing_from_panel = self.account(1)
        del self.panel.users[1001]
        without_panel_account = self.account(2)
        self.database.execute_query('UPDATE users SET pterodactyl_id = NULL WHERE id = 2')
        del self.panel.users[1002]
        already_deleted = self.account(3)
        self.database.execute_query('DELETE FROM users WHERE id = 3')
        del self.panel.users[1003]
        self.sync()
        self.assertEqual(self.database.count('users'), 0)
        self.assertEqual(self.database.count('pending_deletions'), 0)
        self.assertEqual(self.messages(status=2), [])
        self.assertIn(f'Successfully processed pending deletion for {missing_from_panel}', self.messages())
        self.assertIn(f'Successfully processed pending deletion for {without_panel_account}', self.messages())
        self.assertIn(f'User {already_deleted} already deleted, cleaning up pending deletion entry', self.messages())
        self.assertEqual(self.ns['_pending_deletion_next_check'], {})


if __name__ == '__main__':
    unittest.main()
