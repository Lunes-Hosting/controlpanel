"""Shared review decisions, ticket authorization and persistent Discord controls."""
import ast
import asyncio
import hmac
import secrets
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
from flask import Flask, Blueprint, request, session, flash, redirect, url_for, abort, current_app, render_template
from tests.test_credit_transactions import ROOT, load_functions


def load_code(path, namespace, names=None):
    tree=ast.parse((ROOT/path).read_text(encoding='utf-8'))
    tree.body=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))
               and (names is None or node.name in names)]
    exec(compile(tree,path,'exec',flags=__import__('__future__').annotations.compiler_flag),namespace)
    return namespace


class ReviewDatabase:
    def __init__(self):
        self.lock=threading.RLock()
        self.connection=sqlite3.connect(':memory:',check_same_thread=False,isolation_level=None)
        self.connection.executescript('''
            CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT,email TEXT,role TEXT);
            INSERT INTO users VALUES(1,'Pending','pending@example.com','sketchy');
            INSERT INTO users VALUES(2,'Other','other@example.com','sketchy');
            CREATE TABLE tickets(id TEXT PRIMARY KEY,user_id INTEGER);
            INSERT INTO tickets VALUES('ticket-1',1);
            INSERT INTO tickets VALUES('ticket-2',2);
            CREATE TABLE account_reviews(user_id INTEGER PRIMARY KEY,decision TEXT,reviewer TEXT,ticket_id TEXT);
            CREATE TABLE account_review_messages(message_id INTEGER PRIMARY KEY,user_id INTEGER,channel_id INTEGER,ticket_id TEXT);
        ''')
    def execute_query(self,sql,values=(),fetch_all=False):
        with self.lock:
            cursor=self.connection.execute(sql.replace('%s','?'),values)
            return cursor.fetchall() if fetch_all else cursor.fetchone()
    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute('BEGIN')
            raw=self.connection.cursor()
            cursor=SimpleNamespace(execute=lambda sql,values:raw.execute(sql.replace('%s','?').replace(' FOR UPDATE',''),values),
                                   fetchone=raw.fetchone)
            try:
                yield cursor
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            finally:raw.close()


class ReviewFixture(unittest.TestCase):
    def setUp(self):
        self.db=ReviewDatabase();self.addCleanup(self.db.connection.close)
        self.ns=load_functions('managers/account_review.py',dict(DatabaseManager=self.db))
        self.ns['ensure_review_schema']=lambda:None


class ReviewDecisionTests(ReviewFixture):
    def test_approve_promotes_only_pending_account_and_records_actor(self):
        result=self.ns['decide_account_review'](1,'approved','web:7','ticket-1')
        self.assertTrue(result['changed'])
        self.assertEqual(self.ns['get_account_review'](1)['status'],'approved')
        self.assertEqual(self.db.execute_query('SELECT role FROM users WHERE id=1'),('member',))
        self.assertEqual(self.db.execute_query('SELECT reviewer,ticket_id FROM account_reviews WHERE user_id=1'),('web:7','ticket-1'))

    def test_denial_persists_without_approving_and_cannot_be_overridden(self):
        self.ns['decide_account_review'](1,'denied','discord:7','ticket-1')
        self.assertFalse(self.ns['get_account_review'](1)['pending'])
        self.assertEqual(self.db.execute_query('SELECT role FROM users WHERE id=1'),('sketchy',))
        result=self.ns['decide_account_review'](1,'approved','web:8','ticket-1')
        self.assertFalse(result['changed']);self.assertEqual(result['status'],'denied')

    def test_concurrent_decisions_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda i:self.ns['decide_account_review'](1,'approved' if i%2 else 'denied',f'web:{i}','ticket-1'),range(8)))
        self.assertEqual(sum(result['changed'] for result in results),1)
        self.assertEqual(len({result['status'] for result in results}),1)
        self.assertEqual(self.db.execute_query('SELECT COUNT(*) FROM account_reviews'),(1,))

    def test_purchase_and_privileged_roles_are_never_downgraded(self):
        for role in ('client','member','admin','support'):
            self.db.execute_query('UPDATE users SET role=? WHERE id=1',(role,))
            result=self.ns['decide_account_review'](1,'denied','web:7','ticket-1')
            self.assertFalse(result['changed'])
            self.assertEqual(self.db.execute_query('SELECT role FROM users WHERE id=1'),(role,))
        self.assertEqual(self.db.execute_query('SELECT COUNT(*) FROM account_reviews'),(0,))

    def test_purchase_after_denial_no_longer_requires_approval(self):
        self.ns['decide_account_review'](1,'denied','web:7')
        self.db.execute_query("UPDATE users SET role='client' WHERE id=1")
        self.assertEqual(self.ns['get_account_review'](1)['status'],'purchased')

    def test_wrong_ticket_and_invalid_decision_write_nothing(self):
        with self.assertRaises(PermissionError):self.ns['decide_account_review'](1,'approved','web:7','ticket-2')
        with self.assertRaises(ValueError):self.ns['decide_account_review'](1,'admin','web:7','ticket-1')
        self.assertEqual(self.db.execute_query('SELECT COUNT(*) FROM account_reviews'),(0,))


class TicketReviewRouteTests(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.app=Flask(__name__,template_folder=str(ROOT/'templates'));self.app.secret_key='test'
        self.admin=True;self.refresh=Mock()
        self.ns.update(tickets=Blueprint('tickets',__name__),login_required=lambda f:f,
            is_admin=lambda _:self.admin,request=request,session=session,flash=flash,redirect=redirect,
            url_for=url_for,abort=abort,current_app=current_app,hmac=hmac,schedule_account_review_refresh=self.refresh,
            webhook_log=Mock())
        self.ns['tickets'].add_url_rule('/<ticket_id>',endpoint='ticket',view_func=lambda ticket_id:'Ticket')
        load_functions('Routes/Tickets.py',self.ns,{'review_ticket_account'})
        self.app.register_blueprint(self.ns['tickets'],url_prefix='/tickets')
        self.client=self.app.test_client()
        with self.client.session_transaction() as state:state.update(email='admin@example.com',account_id=7,ticket_review_token='csrf')

    def submit(self,**overrides):
        return self.client.post('/tickets/ticket-1/account-approval',data=dict(review_token='csrf',decision='approved',**overrides))

    def test_admin_review_uses_ticket_owner_and_refreshes_discord(self):
        self.assertEqual(self.submit(user_id='2').status_code,302)
        self.assertEqual(self.ns['get_account_review'](1)['status'],'approved')
        self.assertEqual(self.ns['get_account_review'](2)['status'],'pending')
        self.refresh.assert_called_once_with(1)

    def test_non_admin_and_invalid_csrf_are_rejected(self):
        self.admin=False
        self.assertEqual(self.submit().status_code,403)
        self.admin=True
        for token in ('','wrong','\u00e9'):
            self.assertEqual(self.client.post('/tickets/ticket-1/account-approval',data={'review_token':token,'decision':'approved'}).status_code,403)
        self.assertEqual(self.ns['get_account_review'](1)['status'],'pending')
        self.refresh.assert_not_called()

    def test_get_cannot_change_approval_and_missing_ticket_returns_404(self):
        self.assertEqual(self.client.get('/tickets/ticket-1/account-approval').status_code,405)
        self.assertEqual(self.client.post('/tickets/missing/account-approval',data={'review_token':'csrf','decision':'approved'}).status_code,404)

    def test_decision_survives_discord_scheduling_failure(self):
        self.refresh.side_effect=RuntimeError('bot offline')
        with self.assertLogs(self.app.logger,level='ERROR'):
            self.assertEqual(self.submit().status_code,302)
        self.assertEqual(self.ns['get_account_review'](1)['status'],'approved')

    def test_template_only_shows_buttons_for_pending_admin_context(self):
        self.app.url_build_error_handlers.append(lambda error,endpoint,values:'/unused/'+endpoint)
        for state,buttons in ((self.ns['get_account_review'](1),True),(dict(status='denied',pending=False),False),
                              (dict(status='approved',pending=False),False),(None,False)):
            with self.subTest(state=state),self.app.test_request_context('/'):
                html=render_template('ticket.html',info=dict(id='ticket-1',author='Pending',title='Help',status='open',created_at='now'),
                                     messages=[],account_review=state,review_token='csrf')
                self.assertEqual('name="decision" value="approved"' in html,buttons)
                self.assertEqual('name="decision" value="denied"' in html,buttons)


class DiscordReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=ReviewDatabase();self.addCleanup(self.db.connection.close)
        manager=load_functions('managers/account_review.py',dict(DatabaseManager=self.db))
        manager['ensure_review_schema']=lambda:None
        self.ns=load_code('discord_bot/account_approval.py',dict(manager,asyncio=asyncio,discord=discord,
            DISCORD_GUILD_ID=42,get_ticket_id=Mock(return_value='ticket-1'),logger=Mock(),
            _restored_views=False,webhook_log=Mock()))
        self.client=Mock()

    def interaction(self,guild=42,staff=True):
        return SimpleNamespace(guild=SimpleNamespace(id=guild),channel_id=100,
            user=SimpleNamespace(id=7,guild_permissions=SimpleNamespace(administrator=staff,manage_guild=False)),
            response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),client=self.client,
            message=SimpleNamespace(embeds=[],edit=AsyncMock()))

    async def test_ticket_prompt_is_pending_only_and_tracks_persistent_view(self):
        channel=SimpleNamespace(id=100,send=AsyncMock(return_value=SimpleNamespace(id=200)))
        await self.ns['post_ticket_account_review'](channel,'ticket-1')
        self.assertEqual(channel.send.call_args.kwargs['view'].user_id,1)
        self.assertEqual(channel.send.call_args.kwargs['view'].ticket_id,'ticket-1')
        self.assertEqual(self.ns['review_messages'](),[(1,100,200,'ticket-1')])
        self.ns['decide_account_review'](1,'denied','web:7')
        channel.send.reset_mock()
        await self.ns['post_ticket_account_review'](channel,'ticket-1')
        channel.send.assert_not_called()

    async def test_discord_decision_persists_and_removes_buttons(self):
        view=self.ns['AccountApprovalView'](1,'ticket-1');interaction=self.interaction()
        await view._deny(interaction)
        self.assertEqual(self.ns['get_account_review'](1)['status'],'denied')
        interaction.message.edit.assert_awaited_once()
        self.assertIsNone(interaction.message.edit.call_args.kwargs['view'])
        await view._approve(self.interaction())
        self.assertEqual(self.ns['get_account_review'](1)['status'],'denied')

    async def test_wrong_guild_non_staff_and_wrong_channel_cannot_decide(self):
        view=self.ns['AccountApprovalView'](1,'ticket-1')
        for interaction in (self.interaction(guild=99),self.interaction(staff=False)):
            await view._approve(interaction)
            interaction.response.defer.assert_not_awaited()
        self.ns['get_ticket_id'].return_value='ticket-2'
        await view._approve(self.interaction())
        self.assertEqual(self.ns['get_account_review'](1)['status'],'pending')

    async def test_web_decision_sync_removes_buttons_from_linked_discord_message(self):
        self.ns['track_review_message'](1,100,200,'ticket-1')
        self.ns['decide_account_review'](1,'approved','web:7','ticket-1')
        message=SimpleNamespace(embeds=[],edit=AsyncMock())
        self.client.get_channel.return_value=SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        await self.ns['sync_account_review_messages'](self.client,1)
        message.edit.assert_awaited_once()
        self.assertIsNone(message.edit.call_args.kwargs['view'])

    async def test_restart_restores_ticket_views_and_skips_resolved_standard_reviews(self):
        self.ns['track_review_message'](1,100,200,'ticket-1')
        self.ns['AccountApprovalView']=Mock(return_value='view')
        self.ns['restore_pending_approval_views'](self.client)
        self.client.add_view.assert_any_call('view',message_id=200)
        self.ns['AccountApprovalView'].assert_any_call(1,'ticket-1')
        await asyncio.sleep(0.02)

    async def test_open_ticket_embed_is_followed_by_approval_prompt(self):
        # The production function's lazy import resolves this injected module.
        import sys
        from unittest.mock import patch
        ns=load_code('discord_bot/ticket_sync.py',dict(discord=discord),{'send_ticket_open_message'})
        channel=SimpleNamespace(id=100,send=AsyncMock(side_effect=[SimpleNamespace(id=199),SimpleNamespace(id=200)]))
        with patch.dict(sys.modules,{'discord_bot.account_approval':SimpleNamespace(post_ticket_account_review=self.ns['post_ticket_account_review'])}):
            await ns['send_ticket_open_message'](channel,'Help','Staff','staff@example.com','Message','https://example.com/tickets/ticket-1',ticket_id='ticket-1')
        self.assertEqual(channel.send.await_count,2)
        calls=channel.send.call_args_list
        self.assertEqual(calls[0].kwargs['embed'].author.name,'New Ticket Created')
        self.assertEqual(calls[1].kwargs['embed'].title,'Free account review')
        self.assertEqual(calls[1].kwargs['embed'].description,"This free account hasn't been approved yet.")
        self.assertEqual(len(calls[1].kwargs['embed'].fields),0)
        self.assertEqual(calls[1].kwargs['view'].user_id,1) # ticket owner, not staff
        self.assertEqual([child.label for child in calls[1].kwargs['view'].children],['Approve','Deny'])


if __name__=='__main__':unittest.main()
