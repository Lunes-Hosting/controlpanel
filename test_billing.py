"""Run with python -m unittest test_billing; no database or hosting credentials needed."""

import ast
import copy
import json
import re
import unittest
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask, current_app, render_template, request, session

from billing import balance_estimate, match_product, plan_estimates, topup_estimates
from productsexample import products as catalog


ROOT = Path(__file__).resolve().parent


def server(server_id=1, memory=1024, suspended=False, name='Community bot'):
    plan = next(product for product in catalog if product['limits']['memory'] == memory)
    return {'attributes': {
        'id': server_id, 'user': 7, 'name': name, 'limits': copy.deepcopy(plan['limits']),
        'suspended': suspended, 'status': None, 'identifier': f'test{server_id}',
        'uuid': f'test{server_id}-0000-0000', 'node': 1, 'allocation': 1,
        'feature_limits': plan['product_limits'], 'created_at': '2026-01-01T00:00:00+00:00',
    }}


def load_view(path, name, namespace):
    # Importing these modules starts service clients. Load the real view body while
    # supplying its service boundaries, so tests never touch MySQL or Pterodactyl.
    module = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    view = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name)
    view.decorator_list = []
    exec(compile(ast.Module(body=[view], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


def make_app():
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.config.update(TESTING=True, SECRET_KEY='local-billing-test',
                      TEST_BALANCE=Decimal('126.5'), TEST_SCENARIO='normal')

    def get_servers(_):
        scenario = request.args.get('scenario', app.config['TEST_SCENARIO'])
        rows = [server(), server(2, 2048, name='Minecraft'), server(3, 128, name='Sandbox')]
        if scenario == 'unavailable':
            return None
        if scenario == 'timeout':
            raise ConnectionError('Hosting API unavailable')
        if scenario == 'free':
            rows = [server(3, 128)]
        elif scenario == 'empty':
            rows = []
        elif scenario == 'paused':
            rows[0]['attributes']['suspended'] = True
            rows[1]['attributes']['suspended'] = True
        elif scenario == 'mixed':
            rows[1]['attributes']['suspended'] = True
        elif scenario == 'long':
            rows[0]['attributes']['name'] = 'A very long community server name ' * 5
        elif scenario == 'escaped':
            rows[0]['attributes']['name'] = '</script><script>alert("server")</script>'
        return {'attributes': {'relationships': {'servers': {'data': rows}}}}

    def get_account(_):
        amount = Decimal(request.args.get('balance', str(app.config['TEST_BALANCE'])))
        return amount, 7, 'Alex', True, request.args.get('scenario') == 'account-suspended'

    def get_products():
        return [] if request.args.get('scenario') == 'no-packages' else copy.deepcopy(catalog)

    def common():
        return dict(session=session, render_template=render_template, sha256=sha256,
                    products=get_products(), balance_estimate=balance_estimate,
                    topup_estimates=topup_estimates, plan_estimates=plan_estimates,
                    account_get_information=get_account, improve_list_servers=get_servers,
                    SUBSCRIPTION_CREDIT_PRICES={},
                    requests=type('Requests', (), {'RequestException': ConnectionError}),
                    current_app=current_app,
                    convert_to_product=lambda item: match_product(item, catalog))

    @app.before_request
    def sign_in():
        session['email'] = 'alex@example.test'

    @app.route('/')
    def account_view():
        return load_view('Routes/AuthenticationHandler.py', 'index', common())()

    @app.route('/servers/<server_id>', endpoint='servers.server')
    def server_view(server_id):
        namespace = common()
        namespace.update(
            get_user_verification_ptero_id_and_credits=lambda email: (True, 7, get_account(email)[0]),
            verify_server_ownership_by_ptero_id=lambda *_: True,
            get_server_information=lambda _: server(), get_nodes=lambda: [],
        )
        return load_view('Routes/Servers.py', 'server', namespace)(server_id)

    # All account/checkout links remain local and inert in the preview app.
    for endpoint, rule in {
        'index': '/home', 'tickets.tickets_index': '/tickets', 'admin.admin_index': '/admin',
        'user.index': '/account', 'user.logout': '/logout', 'user.delete_account': '/delete',
        'user.resend_confirmation_email': '/verify', 'store.billing_portal': '/store/portal',
        'store.create_checkout_session': '/store/checkout/<price_link>',
        'store.create_subscription_checkout_session': '/store/subscribe/<price_link>',
        'servers.update_server_submit': '/servers/update/<server_id>',
        'servers.delete_server': '/servers/delete/<server_id>',
        'servers.transfer_server_submit': '/servers/transfer/<server_id>/submit',
    }.items():
        app.add_url_rule(rule, endpoint, lambda **_: 'Local preview only')
    return app


class BalanceTests(unittest.TestCase):
    def test_one_and_multiple_plans(self):
        self.assertEqual(balance_estimate(100, [server()], catalog)['hours'], 720)
        estimate = balance_estimate(150, [server(), server(2, 2048)], catalog)
        self.assertEqual(estimate['hours'], 360)
        self.assertEqual(estimate['daily_cost'], 10)
        self.assertEqual(estimate['coverage_percent'], 50)

    def test_fractional_balance_is_preserved(self):
        estimate = balance_estimate(Decimal('0.75'), [server()], catalog)
        self.assertEqual(estimate['hours'], 5.4)
        self.assertEqual(estimate['balance_label'], '0.75')
        self.assertEqual(estimate['duration'], 'About 5 hours')

    def test_thresholds(self):
        for hours, state in [(0, 'empty'), (0.99, 'empty'), (1, 'critical'), (24, 'critical'),
                             (25, 'low'), (72, 'low'), (73, 'week'), (168, 'week'), (169, 'healthy')]:
            with self.subTest(hours=hours):
                # 100 credits/month, a full billing hour costs 100/720 credits.
                balance = Decimal(str(hours)) * 100 / 720
                estimate = balance_estimate(balance, [server()], catalog)
                self.assertEqual(estimate['state'], state)

    def test_free_empty_and_paused_states(self):
        for rows, state in [([], 'free'), ([server(memory=128)], 'free'),
                            ([server(suspended=True)], 'paused')]:
            with self.subTest(state=state, rows=rows):
                result = balance_estimate(0, rows, catalog)
                self.assertTrue(result['available'])
                self.assertEqual(result['state'], state)
                self.assertIsNone(result['hours'])

    def test_suspended_excluded_but_stopped_servers_still_billed(self):
        stopped = server()
        stopped['attributes']['status'] = 'offline'
        estimate = balance_estimate(100, [stopped, server(2, 2048, True)], catalog)
        self.assertEqual(estimate['hours'], 720)
        self.assertEqual(estimate['suspended_count'], 1)
        self.assertEqual(estimate['servers'][1]['monthly_cost'], 0)

    def test_unknown_or_malformed_data_never_looks_free(self):
        for rows in [None, [{}], [{'attributes': {'limits': {'memory': 1024}}}]]:
            self.assertFalse(balance_estimate(100, rows, catalog)['available'])
        for balance in [None, -1, 'NaN', 'Infinity']:
            self.assertFalse(balance_estimate(balance, [server()], catalog)['available'])
        self.assertFalse(balance_estimate(100, [server()], [])['available'])

    def test_custom_memory_uses_same_match_as_billing(self):
        custom = server()
        custom['attributes']['limits']['memory'] = 1100
        estimate = balance_estimate(100, [custom], catalog)
        self.assertEqual(estimate['monthly_cost'], match_product(custom, catalog)['price'])
        resolver = load_view('managers/credit_manager.py', 'convert_to_product',
                             {'match_product': match_product, 'products': catalog})
        self.assertEqual(resolver(custom)['price'], estimate['monthly_cost'])

    def test_estimate_matches_the_actual_hourly_debit(self):
        rows = [server(), server(2, 2048), server(3, 128), server(4, 4096, True)]
        database = Mock()
        get = Mock(side_effect=[
            SimpleNamespace(status_code=200, json=lambda: {'data': rows}),
            SimpleNamespace(status_code=200, json=lambda: {'attributes': {'email': 'alex@example.test'}}),
        ])
        debit = load_view('managers/credit_manager.py', 'use_credits', {
            'safe_requests': SimpleNamespace(get=get), 'PTERODACTYL_URL': 'https://ptero.invalid/',
            'HEADERS': {}, 'get_credits': lambda _: 100,
            'convert_to_product': lambda row: match_product(row, catalog),
            'DatabaseManager': database, 'webhook_log': Mock(),
        })
        debit()
        remaining = database.execute_query.call_args.args[1][0]
        estimate = balance_estimate(100, rows, catalog)
        self.assertAlmostEqual(100 - remaining, estimate['monthly_cost'] / 720)

    def test_large_balance_caps_only_the_meter(self):
        estimate = balance_estimate(10000, [server()], catalog)
        self.assertEqual(estimate['coverage_percent'], 100)
        self.assertEqual(estimate['hours'], 72000)
        self.assertEqual(estimate['duration'], 'About 3,000 days')

    def test_topup_combines_existing_and_purchased_credits(self):
        package = catalog[2]
        result = topup_estimates(Decimal('50.25'), [server()], catalog, [package])[package['price_link']]
        self.assertEqual(result['hours'], 1081.8)
        self.assertEqual(result['extra_duration'], 'about 30 days')
        self.assertFalse(topup_estimates(100, None, catalog, [package])[package['price_link']]['available'])

    def test_plan_change_includes_other_servers_and_first_hour(self):
        rows = [server(), server(2, 2048)]
        original = copy.deepcopy(rows)
        result = plan_estimates(100, rows, catalog, 1, [catalog[3]])['3']
        self.assertTrue(result['affordable'])
        self.assertEqual(result['monthly_cost'], 400)
        self.assertAlmostEqual(result['hours'], 179.5)
        self.assertEqual(rows, original)

    def test_unaffordable_and_free_plan_changes(self):
        result = plan_estimates(0, [server()], catalog, 1, [catalog[3], catalog[0]])
        self.assertFalse(result['3']['affordable'])
        self.assertTrue(result['0']['affordable'])
        self.assertIsNone(result['0']['hours'])
        self.assertEqual(plan_estimates(100, None, catalog, 1, catalog), {})


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.client = self.app.test_client()

    def test_actual_account_view_with_fractional_credits(self):
        self.app.config['TEST_BALANCE'] = Decimal('0.75')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('How long will my balance last?', html)
        self.assertIn('0.75', html)
        self.assertIn('data-state="critical"', html)
        self.assertNotIn('servers online', html)
        self.assertNotIn('credit_percent', html)

    def test_server_lookup_failure_is_not_an_empty_account(self):
        html = self.client.get('/?scenario=unavailable').get_data(as_text=True)
        self.assertIn('Estimate unavailable', html)
        self.assertIn('Server details are unavailable', html)
        self.assertNotIn('No servers yet', html)

    def test_states_and_safe_server_names_render(self):
        for scenario in ['free', 'empty', 'paused', 'mixed', 'escaped', 'no-packages', 'account-suspended', 'timeout']:
            with self.subTest(scenario=scenario):
                response = self.client.get(f'/?scenario={scenario}')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('<script>alert("server")</script>', response.get_data(as_text=True))

    def test_store_preview_and_checkout_work_without_javascript(self):
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Balance coverage after this top-up', html)
        self.assertIn('id="purchaseLink" href="/store/checkout/', html)
        payload = re.search(r'<script id="topup-previews" type="application/json">(.*?)</script>', html).group(1)
        previews = json.loads(payload)
        self.assertEqual(previews[catalog[2]['price_link']]['monthly_cost'], 300)

    def test_actual_server_view_renders_plan_previews(self):
        html = self.client.get('/servers/1').get_data(as_text=True)
        self.assertIn('Current balance coverage:', html)
        payload = re.search(r'<script id="plan-previews" type="application/json">(.*?)</script>', html).group(1)
        preview = json.loads(payload)['3']
        self.assertEqual(preview['monthly_cost'], 400)
        self.assertAlmostEqual(preview['hours'], 227.2)


if __name__ == '__main__':
    unittest.main()
