import discord
from discord.ext import commands
from config import *

bot = discord.Bot()

@bot.event 
async def on_ready(): #on_ready, turn on print and print it.
    print(f"{bot.user} is ready and online!")
    await bot.change_presence(activity=discord.Activity(name=URL, type=3))

def enable_bot():
    id = TOKEN
    bot.run(id) #id should be string