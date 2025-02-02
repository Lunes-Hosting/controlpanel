import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
from managers.database_manager import DatabaseManager
from ..utils.database import UserDB
from ..utils.logger import logger
from flask import current_app
class Users(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_member = None

    @slash_command(name="add_credits", description="Add or remove credits from a user's account")
    async def add_credits_command(self, ctx, email: discord.Option(str, "User's email"), amount: discord.Option(int, "Credits amount (negative to subtract)")): # type: ignore
        await ctx.defer(ephemeral=False)
        if not ctx.author.guild_permissions.administrator:
            await ctx.respond("You do not have permission to use this command.", ephemeral=False)
            return
        
        try:
            # Get current credits
            current_credits = DatabaseManager.execute_query(
                "SELECT credits FROM users WHERE email = %s",
                (email,)
            )
            
            if not current_credits:
                await ctx.respond(f"Error: User with email {email} not found", ephemeral=False)
                return
            
            # Update credits
            DatabaseManager.execute_query(
                "UPDATE users SET credits = credits + %s WHERE email = %s",
                (amount, email)
            )
            
            # Get new balance
            new_credits = DatabaseManager.execute_query(
                "SELECT credits FROM users WHERE email = %s",
                (email,)
            )
            
            embed = discord.Embed(title="Lunes Credits", color=discord.Color.blue())
            embed.add_field(name=f"{'Added' if amount > 0 else 'Removed'}", value=str(f'{amount} credits to {email}.'), inline=True)
            embed.add_field(name="Old Balance:", value=str(current_credits[0]), inline=True)
            embed.add_field(name="New Balance:", value=str(new_credits[0]), inline=True)

            await ctx.respond(embed=embed, ephemeral=False)
            logger.info(f"Added {amount} credits to {email}")
        except Exception as e:
            await ctx.respond(f"Error modifying credits: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/add_credtis": {str(e)}')

    @slash_command(name="info", description="Get user information")
    async def info_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        if not ctx.author.guild_permissions.administrator:
           await ctx.respond("You do not have permission to use this command.", ephemeral=True)
           return
        try:
            if isinstance(UserDB.get_user_info(email), str):
               await ctx.respond(f"{UserDB.get_user_info(email)}", ephemeral=True)
               return 
               
            embed = discord.Embed(title="Lunes User Information", color=discord.Color.yellow())
            embed.add_field(name="Email:", value=str(email), inline=False)
            embed.add_field(name="Credits:", value=str(UserDB.get_user_info(email)['credits']), inline=False)
            embed.add_field(name="Role:", value=str(UserDB.get_user_info(email)['role']), inline=False)
            embed.add_field(name="Pterodactyl ID:", value=str(UserDB.get_user_info(email)['pterodactyl_id']), inline=False)
            embed.add_field(name="Suspended:", value=str(str(bool(UserDB.get_user_info(email)['suspended']))), inline=False)
            
            await ctx.respond(embed=embed, ephemeral=True)
            logger.info(f"Fetched user info for {email}")
        except Exception as e:
            await ctx.respond(f"Error fetching user info: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/info": {str(e)}')

    @slash_command(name="suspend", description="Suspend a user")
    async def suspend_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        with current_app.app_context():
            if not ctx.author.guild_permissions.administrator:
                await ctx.respond("You do not have permission to use this command.", ephemeral=True)
                return
            try:
                
                UserDB.suspend_user(email)
                embed = discord.Embed(title="User Suspended", color=discord.Color.red())
                embed.add_field(name="Email:", value=str(email), inline=False)
                await ctx.respond(embed=embed, ephemeral=True)
                logger.info(f"Suspended user {email}")
                return
            except Exception as e:
                await ctx.respond(f"Error suspending user: {str(e)}", ephemeral=True)
                logger.error(f'Error with discord command "/suspend": {str(e)}')

    @slash_command(name="unsuspend", description="Unsuspend a user")
    async def unsuspend_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        with current_app.app_context():
            if not ctx.author.guild_permissions.administrator:
                await ctx.respond("You do not have permission to use this command.", ephemeral=True)
                return
            try:
                
                UserDB.unsuspend_user(email)
                embed = discord.Embed(title="User Unsuspended", color=discord.Color.green())
                embed.add_field(name="Email:", value=str(email), inline=False)
                await ctx.respond(embed=embed, ephemeral=True)
                logger.info(f"Unsuspended user {email}")
                return
            except Exception as e:
                await ctx.respond(f"Error unsuspending user: {str(e)}", ephemeral=True)
                logger.error(f'Error with discord command "/unsuspend": {str(e)}')

def setup(bot):
     bot.add_cog(Users(bot))
