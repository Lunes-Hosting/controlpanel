from __future__ import annotations

import datetime
from typing import Optional

import discord  # type: ignore

from config import (
    DISCORD_GUILD_ID,
    TICKET_DISCORD_CATEGORY_ID,
    MAIL_SERVER,
    MAIL_PORT,
    MAIL_USERNAME,
    MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER,
)
from managers.database_manager import DatabaseManager
from managers.email_manager import send_email_without_app_context
from managers.ticket_discord_manager import (
    clear_channel,
    get_channel_id,
    get_ticket_id,
    set_channel,
)

try:
    from config import MAIL_USE_TLS  # type: ignore
except Exception:
    MAIL_USE_TLS = True

STAFF_REPLY_EMAIL = "dwatnip123@gmail.com"


async def ensure_category(guild: discord.Guild, category_id: int) -> Optional[discord.CategoryChannel]:
    channel = guild.get_channel(category_id)
    if isinstance(channel, discord.CategoryChannel):
        return channel
    for cat in guild.categories:
        if cat.id == category_id:
            return cat
    return None


async def create_discord_ticket_channel(
    bot: discord.Client,
    ticket_id: int,
    title: str,
    opener_name: str,
    opener_email: str,
    initial_message: str,
    ticket_url: str,
) -> None:
    guild = bot.get_guild(int(DISCORD_GUILD_ID))
    if guild is None:
        return
    category = await ensure_category(guild, int(TICKET_DISCORD_CATEGORY_ID))
    if category is None:
        return

    existing_channel_id = get_channel_id(ticket_id)
    if existing_channel_id:
        channel = guild.get_channel(existing_channel_id)
        if channel is not None:
            await send_ticket_open_message(
                channel,
                opener_name,
                opener_email,
                initial_message,
                ticket_url,
            )
            return

    channel = await guild.create_text_channel(
        name=f"ticket-{ticket_id}",
        category=category,
        topic=f"Ticket #{ticket_id}: {title}",
    )
    set_channel(ticket_id, channel.id)
    await send_ticket_open_message(
        channel,
        opener_name,
        opener_email,
        initial_message,
        ticket_url,
    )


async def send_ticket_open_message(
    channel: discord.TextChannel,
    opener_name: str,
    opener_email: str,
    message: str,
    ticket_url: str,
) -> None:
    embed = discord.Embed(
        title="New Ticket Created",
        description=message,
        color=discord.Color.blue(),
    )
    embed.add_field(name="Opened By", value=f"{opener_name} ({opener_email})", inline=False)
    embed.add_field(name="Ticket Link", value=ticket_url, inline=False)
    await channel.send(embed=embed)


async def send_discord_ticket_message(
    bot: discord.Client,
    ticket_id: int,
    author_name: str,
    author_email: str,
    message: str,
    origin: str,
) -> None:
    guild = bot.get_guild(int(DISCORD_GUILD_ID))
    if guild is None:
        return
    channel_id = get_channel_id(ticket_id)
    if not channel_id:
        # If no channel, create one now
        title_row = DatabaseManager.execute_query(
            "SELECT title FROM tickets WHERE id = %s",
            (ticket_id,),
        )
        if not title_row:
            return
        await create_discord_ticket_channel(
            bot,
            ticket_id,
            title_row[0],
            author_name,
            author_email,
            message,
            f"https://betadash.lunes.host/tickets/{ticket_id}",
        )
        return

    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        title="New Ticket Reply",
        description=message,
        color=discord.Color.green(),
    )
    embed.add_field(name="Author", value=f"{author_name} ({author_email})", inline=False)
    embed.add_field(name="Origin", value=origin, inline=False)
    await channel.send(embed=embed)


async def delete_discord_ticket_channel(bot: discord.Client, ticket_id: int, reason: str) -> None:
    guild = bot.get_guild(int(DISCORD_GUILD_ID))
    if guild is None:
        return
    channel_id = get_channel_id(ticket_id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.delete(reason=reason)
        except Exception:
            pass
    clear_channel(ticket_id)


async def process_discord_message(
    bot: discord.Client,
    channel_id: int,
    author: str,
    author_email: str,
    content: str,
) -> None:
    ticket_id = get_ticket_id(channel_id)
    if not ticket_id:
        return
    owner_email = _get_ticket_owner_email(ticket_id)
    staff_id = _get_staff_user_id(author_email)
    if not owner_email or not staff_id:
        return

    comment_id = _get_next_comment_id()
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    DatabaseManager.execute_query(
        "INSERT INTO ticket_comments (id, ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, %s, %s)",
        (
            comment_id,
            ticket_id,
            staff_id,
            content,
            timestamp,
        ),
    )

    DatabaseManager.execute_query(
        "UPDATE tickets SET reply_status = 'responded', last_reply = NOW() WHERE id = %s",
        (ticket_id,),
    )

    smtp_config = _get_smtp_config()
    if smtp_config:
        send_email_without_app_context(
            owner_email,
            "Support Ticket Update",
            f"{author} replied to your ticket:\n{content}\n\nPlease visit https://betadash.lunes.host/tickets/{ticket_id}",
            smtp_config,
        )


def _get_smtp_config():
    if not all([MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER]):
        return None
    return {
        'MAIL_SERVER': MAIL_SERVER,
        'MAIL_PORT': MAIL_PORT,
        'MAIL_USERNAME': MAIL_USERNAME,
        'MAIL_PASSWORD': MAIL_PASSWORD,
        'MAIL_DEFAULT_SENDER': MAIL_DEFAULT_SENDER,
        'MAIL_USE_TLS': MAIL_USE_TLS,
    }


def _get_ticket_owner_email(ticket_id: int) -> Optional[str]:
    row = DatabaseManager.execute_query(
        "SELECT users.email FROM tickets JOIN users ON tickets.user_id = users.id WHERE tickets.id = %s",
        (ticket_id,),
    )
    if row:
        return row[0]
    return None


def _get_staff_user_id(author_email: str) -> Optional[int]:
    lookup_email = author_email or STAFF_REPLY_EMAIL
    row = DatabaseManager.execute_query(
        "SELECT id FROM users WHERE email = %s",
        (lookup_email,),
    )
    if row:
        return row[0]
    return None


def _get_next_comment_id() -> int:
    row = DatabaseManager.execute_query(
        "SELECT id FROM ticket_comments ORDER BY id DESC LIMIT 0, 1",
    )
    if not row:
        return 0
    return row[0] + 1
