"""Integration regressions discovered while reviewing the local changes."""

import subprocess
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from flask import Flask, Blueprint, request, session, flash, redirect, url_for
from test_credit_transactions import load_functions
from request_security import login_credentials, peer_ip, login_limit_key


class ReviewTests(unittest.TestCase):
    def test_login_preserves_safe_destination_across_session_rotation(self):
        for destination, expected in (('/tickets/example', '/tickets/example'),
                                      ('//example.invalid', '/')):
            app = Flask(__name__)
            app.secret_key = 'test'
            user = Blueprint('user', __name__)
            user.add_url_rule('/', endpoint='index', view_func=lambda: 'Dashboard')
            def establish(email, account):
                session.clear()
                session['email'] = email
            ns = dict(user=user, request=request, session=session, flash=flash,
                      limiter=SimpleNamespace(limit=lambda *a, **kw: lambda f: f),
                      login_credentials=login_credentials,
                      peer_ip=peer_ip, login_limit_key=login_limit_key,
                      redirect=redirect, url_for=url_for, DEBUG_FRONTEND_MODE=True,
                      RECAPTCHA_SECRET_KEY='test', RECAPTCHA_SITE_KEY='test',
                      _get_client_ip=lambda _: '192.0.2.1', login=lambda *a: (7,),
                      establish_session=establish, render_template=lambda *a, **kw: 'Login',
                      is_safe_url=lambda value: value.startswith('/') and not value.startswith('//'))
            load_functions('Routes/AuthenticationHandler.py', ns, {'login_user'})
            app.register_blueprint(user)
            client = app.test_client()
            with client.session_transaction() as state:
                state.update(next=destination, pay_id='old-payment')
            response = client.post('/login', data={'email': 'user@example.com', 'password': 'test'})
            self.assertEqual(response.location, expected)
            with client.session_transaction() as state:
                self.assertNotIn('pay_id', state)
                self.assertNotIn('next', state)

    def test_deleted_creation_reservation_is_released(self):
        database = SimpleNamespace(execute_query=Mock())
        ns = load_functions('managers/server_creation_lock.py', {'DatabaseManager': database},
                            {'release_deleted_server'})
        ns['release_deleted_server']('123')
        sql, params = database.execute_query.call_args.args
        self.assertIn("state='deleted'", sql)
        self.assertIn("state='pending'", sql)
        self.assertEqual(params, (123,))

    def test_owner_delete_checks_remote_success(self):
        app = Flask(__name__)
        app.secret_key = 'test'
        remote_delete = Mock()
        ns = dict(servers=SimpleNamespace(route=lambda *a, **kw: lambda f: f),
                  login_required=lambda f: f, session=session,
                  safe_requests=SimpleNamespace(get=lambda *a, **kw: Mock(json=lambda: {'attributes': {'user': 7}})),
                  DatabaseManager=SimpleNamespace(execute_query=lambda *a: (7,)),
                  PTERODACTYL_URL='https://example.invalid/', HEADERS={},
                  webhook_log=Mock(), delete_managed_server=remote_delete,
                  redirect=redirect, url_for=lambda *a: '/')
        load_functions('Routes/Servers.py', ns, {'delete_server'})
        for success in (False, True):
            remote_delete.return_value = success
            with app.test_request_context('/'):
                session['email'] = 'user@example.com'
                response = ns['delete_server']('123')
                self.assertEqual(response.status_code if success else response[1], 302 if success else 502)
            remote_delete.assert_called_with(123)

    def test_real_manager_imports_and_discord_command_registration(self):
        # Import complete production modules (including real py-cord decorators)
        # in a clean interpreter. Block every network call and stub only external
        # configuration, DB access, product data and the API-backed cache.
        script = r'''
import ast, asyncio, importlib, pathlib, sys, types
from unittest.mock import Mock, patch
root = pathlib.Path.cwd()
config = types.ModuleType('config')
names = set()
for path in root.rglob('*.py'):
    if any(part.startswith('.') for part in path.relative_to(root).parts): continue
    try: tree = ast.parse(path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeError): continue
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'config':
            names.update(n.name for n in node.names if n.name != '*')
for name in names: setattr(config, name, 'test')
config.DISCORD_GUILD_ID = 123
config.ALT_ENFORCE = False
config.ALT_DETECTION_ENABLED = False
sys.modules['config'] = config
products = types.ModuleType('products'); products.products = []
sys.modules['products'] = products
cache = types.ModuleType('pterocache'); cache.PteroCache = Mock
sys.modules['pterocache'] = cache
database = types.ModuleType('managers.database_manager')
def query(sql, *args, **kwargs):
    if 'DATA_TYPE' in sql: return ('varchar', 36)
    if 'SELECT EXTRA' in sql: return ('auto_increment',)
    return [] if kwargs.get('fetch_all') else None
database.DatabaseManager = type('DatabaseManager', (), {'execute_query': staticmethod(query)})
sys.modules['managers.database_manager'] = database
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
with patch('socket.socket.connect', side_effect=AssertionError('Unexpected network access')):
    import managers
    for name in ('account_sessions', 'payment_manager', 'server_creation_lock',
                 'suspension_manager', 'suspension_notifications', 'ticket_ids', 'ticket_discord_manager',
                 'checkout_manager', 'invoice_manager'):
        importlib.import_module('managers.' + name)
    from managers.server_manager import delete_server
    database.DatabaseManager.execute_query = Mock(side_effect=query)
    with patch('requests.delete', return_value=Mock(status_code=500)):
        assert delete_server(123) is False
    database.DatabaseManager.execute_query.assert_not_called()
    with patch('requests.delete', return_value=Mock(status_code=204)):
        assert delete_server(123) is True
    sql, params = database.DatabaseManager.execute_query.call_args.args
    assert "state='deleted'" in sql and params == (123,)
    from discord_bot.cogs.users import Users
    from discord_bot.cogs.linking import Linking
    import discord
    bot = discord.Bot()
    bot.add_cog(Users(bot))
    bot.add_cog(Linking(bot))
    commands = {command.name for command in bot.pending_application_commands}
    assert {'check_reason', 'info', 'getuser'} <= commands, commands
loop.close()
'''
        result = subprocess.run([sys.executable, '-c', textwrap.dedent(script)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
