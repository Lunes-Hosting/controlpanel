from discord.ext import commands # type: ignore
import sys
import os
import asyncio
import discord # type: ignore

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import *  # noqa: F401,F403
from .utils.logger import logger
from discord_bot.ticket_bridge import set_bot_loop
from discord_bot.ticket_sync import process_discord_message

bot = commands.Bot(command_prefix="!")
bot = discord.Bot()

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


async def run_bot():
    await bot.start(TOKEN)
