import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
from discord.ext.commands import cooldown, BucketType # type: ignore
from managers.database_manager import DatabaseManager
from managers.email_manager import send_email_without_app_context
from ..utils.database import UserDB
from ..utils.logger import logger
import random
import string
import importlib.util
import sys
import os
import datetime

class Linking(commands.Cog):
    def __init__(self, bot, flask_app=None):
        self.bot = bot
        self._last_member = None
        self.flask_app = flask_app
        self.codes = {}
        self.cooldowns = {}

    @slash_command(name="getcode", description="Get a linking code")
    async def getcode_command(self, ctx, email: discord.Option(str, "User's email")):
        await ctx.defer(ephemeral=True)
        try:
            # Check if user is on cooldown
            user_id = ctx.author.id
            current_time = datetime.datetime.now()
            
            if user_id in self.cooldowns:
                time_diff = current_time - self.cooldowns[user_id]
                if time_diff.total_seconds() < 300:  # 5 minutes = 300 seconds
                    remaining = 300 - int(time_diff.total_seconds())
                    minutes = remaining // 60
                    seconds = remaining % 60
                    await ctx.respond(f"Please wait {minutes} minutes and {seconds} seconds before requesting another code.", ephemeral=True)
                    return
            if isinstance(UserDB.get_user_info(email), str):
                await ctx.respond(f"{UserDB.get_user_info(email)}", ephemeral=True)
                return
            code = ''.join(random.choices(string.ascii_letters + string.digits, k=6)).lower()
            self.codes[code] = email
            
            # Load config dynamically without importing it directly
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config.py')
            spec = importlib.util.spec_from_file_location("config", config_path)
            config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config)
            
            # Create SMTP config dictionary from config values
            smtp_config = {
                'MAIL_SERVER': config.MAIL_SERVER,
                'MAIL_PORT': config.MAIL_PORT,
                'MAIL_USERNAME': config.MAIL_USERNAME,
                'MAIL_PASSWORD': config.MAIL_PASSWORD,
                'MAIL_DEFAULT_SENDER': config.MAIL_DEFAULT_SENDER,
                'MAIL_USE_TLS': True
            }
            
            # Send email without Flask app context
            send_email_without_app_context(email, "Linking Code", f"Your linking code is: {code}", smtp_config)
            
            # Set cooldown for this user
            self.cooldowns[ctx.author.id] = datetime.datetime.now()
            
            await ctx.respond("Linking code sent to your email.", ephemeral=True)
            logger.info(f"Sent linking code to {email}")
        except Exception as e:
            await ctx.respond(f"Error generating linking code: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/getcode": {str(e)}')

    @slash_command(name="link", description="Link your Discord account to your email")
    async def link_command(self, ctx, code: discord.Option(str, "Linking code")):
        await ctx.defer(ephemeral=True)
        try:
            if code not in self.codes:
                await ctx.respond("Invalid linking code.", ephemeral=True)
                return
            email = self.codes[code]
            UserDB.link_discord(email, ctx.author.id)
            await ctx.respond("Linked your Discord account to your email.", ephemeral=True)
            logger.info(f"Linked {ctx.author.id} to {email}")
            self.codes.pop(code)
        except Exception as e:
            await ctx.respond(f"Error linking account: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/link": {str(e)}')

    @slash_command(name="getuser", description="Get user info")
    async def getuser_command(self, ctx, user: discord.Option(discord.User, "User")):
        await ctx.defer(ephemeral=True)
        if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
            await ctx.respond("You do not have permission to use this command.", ephemeral=False)
            return
        try:
            info = UserDB.get_discord_user_info(user.id)
            if isinstance(info, str):
                await ctx.respond(f"{info}", ephemeral=True)
                return
            embed = discord.Embed(title="Lunes User Information", color=discord.Color.yellow())
            embed.add_field(name="Email:", value=str(info['email']), inline=False)
            embed.add_field(name="Credits:", value=info['credits'], inline=False)
            embed.add_field(name="Role:", value=info['role'], inline=False)
            embed.add_field(name="Pterodactyl ID:", value=info['pterodactyl_id'], inline=False)
            embed.add_field(name="Suspended:", value=str(str(bool(info['suspended']))), inline=False)
            embed.add_field(name="Manage", value=f"https://betadash.lunes.host/admin/user/{info['id']}/servers", inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
            logger.info(f"Fetched user info for {user.id}")
        except Exception as e:
            await ctx.respond(f"Error fetching user info: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/getuser": {str(e)}')

    
def setup(bot, flask_app=None):
    bot.add_cog(Linking(bot, flask_app))
