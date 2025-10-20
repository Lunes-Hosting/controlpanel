import asyncio
from typing import List, Optional, Tuple, Dict, Any

from discord_bot.bot import bot
from discord_bot.ticket_sync import (
    create_discord_ticket_channel,
    delete_discord_ticket_channel,
    send_discord_ticket_message,
)
from discord_bot.utils.logger import logger


_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_pending_tasks: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]] = []


def set_bot_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _bot_loop
    _bot_loop = loop
    if not _pending_tasks:
        return
    logger.info("Discord ticket bridge: processing %s queued task(s)", len(_pending_tasks))
    for task_type, args, kwargs in list(_pending_tasks):
        _submit_task(task_type, args, kwargs)
    _pending_tasks.clear()


def _get_bot_loop() -> Optional[asyncio.AbstractEventLoop]:
    if _bot_loop and _bot_loop.is_running():
        return _bot_loop
    loop = getattr(bot, "loop", None)
    if loop and loop.is_running():
        return loop
    return None


def _queue_task(task_type: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
    logger.warning("Discord ticket bridge: bot loop not running; queueing %s task", task_type)
    _pending_tasks.append((task_type, args, kwargs))


def _submit_task(task_type: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
    loop = _get_bot_loop()
    if not loop:
        _queue_task(task_type, args, kwargs)
        return
    if task_type == "create":
        coro = create_discord_ticket_channel(bot, *args, **kwargs)
    elif task_type == "message":
        coro = send_discord_ticket_message(bot, *args, **kwargs)
    elif task_type == "delete":
        coro = delete_discord_ticket_channel(bot, *args, **kwargs)
    else:
        logger.error("Discord ticket bridge: unknown task type %s", task_type)
        return
    asyncio.run_coroutine_threadsafe(coro, loop)


def schedule_ticket_channel_creation(
    ticket_id: int,
    title: str,
    opener_name: str,
    opener_email: str,
    initial_message: str,
    ticket_url: str,
) -> None:
    logger.debug("Scheduling Discord ticket channel creation for ticket %s", ticket_id)
    _submit_task(
        "create",
        (ticket_id, title, opener_name, opener_email, initial_message, ticket_url),
        {},
    )


def schedule_ticket_message(
    ticket_id: int,
    author_name: str,
    author_email: str,
    message: str,
    origin: str = "Web Panel",
) -> None:
    logger.debug("Scheduling Discord ticket message sync for ticket %s", ticket_id)
    _submit_task(
        "message",
        (ticket_id, author_name, author_email, message, origin),
        {},
    )


def schedule_ticket_channel_deletion(ticket_id: int, reason: str) -> None:
    logger.debug("Scheduling Discord ticket channel deletion for ticket %s", ticket_id)
    _submit_task("delete", (ticket_id, reason), {})
