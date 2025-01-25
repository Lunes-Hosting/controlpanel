import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
import secrets
import random
class FunStuff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_member = None
    @slash_command(name="randomnumber", description="Generate a random number between 1 and 1000")
    async def randomnumber_command(self, ctx):
      try:  
        number = secrets.SystemRandom().randint(1, 1000)
        embed = discord.Embed(title=f"🎲 Your random number is: **{number}**", color=discord.Color.yellow())
        await ctx.respond(embed=embed, ephemeral=False)
      except Exception as e:
            await ctx.respond(f"Error generating random number: {str(e)}", ephemeral=True)

    @slash_command(name="tudou", description="Random Tudou Gif")
    async def todou_command(self, ctx):
        try:
            number = random.randint(1, 337)
            embed = discord.Embed(title="Tudou Gif", color=discord.Color.yellow())
            embed.set_image(url=f"https://raw.githubusercontent.com/deplantis/tudou/main/tudou/{number}.gif")
            await ctx.respond(embed=embed, ephemeral=False)
        except Exception as e:
            await ctx.respond(f"Error fetching tudou gif: {str(e)}", ephemeral=True)

def setup(bot):
    bot.add_cog(FunStuff(bot))
