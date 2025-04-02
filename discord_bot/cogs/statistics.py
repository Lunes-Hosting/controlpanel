import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
from managers.database_manager import DatabaseManager
from ..utils.ptero import PteroAPI
from ..utils.database import UserDB
from ..utils.logger import logger
class Statistics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_member = None

    @slash_command(name="stats", description="Show total servers and users")
    async def trigger_command(self, ctx):
        await ctx.defer(ephemeral=False)
        if not ctx.author.guild_permissions.administrator:
            await ctx.respond("You do not have permission to use this command.", ephemeral=True)
            return

        try:

            embed = discord.Embed(title="Lunes Statistics", color=discord.Color.blue())
            embed.add_field(name="Total Users", value=str(UserDB.get_all_users()), inline=True)
            embed.add_field(name="Suspended Users", value=str(UserDB.get_suspended_users()), inline=False)

            embed.add_field(name="Total Servers", value=str(PteroAPI.get_all_servers()['servers']), inline=False)


            await ctx.respond(embed=embed, ephemeral=True)
            
            channel = self.bot.get_channel(1284260369925279744)
            if channel:
                await channel.send(embed=embed)
            
        except Exception as e:
            await ctx.respond(f"Error fetching statistics: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/stats": {str(e)}')


def setup(bot):
    bot.add_cog(Statistics(bot))
