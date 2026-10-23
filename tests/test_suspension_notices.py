"""Reason visibility, notification deduplication/retry, and Discord authorization."""

import ast
import asyncio
import __future__
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, closing
import hashlib
from html import escape
import json
from pathlib import Path
import smtplib
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from uuid import uuid4
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, Blueprint, request, session, flash, redirect, url_for
from jinja2 import Environment, FileSystemLoader, select_autoescape
from tests.test_credit_transactions import load_functions
from runtime.request_security import login_credentials, peer_ip, login_limit_key

ROOT = Path(__file__).resolve().parents[1]


def load_class(path, name, ns):
    node = next(n for n in ast.parse((ROOT / path).read_text(encoding='utf-8')).body
                if isinstance(n, ast.ClassDef) and n.name == name)
    node.bases = []
    for method in node.body:
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec',
                 flags=__future__.annotations.compiler_flag), ns)
    return ns[name]


class LoginReasonTests(unittest.TestCase):
    def test_reason_requires_correct_credentials_and_is_shown_for_both_suspension_results(self):
        for result in ('suspended', 'alt_suspended', None):
            with self.subTest(result=result):
                app = Flask(__name__)
                app.secret_key = 'test'
                user = Blueprint('user', __name__)
                user.add_url_rule('/', endpoint='index', view_func=lambda: 'Dashboard')
                reason = Mock(return_value='Repeated abuse report')
                ns = dict(user=user, request=request, session=session, flash=flash,
                          limiter=SimpleNamespace(limit=lambda *a, **kw: lambda f: f),
                          login_credentials=login_credentials, verify_turnstile=lambda *a: True,
                          peer_ip=peer_ip, login_limit_key=login_limit_key,
                          redirect=redirect, url_for=url_for, DEBUG_FRONTEND_MODE=False,
                          requests=SimpleNamespace(post=lambda *a, **kw: Mock(json=lambda: {'success': True})),
                          RECAPTCHA_SECRET_KEY='test', RECAPTCHA_SITE_KEY='test',
                          _get_client_ip=lambda _: '192.0.2.1', login=lambda *a: result,
                          get_id=lambda _: (7,), get_user_suspension=reason,
                          json=json, render_template=Mock(return_value='Login'))
                load_functions('Routes/AuthenticationHandler.py', ns, {'login_user'})
                app.register_blueprint(user)
                client = app.test_client()
                self.assertEqual(client.post('/login', data={'email': 'user@example.com', 'password': 'test', 'g-recaptcha-response': 'test'}).status_code, 302)
                with client.session_transaction() as state:
                    text = ' '.join(message for _, message in state.get('_flashes', []))
                if result:
                    self.assertIn('Repeated abuse report', text)
                    reason.assert_called_once_with(7)
                else:
                    self.assertNotIn('Repeated abuse report', text)
                    reason.assert_not_called()


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'outbox.sqlite')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE suspension_email_outbox (id TEXT PRIMARY KEY, dedup_key TEXT UNIQUE, entity_type TEXT, entity_id INTEGER, recipient TEXT, subject TEXT, body TEXT)')
        self.ns = load_functions('managers/suspension_notifications.py', {
            'hashlib': hashlib, 'uuid4': uuid4,
        })

    @contextmanager
    def cursor(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            cursor = db.cursor()
            class Adapter:
                def execute(self, sql, params):
                    cursor.execute(sql.replace('INSERT IGNORE', 'INSERT OR IGNORE').replace('%s', '?'), params)
            yield Adapter()
            db.commit()

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT recipient,subject,body FROM suspension_email_outbox').fetchall()

    def test_repeated_suspension_queues_one_email_and_unsuspend_allows_new_episode(self):
        def queue(_):
            with self.cursor() as cursor:
                self.ns['queue_suspension_email'](cursor, 'server', 1, 'user@example.com', 'Insufficient credits', label='Example', source='billing')
        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(queue, range(6)))
        self.assertEqual(len(self.rows()), 1)
        self.assertIn('Example', self.rows()[0][2])
        self.assertIn('Reason: Insufficient credits', self.rows()[0][2])
        with self.cursor() as cursor:
            self.ns['clear_suspension_email_keys'](cursor, 'server', 1)
        queue(0)
        self.assertEqual(len(self.rows()), 2)

    def test_account_and_inactivity_notifications_include_the_saved_reason(self):
        with self.cursor() as cursor:
            self.ns['queue_suspension_email'](cursor, 'user', 7, 'user@example.com', 'Abuse report')
            self.ns['queue_suspension_email'](cursor, 'server', 1, 'user@example.com', 'Owner inactive', source='inactivity')
        self.assertIn('Reason: Abuse report', self.rows()[0][2])
        self.assertIn('17 days', self.rows()[1][2])

    def test_delivery_acknowledges_success_and_requeues_failure(self):
        for fail in (False, True):
            cursor = Mock()
            cursor.fetchone.side_effect = [('event', 'user@example.com', 'Account suspended', 'Reason: Abuse'), None]
            @contextmanager
            def transaction(): yield cursor
            database = SimpleNamespace(transaction=transaction, execute_query=Mock())
            ns = load_functions('managers/suspension_notifications.py', {
                'DatabaseManager': database, '_delivery_lock': threading.Lock(),
            })
            sender = Mock(side_effect=RuntimeError('SMTP unavailable') if fail else None)
            ns['deliver_pending_suspension_emails'](sender=sender)
            sender.assert_called_once_with('user@example.com', 'Account suspended', 'Reason: Abuse')
            sql = database.execute_query.call_args.args[0]
            self.assertIn("status='pending'" if fail else "status='sent'", sql)

    def test_smtp_sender_escapes_reason_in_html_and_reports_delivery_errors(self):
        smtp = MagicMock()
        connection = smtp.SMTP.return_value.__enter__.return_value
        config = SimpleNamespace(MAIL_DEFAULT_SENDER=('Lunes', 'sender@example.com'), MAIL_SERVER='smtp.example.com',
                                 MAIL_PORT=587, MAIL_USERNAME='sender', MAIL_PASSWORD='test', MAIL_USE_TLS=True)
        ns = load_functions('managers/email_manager.py', {
            'MIMEMultipart': MIMEMultipart, 'MIMEText': MIMEText, 'smtplib': smtp, 'escape': escape,
        }, {'send_suspension_email', '_plain_email_body', '_render_email_html'})
        with patch.dict(sys.modules, {'config': config}):
            ns['send_suspension_email']('user@example.com', 'Account suspended', 'Reason: <script>test</script>')
            message = connection.send_message.call_args.args[0]
            html = message.get_payload()[1].get_payload(decode=True).decode()
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            connection.starttls.assert_called_once()
            connection.send_message.side_effect = smtplib.SMTPException('Unavailable')
            with self.assertRaises(smtplib.SMTPException):
                ns['send_suspension_email']('user@example.com', 'Account suspended', 'Reason: Abuse')


class DiscordReasonTests(unittest.TestCase):
    def setUp(self):
        ns = load_functions('discord_bot/utils/permissions.py', {'DISCORD_GUILD_ID': 123})
        self.reason = 'Repeated abuse report'
        self.info = {'id': 7, 'email': 'user@example.com', 'credits': 10, 'role': 'member',
                     'pterodactyl_id': 101, 'suspended': True, 'suspension_reason': self.reason}
        self.userdb = SimpleNamespace(get_user_info=Mock(return_value=self.info),
                                      get_discord_user_info=Mock(return_value=self.info))
        class Embed:
            def __init__(self, **kwargs): self.fields = {}
            def add_field(self, name, value, **kwargs): self.fields[name] = value
        ns.update(discord=SimpleNamespace(Embed=Embed, Color=Mock()), UserDB=self.userdb, logger=Mock())
        self.users = load_class('discord_bot/cogs/users.py', 'Users', ns)(Mock())
        self.linking = load_class('discord_bot/cogs/linking.py', 'Linking', ns)(Mock())

    def context(self, guild=123, admin=True):
        return SimpleNamespace(guild=SimpleNamespace(id=guild),
                               author=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=admin), roles=[]),
                               respond=AsyncMock(), defer=AsyncMock())

    def test_profile_commands_and_check_reason_include_status_and_reason(self):
        for method, argument in [(self.users.info_command, 'user@example.com'),
                                 (self.linking.getuser_command, SimpleNamespace(id=77)),
                                 (self.users.check_reason_command, 'user@example.com')]:
            ctx = self.context()
            asyncio.run(method(ctx, argument))
            fields = ctx.respond.call_args.kwargs['embed'].fields
            self.assertIn(self.reason, fields.values())
            self.assertTrue(ctx.respond.call_args.kwargs['ephemeral'])

    def test_foreign_guild_admin_and_nonstaff_cannot_query_reasons(self):
        for guild, admin in [(999, True), (123, False)]:
            ctx = self.context(guild, admin)
            asyncio.run(self.users.check_reason_command(ctx, 'user@example.com'))
        self.userdb.get_user_info.assert_not_called()

    def test_active_account_reports_not_suspended(self):
        self.info.update(suspended=False, suspension_reason=None)
        ctx = self.context()
        asyncio.run(self.users.check_reason_command(ctx, 'user@example.com'))
        fields = ctx.respond.call_args.kwargs['embed'].fields
        self.assertEqual(fields['Suspended'], 'No')
        self.assertEqual(fields['Reason'], 'Not suspended')


class ServerBannerTests(unittest.TestCase):
    def test_suspension_banner_is_conditional_and_reason_is_escaped(self):
        env = Environment(loader=FileSystemLoader(ROOT / 'templates'), autoescape=select_autoescape())
        env.globals.update(url_for=lambda *a, **kw: '/', get_flashed_messages=lambda **kw: [],
                           session={}, request=SimpleNamespace(endpoint='servers.server'))
        info = {'attributes': {'id': 1, 'name': 'Example', 'suspended': True, 'limits': {'memory': 512}}}
        context = dict(info=info, verified=False, nodes=[], suspension_reason='<script>abuse</script>')
        html = env.get_template('server.html').render(**context)
        self.assertIn('This server is suspended', html)
        self.assertIn('&lt;script&gt;abuse&lt;/script&gt;', html)
        self.assertNotIn('<script>abuse</script>', html)
        info['attributes']['suspended'] = False
        self.assertNotIn('This server is suspended', env.get_template('server.html').render(**context))


if __name__ == '__main__': unittest.main()
