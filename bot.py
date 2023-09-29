import discord
from discord.ext import commands
from config import *


if (ENABLE_BOT):
    bot = discord.Bot()

    @bot.event
    async def on_ready():
        print(f"{bot.user} is ready and online!")
        await bot.change_presence(activity=discord.Activity(name=HOST, type=3))


    bot.run(BOT_ID)