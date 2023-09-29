import discord
from discord.ext import commands
import configexample


if (configexample.ENABLE_BOT):
    bot = discord.Bot()

    @bot.event
    async def on_ready():
        print(f"{bot.user} is ready and online!")
        await bot.change_presence(activity=discord.Activity(name=configexample.HOST, type=3))


    bot.run("MTAzNDU3OTMwOTE0MDExOTYzMg.Gtap4R.k3dFQ8JTyEEJTGJttK3vwqehFM-J1DexBuNSsw")