import asyncio
from typing import Optional

from discord_bot.bot import bot
from discord_bot.ticket_sync import (
    create_discord_ticket_channel,
    delete_discord_ticket_channel,
    send_discord_ticket_message,
)
from discord_bot.utils.logger import logger


def _get_bot_loop() -> Optional[asyncio.AbstractEventLoop]:
    loop = getattr(bot, "loop", None)
    if loop and loop.is_running():
        return loop
    logger.warning("Discord ticket bridge: bot loop not running; skipping schedule")
    return None


def schedule_ticket_channel_creation(
    ticket_id: int,
    title: str,
    opener_name: str,
    opener_email: str,
    initial_message: str,
    ticket_url: str,
) -> None:
    loop = _get_bot_loop()
    if not loop:
        return
    logger.debug("Scheduling Discord ticket channel creation for ticket %s", ticket_id)
    asyncio.run_coroutine_threadsafe(
        create_discord_ticket_channel(
            bot,
            ticket_id,
            title,
            opener_name,
            opener_email,
            initial_message,
            ticket_url,
        ),
        loop,
    )


def schedule_ticket_message(
    ticket_id: int,
    author_name: str,
    author_email: str,
    message: str,
    origin: str = "Web Panel",
) -> None:
    loop = _get_bot_loop()
    if not loop:
        return
    logger.debug("Scheduling Discord ticket message sync for ticket %s", ticket_id)
    asyncio.run_coroutine_threadsafe(
        send_discord_ticket_message(
            bot,
            ticket_id,
            author_name,
            author_email,
            message,
            origin,
        ),
        loop,
    )


def schedule_ticket_channel_deletion(ticket_id: int, reason: str) -> None:
    loop = _get_bot_loop()
    if not loop:
        return
    logger.debug("Scheduling Discord ticket channel deletion for ticket %s", ticket_id)
    asyncio.run_coroutine_threadsafe(
        delete_discord_ticket_channel(bot, ticket_id, reason),
        loop,
    )
