from discord.ext import commands # type: ignore
import sys
import os
import asyncio
import discord # type: ignore
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import *
from .utils.logger import logger
bot = commands.Bot(command_prefix="!")
bot = discord.Bot()

@bot.event
async def on_ready():
    logger.info(f'Logged into Discord Bot: {bot.user}')
    
    
    async def change_presence():
        while True:
            await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Lunes Hosting"))
            await asyncio.sleep(60)  

    asyncio.create_task(change_presence())

@bot.command(description="Sends the bot's latency.") # this decorator makes a slash command
async def ping(ctx): # a slash command will be created with the name "ping"
    await ctx.respond(f"Pong! Latency is {round(bot.latency * 1000)}ms")


async def run_bot():
    await bot.start(TOKEN)