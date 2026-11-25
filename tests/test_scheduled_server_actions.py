"""Scheduled jobs report what happened to a server and do not repeat a failure every run."""

import datetime
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from runtime.retry_throttle import RetryThrottle
from tests.test_credit_transactions import load_functions


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class InlineThread:
    """Runs a job's background work at once, so a test sees its outcome."""

    def __init__(self, target, args=(), kwargs=None):
        self.run = lambda: target(*args, **(kwargs or {}))

    def start(self):
        self.run()


class Panel:
    """Pterodactyl as the jobs see it. Servers on an unreachable node cannot be changed."""

    def __init__(self):
        self.servers = {}
        self.unreachable = set()
        self.list_status = 200
        self.attempts = []

    def add(self, server_id, owner, suspended=True, days_since_update=5):
        updated = datetime.datetime.now() - datetime.timedelta(days=days_since_update)
        self.servers[server_id] = {'id': server_id, 'user': owner, 'name': f'Server {server_id}',
                                   'suspended': suspended, 'limits': {'memory': 128},
                                   'updated_at': updated.strftime('%Y-%m-%dT%H:%M:%S+00:00')}

    def get(self, url, **kwargs):
        servers = [{'attributes': dict(server)} for server in self.servers.values()]
        return Mock(status_code=self.list_status, json=lambda: {'data': servers})

    def change(self, action, server_id):
        self.attempts.append((action, server_id))
        if server_id in self.unreachable:
            raise requests.HTTPError('504 Server Error: Gateway Timeout')

    def delete_server(self, server_id):
        try:
            self.change('delete', server_id)
        except requests.HTTPError:
            return False
        del self.servers[server_id]
        return True

    def unsuspend_server(self, server_id, automatic=False):
        self.change('unsuspend', server_id)
        self.servers[server_id]['suspended'] = False
        return 204

    def suspend_server(self, server_id, reason, source):
        self.change('suspend', server_id)
        self.servers[server_id]['suspended'] = True
        return 204


class RetryThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.throttle = RetryThrottle(retry_after=600, report_every=86400, clock=self.clock)

    def test_failed_action_waits_for_the_retry_delay_and_is_reported_once_a_day(self):
        self.assertTrue(self.throttle.begin('server'))
        self.assertTrue(self.throttle.failed('server'))
        for self.clock.now in (1, 300, 599):
            self.assertFalse(self.throttle.begin('server'))
        self.clock.now = 600
        self.assertTrue(self.throttle.begin('server'))
        self.assertFalse(self.throttle.failed('server'))
        self.clock.now = 86400
        self.assertTrue(self.throttle.begin('server'))
        self.assertTrue(self.throttle.failed('server'))

    def test_running_attempt_is_not_started_twice(self):
        self.assertTrue(self.throttle.begin('server'))
        self.assertFalse(self.throttle.begin('server'))
        self.assertTrue(self.throttle.begin('another server'))

    def test_success_forgets_the_failure(self):
        self.throttle.begin('server')
        self.throttle.failed('server')
        self.throttle.succeeded('server')
        self.assertTrue(self.throttle.begin('server'))
        self.assertTrue(self.throttle.failed('server'))

    def test_memory_is_bounded(self):
        throttle = RetryThrottle(max_entries=3, clock=self.clock)
        for key in range(10):
            throttle.begin(key)
            throttle.failed(('report', key))
        self.assertEqual(len(throttle.entries), 3)


class ScheduledJobFixture(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.panel = Panel()
        self.log = Mock()
        # pterodactyl_id -> panel account
        self.accounts = {}
        self.suspensions = {}
        self.ns = {
            'job_failures': RetryThrottle(retry_after=600, report_every=86400, clock=self.clock),
            'webhook_log': self.log, 'threading': SimpleNamespace(Thread=InlineThread),
            'safe_requests': SimpleNamespace(get=self.panel.get), 'requests': requests, 'datetime': datetime,
            'PTERODACTYL_URL': 'https://panel.invalid/', 'HEADERS': {},
            'delete_server': self.panel.delete_server, 'unsuspend_server': self.panel.unsuspend_server,
            'suspend_server': self.panel.suspend_server,
            'convert_to_product': lambda server: {'price': 0 if server['attributes']['limits']['memory'] == 128 else 720},
            'get_server_suspension': self.suspensions.get,
            'check_if_user_suspended': lambda owner: self.accounts[owner]['suspended'] if owner in self.accounts else None,
            'DatabaseManager': self.database(),
        }
        load_functions('managers/server_manager.py', self.ns, {'delete_server_and_report', 'unsuspend_server_and_report'})
        load_functions('managers/credit_manager.py', self.ns, {'check_to_unsuspend', 'delete_suspended_users_servers'})
        load_functions('managers/maintenance.py', self.ns, {'delete_inactive_free_servers'})

    def database(self):
        def execute_query(query, values=None, **kwargs):
            account = self.accounts.get(values[0])
            if account is None:
                return None
            if query.startswith('SELECT email, credits, suspended'):
                return account['email'], account['credits'], account['suspended']
            if query.startswith('SELECT last_seen'):
                return (account['last_seen'],)
            raise AssertionError(f'Unexpected query: {query}')
        database = SimpleNamespace(execute_query=execute_query)
        database_factory = Mock(return_value=database)
        database_factory.execute_query = execute_query
        return database_factory

    def account(self, owner, credits=0, suspended=0, days_since_seen=0):
        self.accounts[owner] = {'email': f'user{owner}@example.com', 'credits': credits, 'suspended': suspended,
                                'last_seen': datetime.datetime.now() - datetime.timedelta(days=days_since_seen)}

    def run_job(self, name, runs, every):
        """Run a job the way the scheduler does, `every` seconds apart."""
        with patch.dict(sys.modules, {
            'managers.credit_manager': SimpleNamespace(convert_to_product=self.ns['convert_to_product']),
        }):
            for _ in range(runs):
                self.ns[name]()
                self.clock.now += every

    def messages(self, status=None):
        return [call.args[0] for call in self.log.call_args_list
                if status is None or (call.args[1:2] or (call.kwargs.get('status', -1),))[0] == status]


class UnsuspensionCheckTests(ScheduledJobFixture):
    def test_server_that_cannot_be_deleted_is_reported_once_and_retried_every_ten_minutes(self):
        self.account(7)
        self.panel.add(1, owner=7)
        self.panel.servers[1]['limits']['memory'] = 1024
        self.suspensions[1] = {'active': True, 'source': 'billing', 'reason': 'Insufficient credits'}
        self.panel.unreachable.add(1)

        self.run_job('check_to_unsuspend', runs=120, every=60)
        self.assertEqual(self.panel.attempts, [('delete', 1)] * 12)
        self.assertEqual(self.messages(), ['Failed to delete server Server 1 (ID: 1) due to suspension for more than 3 days'])
        self.assertEqual(self.messages(status=2), self.messages())

        self.panel.unreachable.clear()
        self.run_job('check_to_unsuspend', runs=10, every=60)
        self.assertNotIn(1, self.panel.servers)
        self.assertEqual(self.messages()[-1], 'Deleted server Server 1 (ID: 1) due to suspension for more than 3 days')
        self.assertEqual(len(self.messages()), 2)

    def test_server_is_reported_unsuspended_only_after_it_was(self):
        self.account(7, credits=50)
        self.panel.add(1, owner=7)
        self.panel.servers[1]['limits']['memory'] = 1024
        self.suspensions[1] = {'active': True, 'source': 'billing', 'reason': 'Insufficient credits'}
        self.panel.unreachable.add(1)

        self.run_job('check_to_unsuspend', runs=120, every=60)
        self.assertEqual(self.panel.attempts, [('unsuspend', 1)] * 12)
        self.assertEqual(self.messages(), [
            'Failed to unsuspend server Server 1 (ID: 1) for user user7@example.com: 504 Server Error: Gateway Timeout'])

        self.panel.unreachable.clear()
        self.run_job('check_to_unsuspend', runs=10, every=60)
        self.assertFalse(self.panel.servers[1]['suspended'])
        self.assertEqual(self.messages()[-1],
                         'Unsuspended server Server 1 (ID: 1) for user user7@example.com (has 50.00 credits)')
        self.assertEqual(len(self.messages()), 2)

    def test_server_that_is_no_longer_eligible_is_left_alone_quietly(self):
        self.account(7, credits=50)
        self.panel.add(1, owner=7)
        self.panel.servers[1]['limits']['memory'] = 1024
        self.suspensions[1] = {'active': True, 'source': 'billing', 'reason': 'Insufficient credits'}
        # Staff suspended it by hand in the meantime, so the automatic unsuspension is refused.
        refused = Mock(return_value=409)
        self.ns['unsuspend_server'] = refused
        self.run_job('check_to_unsuspend', runs=30, every=60)
        self.assertEqual(refused.call_count, 3)
        self.log.assert_not_called()

    def test_owner_is_the_linked_panel_account_and_suspended_or_unlinked_owners_are_skipped(self):
        self.account(7, credits=50, suspended=1)
        for server_id, owner in ((1, 7), (2, 8)):
            self.panel.add(server_id, owner=owner)
            self.panel.servers[server_id]['limits']['memory'] = 1024
            self.suspensions[server_id] = {'active': True, 'source': 'billing', 'reason': 'Insufficient credits'}
        self.run_job('check_to_unsuspend', runs=3, every=60)
        self.assertEqual(self.panel.attempts, [])
        self.log.assert_not_called()

    def test_unavailable_server_list_is_reported_once_per_outage(self):
        self.panel.list_status = 502
        self.run_job('check_to_unsuspend', runs=30, every=60)
        self.assertEqual(self.messages(), ['Failed to get servers for unsuspension check: 502'])
        self.panel.list_status = 200
        self.run_job('check_to_unsuspend', runs=1, every=60)
        self.panel.list_status = 502
        self.run_job('check_to_unsuspend', runs=30, every=60)
        self.assertEqual(self.messages(), ['Failed to get servers for unsuspension check: 502'] * 2)


class SuspendedUserServerTests(ScheduledJobFixture):
    def test_server_is_only_reported_deleted_once_it_was(self):
        self.account(7, suspended=1)
        self.account(8)
        self.panel.add(1, owner=7, suspended=False)
        self.panel.add(2, owner=8, suspended=False)
        self.panel.unreachable.add(1)

        self.run_job('delete_suspended_users_servers', runs=60, every=120)
        self.assertEqual(self.panel.attempts, [('delete', 1)] * 12)
        self.assertEqual(self.messages(), ['Failed to delete server Server 1 (ID: 1) of a suspended user'])

        self.panel.unreachable.clear()
        self.run_job('delete_suspended_users_servers', runs=5, every=120)
        self.assertEqual(list(self.panel.servers), [2])
        self.assertEqual(self.messages(status=1), ['Server Server 1 (ID: 1) deleted due to user suspension'])
        self.assertEqual(len(self.messages()), 2)
        # As before, this job does not write to the activity log.
        self.assertTrue(all(call.kwargs == {'database_log': False} for call in self.log.call_args_list))

    def test_unavailable_server_list_is_reported_once_per_outage(self):
        self.panel.list_status = 502
        self.run_job('delete_suspended_users_servers', runs=30, every=120)
        self.assertEqual(self.messages(), ['Failed to get servers for suspension check: 502'])


class InactiveFreeServerTests(ScheduledJobFixture):
    def test_free_server_is_only_reported_deleted_once_it_was(self):
        self.account(7, days_since_seen=20)
        self.panel.add(1, owner=7)
        self.panel.unreachable.add(1)

        self.run_job('delete_inactive_free_servers', runs=48, every=3600)
        self.assertEqual(self.panel.attempts, [('delete', 1)] * 48)
        self.assertEqual(self.messages(), [
            'Failed to delete free tier server Server 1 (ID: 1) due to owner inactivity (17+ days)'] * 2)

        self.panel.unreachable.clear()
        self.run_job('delete_inactive_free_servers', runs=1, every=3600)
        self.assertEqual(self.panel.servers, {})
        self.assertEqual(self.messages()[-1], 'Deleted free tier server Server 1 (ID: 1) due to owner inactivity (17+ days)')

    def test_free_server_that_cannot_be_suspended_is_reported_once_a_day(self):
        self.account(7, days_since_seen=16)
        self.panel.add(1, owner=7, suspended=False)
        self.panel.unreachable.add(1)

        self.run_job('delete_inactive_free_servers', runs=24, every=3600)
        self.assertEqual(self.panel.attempts, [('suspend', 1)] * 24)
        self.assertEqual(self.messages(), [
            'Failed to suspend free tier server Server 1 (ID: 1) due to owner inactivity (15+ days): '
            '504 Server Error: Gateway Timeout'])

        self.panel.unreachable.clear()
        self.run_job('delete_inactive_free_servers', runs=2, every=3600)
        self.assertTrue(self.panel.servers[1]['suspended'])
        self.assertEqual(self.messages()[-1], 'Suspended free tier server Server 1 (ID: 1) due to owner inactivity (15+ days)')
        self.assertEqual(len(self.messages()), 2)

    def test_active_owner_keeps_the_free_server(self):
        self.account(7, days_since_seen=3)
        self.panel.add(1, owner=7, suspended=False)
        self.run_job('delete_inactive_free_servers', runs=3, every=3600)
        self.assertEqual(self.panel.attempts, [])
        self.log.assert_not_called()


if __name__ == '__main__':
    unittest.main()
