"""Translate ticket conversations between the opener's language and English.

What the opener writes is shown to staff in English, in Discord only. Once the opener
has written in another language, what staff write is shown in that language too.
"""

import asyncio
import re

import discord  # type: ignore

import config
from managers.ticket_discord_manager import get_ticket_id, get_ticket_language, set_ticket_language
from runtime.translation import interpret, language_name, translate_reply
from .utils.logger import logger
from .utils.permissions import is_staff_member


def _category_ids(value):
    """The configured category IDs as a set; a mistyped setting is ignored rather than stopping the bot."""
    try:
        return {int(item) for item in ([value] if isinstance(value, (int, str)) else value or ())}
    except (TypeError, ValueError):
        logger.warning('DISCORD_TICKET_CATEGORY_IDS should be a list of category IDs; ignoring %r', value)
        return set()


ENABLED = bool(getattr(config, 'TICKET_TRANSLATION_ENABLED', True))
DISCORD_GUILD_ID = getattr(config, 'DISCORD_GUILD_ID', None)
# Categories whose channels are Discord tickets even without "ticket" in a name.
DISCORD_TICKET_CATEGORY_IDS = _category_ids(getattr(config, 'DISCORD_TICKET_CATEGORY_IDS', None))

TRANSLATION_WAIT = 10  # seconds
_MENTION = re.compile(r'<@!?(\d+)>')
_openers = {}


async def _post(channel, embed, reply_to):
    # The translated text must never ping anyone a second time.
    quiet = discord.AllowedMentions.none()
    if reply_to is not None:
        try:
            await reply_to.reply(embed=embed, mention_author=False, allowed_mentions=quiet)
            return
        except discord.HTTPException:
            # The message was deleted, or the bot may not read history here: post it on its own.
            pass
    await channel.send(embed=embed, allowed_mentions=quiet)


async def show_english(channel, ticket_key, text, reply_to=None):
    """The opener wrote `text`: show staff the English version and remember the opener's language."""
    if not ENABLED:
        return
    try:
        known = await asyncio.to_thread(get_ticket_language, ticket_key)
        reading = await asyncio.to_thread(interpret, text, known)
        if reading is None:
            return
        language = language_name(reading.language)
        embed = discord.Embed(description=reading.english[:4096], color=discord.Color.blurple())
        embed.set_author(name=f'Translated from {language}')
        if reading.language != known:
            await asyncio.to_thread(set_ticket_language, ticket_key, reading.language)
            embed.set_footer(text=f'Staff replies in this ticket are now translated to {language}.')
        await _post(channel, embed, reply_to)
    except Exception:
        logger.exception('Ticket translation failed for %s', ticket_key)


async def translate_for_opener(ticket_key, text):
    """What staff wrote, in the opener's language.

    Returns (Reply, language), or None when the opener has not written in another language,
    there is nothing to translate, or no translation arrives in time.
    """
    if not ENABLED:
        return None
    try:
        language = await asyncio.to_thread(get_ticket_language, ticket_key)
        if not language:
            return None
        # A web ticket waits for this before it stores the reply, so the wait is kept short.
        reply = await asyncio.wait_for(asyncio.to_thread(translate_reply, text, language), TRANSLATION_WAIT)
    except asyncio.TimeoutError:
        logger.warning('Ticket %s: the translation took too long; the reply stays in English', ticket_key)
        return None
    except Exception:
        logger.exception('Ticket translation failed for %s', ticket_key)
        return None
    return (reply, language) if reply else None


def web_reply(reply):
    """How a translated staff reply reads on the website."""
    return f'[{reply.label}] {reply.text}'


async def show_translation(channel, reply, language, reply_to=None, web=False):
    """Post the translation of a staff reply in the ticket's Discord channel."""
    embed = discord.Embed(description=reply.text[:4096], color=discord.Color.blurple())
    if web:
        # Only staff see a web ticket's channel; this is what the opener reads on the website.
        embed.set_author(name=f'Sent to the web ticket in {language_name(language)}')
    else:
        embed.set_footer(text=reply.label)
    try:
        await _post(channel, embed, reply_to)
    except Exception:
        logger.exception('Could not post a translation in channel %s', getattr(channel, 'id', None))


def is_discord_ticket(channel):
    """A private channel or thread made by the Discord ticket system."""
    guild = getattr(channel, 'guild', None)
    if guild is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return False
    parent = channel.parent if isinstance(channel, discord.Thread) else None
    category = getattr(parent or channel, 'category', None)
    names = ' '.join(str(getattr(item, 'name', '') or '') for item in (channel, parent, category)).lower()
    if 'ticket' not in names and getattr(category, 'id', None) not in DISCORD_TICKET_CATEGORY_IDS:
        return False
    # Channels everyone can read, such as the one with the "open a ticket" button, are not tickets.
    if isinstance(channel, discord.Thread) and channel.is_private():
        return True
    return not (parent or channel).permissions_for(guild.default_role).view_channel


async def _opener_id(channel):
    """Who the ticket belongs to: the first person the ticket bot greets in the channel."""
    if channel.id not in _openers:
        opener = 0
        try:
            async for first in channel.history(limit=1, oldest_first=True):
                if not first.author.bot:
                    break
                greeting = ' '.join([first.content or ''] + [embed.description or '' for embed in first.embeds])
                for user_id in _MENTION.findall(greeting):
                    member = channel.guild.get_member(int(user_id))
                    if member is not None and not member.bot:
                        opener = member.id
                        break
        except discord.HTTPException:
            # Without permission to read the channel's history, the staff role alone decides.
            pass
        if len(_openers) >= 2000:
            _openers.clear()
        _openers[channel.id] = opener
    return _openers[channel.id]


async def translate_discord_ticket_message(message):
    """Translate a message sent in a ticket that was opened in Discord."""
    if not ENABLED or not DISCORD_GUILD_ID or message.author.bot:
        return
    if message.guild is None or message.guild.id != int(DISCORD_GUILD_ID):
        return
    channel = message.channel
    text = (message.content or '').strip()
    if not text or not is_discord_ticket(channel):
        return
    try:
        # Channels linked to a web ticket are translated by the ticket bridge.
        if await asyncio.to_thread(get_ticket_id, channel.id):
            return
        # A staff member who opens a ticket is that ticket's opener, not its staff.
        staff = is_staff_member(message.author) and message.author.id != await _opener_id(channel)
    except Exception:
        logger.exception('Ticket translation failed in channel %s', channel.id)
        return
    if staff:
        translated = await translate_for_opener(str(channel.id), text)
        if translated:
            await show_translation(channel, *translated, reply_to=message)
    else:
        await show_english(channel, str(channel.id), text, reply_to=message)
