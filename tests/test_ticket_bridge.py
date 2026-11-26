"""Web ticket replies keep reaching Discord after a ticket channel was deleted there."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from tests.test_ticket_account_approval import load_code


class TicketReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.send = AsyncMock()
        self.guild = Mock()
        self.guild.get_channel.return_value = self.channel
        self.bot = Mock()
        self.bot.get_guild.return_value = self.guild
        self.channels = {'ticket-1': 111}
        self.create = AsyncMock()
        self.order = []
        self.translate = AsyncMock(side_effect=lambda *args: self.order.append('translated'))
        self.ns = load_code('discord_bot/ticket_sync.py', dict(
            discord=discord, DISCORD_GUILD_ID=1, TICKET_DISCORD_CATEGORY_ID=2, logger=Mock(),
            get_channel_id=self.channels.get,
            _apply_ticket_channel_status=AsyncMock(side_effect=lambda *args: self.order.append('renamed')),
            _translate_web_message=self.translate,
            DatabaseManager=SimpleNamespace(execute_query=lambda query, values: ('Server offline',)),
        ), {'send_discord_ticket_message', '_discord_ids_configured'})
        self.ns['create_discord_ticket_channel'] = self.create

    async def reply(self):
        await self.ns['send_discord_ticket_message'](
            self.bot, 'ticket-1', 'User', 'user@example.com', 'Any update?', 'Panel User')

    async def test_reply_is_posted_in_the_ticket_channel(self):
        await self.reply()
        self.guild.get_channel.assert_called_once_with(111)
        self.assertEqual(self.channel.send.await_args.kwargs['embed'].description, 'Any update?')
        self.create.assert_not_awaited()
        self.translate.assert_awaited_once_with(self.channel, 'ticket-1', 'user@example.com', 'Any update?')
        # Discord can hold a rename back for minutes, so the translation does not wait behind it.
        self.assertEqual(self.order, ['translated', 'renamed'])

    async def test_reply_recreates_a_channel_that_was_deleted_in_discord(self):
        self.guild.get_channel.return_value = None
        await self.reply()
        self.create.assert_awaited_once_with(
            self.bot, 'ticket-1', 'Server offline', 'User', 'user@example.com', 'Any update?',
            'https://betadash.lunes.host/tickets/ticket-1')

    async def test_first_reply_without_a_channel_still_creates_one(self):
        self.channels.clear()
        await self.reply()
        self.guild.get_channel.assert_not_called()
        self.create.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
