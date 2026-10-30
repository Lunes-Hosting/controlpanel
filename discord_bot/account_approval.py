"""Discord review workflow for non-Gmail free accounts."""

import asyncio
import threading
from typing import Optional

import discord  # type: ignore

from config import ACCOUNT_APPROVAL_CHANNEL_ID, DISCORD_GUILD_ID
from managers.account_review import (get_account_review, decide_account_review,
                                     ensure_review_schema, track_review_message, review_messages)
from managers.ticket_discord_manager import get_ticket_id
from managers.database_manager import DatabaseManager
from managers.logging import webhook_log
from .utils.logger import logger


_bot: Optional[discord.Bot] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_reviews = []
_pending_lock = threading.Lock()
_restored_views = False


class AccountApprovalView(discord.ui.View):
    """Persistent account controls, optionally bound to a linked ticket channel."""

    def __init__(self, user_id: int, ticket_id=None):
        super().__init__(timeout=None)
        self.user_id = int(user_id)
        self.ticket_id = str(ticket_id) if ticket_id is not None else None
        suffix = f"{self.user_id}:{self.ticket_id}" if self.ticket_id else str(self.user_id)
        for label, action, style, callback in (
            ('Approve', 'approve', discord.ButtonStyle.success, self._approve),
            ('Deny', 'deny', discord.ButtonStyle.danger, self._deny)):
            button = discord.ui.Button(label=label, style=style, custom_id=f"account-approval:{action}:{suffix}")
            button.callback = callback
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        guild = getattr(interaction, 'guild', None)
        permissions = getattr(interaction.user, 'guild_permissions', None)
        if (not guild or not DISCORD_GUILD_ID or guild.id != int(DISCORD_GUILD_ID)
                or not permissions or not (permissions.administrator or permissions.manage_guild)):
            await interaction.response.send_message('Only authorized server managers can review accounts here.', ephemeral=True)
            return False
        if self.ticket_id:
            linked = await asyncio.to_thread(get_ticket_id, interaction.channel_id)
            if linked != self.ticket_id:
                await interaction.response.send_message('This review does not belong to this ticket channel.', ephemeral=True)
                return False
        return True

    async def _approve(self, interaction: discord.Interaction):
        await self._decide(interaction, 'approved')

    async def _deny(self, interaction: discord.Interaction):
        await self._decide(interaction, 'denied')

    async def _decide(self, interaction, decision):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        try:
            state = await asyncio.to_thread(decide_account_review, self.user_id, decision,
                f'discord:{interaction.user.id}', self.ticket_id)
        except Exception:
            logger.exception('Account review failed for account %s', self.user_id)
            await interaction.followup.send('Could not save the review. Please try again.', ephemeral=True)
            return
        # A stale button always reflects the saved result and cannot override it.
        await _edit_review_message(interaction.message, state, ticket=bool(self.ticket_id))
        if state['changed']:
            try:
                await asyncio.to_thread(webhook_log,
                    f"Account ID {self.user_id} {decision} by {state['reviewer']}", database_log=True)
            except Exception:
                logger.exception('Could not mirror the saved account review to the activity log')
        await sync_account_review_messages(interaction.client, self.user_id)


def _status_text(state):
    return {'pending': 'Pending review', 'approved': 'Approved',
            'denied': 'Denied - free server creation remains blocked',
            'purchased': 'Already approved by purchase',
            'not_required': 'Manual approval is not required'}.get(state['status'], 'Unavailable')


def _ticket_review_embed(status='pending'):
    description = {
        'pending': "This free account hasn't been approved yet.",
        'approved': 'This free account has been approved.',
        'denied': 'Free server approval was denied.',
    }.get(status, 'This account no longer needs free server approval.')
    color = (discord.Color.orange() if status == 'pending' else
             discord.Color.red() if status == 'denied' else discord.Color.green())
    return discord.Embed(title='Free account review', description=description, color=color)


async def _edit_review_message(message, state, ticket=False):
    if message is None:
        return
    if ticket:
        await message.edit(embed=_ticket_review_embed(state['status']), view=None)
        return
    embed = discord.Embed.from_dict(message.embeds[0].to_dict()) if message.embeds else _review_embed(
        state['user_id'], state['name'], state['email'])
    embed.color = (discord.Color.red() if state['status'] == 'denied' else discord.Color.green())
    for index, field in enumerate(embed.fields):
        if field.name == 'Status':
            embed.set_field_at(index, name='Status', value=_status_text(state), inline=False)
            break
    else:
        embed.add_field(name='Status', value=_status_text(state), inline=False)
    if state.get('reviewer'):
        embed.set_footer(text=f"Reviewed by {state['reviewer']}")
    await message.edit(embed=embed, view=None)


async def sync_account_review_messages(bot, user_id=None):
    try:
        rows = await asyncio.to_thread(review_messages, user_id)
    except Exception:
        logger.exception('Could not load account review messages')
        return
    for owner, channel_id, message_id, ticket_id in rows:
        try:
            state = await asyncio.to_thread(get_account_review, owner)
            if not state or state['pending']:
                continue
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
            await _edit_review_message(message, state, ticket=ticket_id is not None)
        except discord.NotFound:
            await asyncio.to_thread(DatabaseManager.execute_query,
                'DELETE FROM account_review_messages WHERE message_id=%s', (message_id,))
        except Exception:
            logger.exception('Could not refresh account review message %s', message_id)


async def post_ticket_account_review(channel, ticket_id):
    # Resolve the ticket owner by ID: lazy channel creation may be triggered by a
    # staff reply, whose author/email must never become the account being reviewed.
    row = await asyncio.to_thread(DatabaseManager.execute_query,
        'SELECT user_id FROM tickets WHERE id=%s', (ticket_id,))
    if not row:
        return
    state = await asyncio.to_thread(get_account_review, row[0])
    if not state or not state['pending']:
        return
    message = await channel.send(embed=_ticket_review_embed(),
                                 view=AccountApprovalView(state['user_id'],ticket_id))
    await asyncio.to_thread(track_review_message,state['user_id'],channel.id,message.id,ticket_id)
    # Handle a decision made on the web while Discord was sending the message.
    latest = await asyncio.to_thread(get_account_review, state['user_id'])
    if latest and not latest['pending']:
        await _edit_review_message(message, latest, ticket=True)


def _email_domain(email):
    return str(email).rsplit("@", 1)[-1].lower()


def _review_embed(
    user_id,
    name,
    email,
    status="Pending review",
    color=None,
    reason=None,
    alt_ip=None,
    main_account_info=None,
):
    embed = discord.Embed(
        title="Free account review",
        description=(
            "This free account must be manually approved before it can create a server. "
            "A successful credit purchase automatically changes the role to client."
        ),
        color=color or discord.Color.orange(),
    )
    embed.add_field(name="Account ID", value=str(user_id), inline=True)
    embed.add_field(name="Username", value=str(name), inline=True)
    embed.add_field(name="Full Email", value=str(email), inline=True)
    if alt_ip:
        embed.add_field(name="Signup IP", value=str(alt_ip), inline=True)
    if main_account_info:
        main_summary = (
            f"ID: {main_account_info.get('id', 'N/A')}\n"
            f"Username: {main_account_info.get('name', 'N/A')}\n"
            f"Full Email: {main_account_info.get('email', 'N/A')}\n"
            f"Main IP: {main_account_info.get('ip', 'N/A')}"
        )
        embed.add_field(name="Matched Main Account", value=main_summary, inline=False)
    if reason:
        embed.add_field(name="Review reason", value=str(reason), inline=False)
    embed.add_field(name="Status", value=status, inline=False)
    return embed


async def _post_account_review(user_id, name, email, reason, alt_ip=None, main_account_info=None):
    if _bot is None:
        return

    channel_id = int(ACCOUNT_APPROVAL_CHANNEL_ID)
    channel = _bot.get_channel(channel_id)
    if channel is None:
        channel = await _bot.fetch_channel(channel_id)

    state = await asyncio.to_thread(get_account_review, user_id)
    if not state or not state["pending"]:
        return
    message = await channel.send(
        embed=_review_embed(
            user_id,
            name,
            email,
            reason=reason,
            alt_ip=alt_ip,
            main_account_info=main_account_info,
        ),
        view=AccountApprovalView(user_id),
    )
    await asyncio.to_thread(track_review_message,user_id,channel.id,message.id)
    latest = await asyncio.to_thread(get_account_review, user_id)
    if latest and not latest["pending"]:
        await _edit_review_message(message, latest)


def queue_account_review(
    user_id: int,
    name: str,
    email: str = "",
    reason: str = "Non-Gmail email domain",
    alt_ip: Optional[str] = None,
    main_account_info: Optional[dict] = None,
    email_domain: Optional[str] = None,
):
    """Queue a Discord review from a Flask request thread."""
    resolved_email = email or email_domain or ""
    review = (int(user_id), name, resolved_email, reason, alt_ip, main_account_info)
    with _pending_lock:
        if _bot_loop is None or _bot is None or not _bot_loop.is_running():
            _pending_reviews.append(review)
            return
        future = asyncio.run_coroutine_threadsafe(_post_account_review(*review), _bot_loop)

    def _log_failure(done_future):
        try:
            done_future.result()
        except Exception:
            logger.exception("Failed to post account review for account ID %s", user_id)

    future.add_done_callback(_log_failure)


def set_approval_bot(loop: asyncio.AbstractEventLoop, bot: discord.Bot):
    """Attach the running Discord bot and flush reviews queued during startup."""
    global _bot, _bot_loop
    _bot = bot
    _bot_loop = loop

    with _pending_lock:
        queued = list(_pending_reviews)
        _pending_reviews.clear()

    for review in queued:
        loop.create_task(_post_account_review(*review))


def restore_pending_approval_views(bot: discord.Bot):
    """Restore persistent button handlers after a bot restart."""
    global _restored_views
    if _restored_views:
        return

    ensure_review_schema()
    pending = DatabaseManager.execute_query(
        "SELECT u.id FROM users u LEFT JOIN account_reviews r ON r.user_id=u.id "
        "WHERE u.role='sketchy' AND r.user_id IS NULL", fetch_all=True) or []
    for row in pending:
        bot.add_view(AccountApprovalView(row[0]))
    for user_id, _, message_id, ticket_id in review_messages():
        if ticket_id:
            bot.add_view(AccountApprovalView(user_id,ticket_id), message_id=message_id)
    _restored_views = True
    asyncio.get_running_loop().create_task(sync_account_review_messages(bot))
