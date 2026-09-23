"""Discord review workflow for non-Gmail free accounts."""

import asyncio
import threading
from typing import Optional

import discord  # type: ignore

from config import ACCOUNT_APPROVAL_CHANNEL_ID
from managers.database_manager import DatabaseManager
from managers.logging import webhook_log
from .utils.logger import logger


_bot: Optional[discord.Bot] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_reviews = []
_pending_lock = threading.Lock()
_restored_views = False


class AccountApprovalView(discord.ui.View):
    """Persistent green approve/red deny controls for one account."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = int(user_id)

        approve_button = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"account-approval:approve:{self.user_id}",
        )
        deny_button = discord.ui.Button(
            label="Deny",
            style=discord.ButtonStyle.danger,
            custom_id=f"account-approval:deny:{self.user_id}",
        )
        approve_button.callback = self._approve
        deny_button.callback = self._deny
        self.add_item(approve_button)
        self.add_item(deny_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not permissions or not (permissions.administrator or permissions.manage_guild):
            await interaction.response.send_message(
                "You need Manage Server permission to review accounts.",
                ephemeral=True,
            )
            return False
        return True

    def _disable_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    def _get_account(self):
        return DatabaseManager.execute_query(
            "SELECT name, email, role FROM users WHERE id = %s",
            (self.user_id,),
        )

    async def _approve(self, interaction: discord.Interaction):
        account = self._get_account()
        if not account:
            await interaction.response.send_message("Account no longer exists.", ephemeral=True)
            return

        name, email, role = account
        if role == "sketchy":
            DatabaseManager.execute_query(
                "UPDATE users SET role = 'member' WHERE id = %s AND role = 'sketchy'",
                (self.user_id,),
            )
            status = "Approved"
            color = discord.Color.green()
            webhook_log(
                f"Account ID {self.user_id} approved by Discord staff member {interaction.user}",
                database_log=True,
            )
        elif role == "client":
            status = "Already approved by purchase"
            color = discord.Color.green()
        else:
            status = f"Already resolved ({role})"
            color = discord.Color.light_grey()

        self._disable_buttons()
        embed = _review_embed(
            self.user_id,
            name,
            _email_domain(email),
            status,
            color,
        )
        embed.set_footer(text=f"Reviewed by {interaction.user}")
        await interaction.response.edit_message(embed=embed, view=self)

    async def _deny(self, interaction: discord.Interaction):
        account = self._get_account()
        if not account:
            await interaction.response.send_message("Account no longer exists.", ephemeral=True)
            return

        name, email, role = account
        if role == "sketchy":
            status = "Denied - account remains blocked from server creation"
            color = discord.Color.red()
            webhook_log(
                f"Account ID {self.user_id} denied by Discord staff member {interaction.user}",
                database_log=True,
            )
        elif role == "client":
            status = "Not denied - account has purchased credits"
            color = discord.Color.gold()
        else:
            status = f"Already resolved ({role})"
            color = discord.Color.light_grey()

        self._disable_buttons()
        embed = _review_embed(
            self.user_id,
            name,
            _email_domain(email),
            status,
            color,
        )
        embed.set_footer(text=f"Reviewed by {interaction.user}")
        await interaction.response.edit_message(embed=embed, view=self)


def _email_domain(email):
    return str(email).rsplit("@", 1)[-1].lower()


def _review_embed(user_id, name, email_domain, status="Pending review", color=None, reason=None):
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
    embed.add_field(name="Email domain", value=str(email_domain), inline=False)
    if reason:
        embed.add_field(name="Review reason", value=str(reason), inline=False)
    embed.add_field(name="Status", value=status, inline=False)
    return embed


async def _post_account_review(user_id, name, email_domain, reason):
    if _bot is None:
        return

    channel_id = int(ACCOUNT_APPROVAL_CHANNEL_ID)
    channel = _bot.get_channel(channel_id)
    if channel is None:
        channel = await _bot.fetch_channel(channel_id)

    await channel.send(
        embed=_review_embed(user_id, name, email_domain, reason=reason),
        view=AccountApprovalView(user_id),
    )


def queue_account_review(user_id: int, name: str, email_domain: str, reason: str = "Non-Gmail email domain"):
    """Queue a Discord review from a Flask request thread."""
    review = (int(user_id), name, email_domain, reason)
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

    pending = DatabaseManager.execute_query(
        "SELECT id FROM users WHERE role = 'sketchy'",
        fetch_all=True,
    ) or []
    for row in pending:
        bot.add_view(AccountApprovalView(row[0]))
    _restored_views = True
