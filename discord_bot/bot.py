import sys
import os
import asyncio
import discord # type: ignore

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import *  # noqa: F401,F403
from .utils.logger import logger
from discord_bot.ticket_bridge import set_bot_loop
from discord_bot.ticket_sync import process_discord_message
from managers.database_manager import DatabaseManager
from managers.ticket_discord_manager import (
    clear_channel,
    get_ticket_id,
)
from managers.logging import webhook_log

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = discord.Bot(intents=intents)

@bot.event
async def on_ready():
    set_bot_loop(asyncio.get_running_loop(), bot)
    logger.info(f'Logged into Discord Bot: {bot.user}')
    
    

    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Lunes Hosting")) 

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    await process_discord_message(bot, message)
    if hasattr(bot, "process_commands"):
        await bot.process_commands(message)


@bot.command(description="Sends the bot's latency.") # this decorator makes a slash command
async def ping(ctx): # a slash command will be created with the name "ping"
    await ctx.respond(f"Pong! Latency is {bot.latency}ms")


@bot.command(description="Close a linked web ticket and delete this channel.")
async def closewebticket(ctx):
    if not ctx.guild or ctx.guild.id != int(DISCORD_GUILD_ID):
        await ctx.respond("This command can only be used inside the Lunes guild.", ephemeral=True)
        return

    member = ctx.author
    if isinstance(member, discord.Member):
        role_ids = [role.id for role in member.roles]
        if 1364999900135165993 not in role_ids:
            await ctx.respond("You do not have permission to use this command.", ephemeral=True)
            return
    else:
        await ctx.respond("Unable to determine your roles.", ephemeral=True)
        return

    channel = ctx.channel
    if not isinstance(channel, discord.TextChannel):
        await ctx.respond("This command must be used inside a ticket text channel.", ephemeral=True)
        return

    ticket_id = get_ticket_id(channel.id)
    if not ticket_id:
        await ctx.respond("This channel is not linked to a web ticket.", ephemeral=True)
        return

    try:
        DatabaseManager.execute_query(
            "UPDATE tickets SET status = 'closed', reply_status = 'responded', last_reply = NOW() WHERE id = %s",
            (ticket_id,),
        )
        DatabaseManager.execute_query(
            "INSERT INTO ticket_comments (ticket_id, user_id, ticketcomment, created_at) VALUES (%s, %s, %s, NOW())",
            (
                ticket_id,
                _get_ticket_owner_id(ticket_id),
                f"Ticket closed by Discord staff member {ctx.author.display_name}.",
            ),
        )
    except Exception as exc:
        logger.exception("Failed to close ticket %s from Discord", ticket_id)
        await ctx.respond("Failed to close the ticket. Please check logs.", ephemeral=True)
        return

    webhook_log(
        f"Ticket #{ticket_id} closed via Discord by {ctx.author.display_name}",
        1,
        is_ticket=True,
    )

    await ctx.respond(f"Ticket #{ticket_id} closed. This channel will be deleted shortly.", ephemeral=True)

    clear_channel(ticket_id)
    try:
        await channel.delete(reason=f"Ticket #{ticket_id} closed via Discord")
    except Exception as exc:
        logger.warning("Failed to delete channel %s: %s", channel.id, exc)


async def run_bot():
    await bot.start(TOKEN)


def _get_ticket_owner_id(ticket_id: int) -> int:
    row = DatabaseManager.execute_query(
        "SELECT user_id FROM tickets WHERE id = %s",
        (ticket_id,),
    )
    if row:
        return row[0]
    return 0
