import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
from managers.database_manager import DatabaseManager
from ..utils.database import UserDB
from ..utils.logger import logger


def _find_account(email):
    email = (email or "").strip()
    if "@" not in email:
        return email, None, "That doesn't look like an email address. Please enter the account's email, not the username."
    row = DatabaseManager.execute_query("SELECT suspended FROM users WHERE email = %s", (email,))
    if not row:
        return email, None, f"No account found with the email `{email}`. Please double-check it."
    return email, bool(row[0]), None


class Users(commands.Cog):
    def __init__(self, bot, flask_app=None):
        self.bot = bot
        self._last_member = None
        self.flask_app = flask_app

    @slash_command(name="add_credits", description="Add or remove credits from a user's account")
    async def add_credits_command(self, ctx, email: discord.Option(str, "User's email"), amount: discord.Option(int, "Credits amount (negative to subtract)")): # type: ignore
        await ctx.defer(ephemeral=False)
        if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
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
        if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
            await ctx.respond("You do not have permission to use this command.", ephemeral=True)
            return
        try:
            info = UserDB.get_user_info(email)
            if isinstance(info, str):
                await ctx.respond(f"{info}", ephemeral=True)
                return
            embed = discord.Embed(title="Lunes User Information", color=discord.Color.yellow())
            embed.add_field(name="Email:", value=str(email), inline=False)
            embed.add_field(name="Credits:", value=info['credits'], inline=False)
            embed.add_field(name="Role:", value=info['role'], inline=False)
            embed.add_field(name="Pterodactyl ID:", value=info['pterodactyl_id'], inline=False)
            embed.add_field(name="Suspended:", value=str(str(bool(info['suspended']))), inline=False)
            embed.add_field(name="Manage", value=f"https://betadash.lunes.host/admin/user/{info['id']}/servers", inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
            logger.info(f"Fetched user info for {email}")
        except Exception as e:
            await ctx.respond(f"Error fetching user info: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/info": {str(e)}')

    @slash_command(name="suspend", description="Suspend a user")
    async def suspend_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        if self.flask_app is None:
            await ctx.respond("Error: Flask app not initialized", ephemeral=True)
            return
        with self.flask_app.app_context():
            if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
                await ctx.respond("You do not have permission to use this command.", ephemeral=True)
                return
            try:
                email, is_suspended, problem = _find_account(email)
                if problem:
                    await ctx.respond(problem, ephemeral=True)
                    return
                if is_suspended:
                    await ctx.respond(f"`{email}` is already suspended.", ephemeral=True)
                    return
                UserDB.suspend_user(email, actor=f"{ctx.author} ({ctx.author.id})")
                embed = discord.Embed(title="User Suspended", color=discord.Color.red())
                embed.add_field(name="Email:", value=str(email), inline=False)
                await ctx.respond(embed=embed, ephemeral=True)
                logger.info(f"Suspended user {email}")
                return
            except Exception as e:
                await ctx.respond(f"Error suspending user: {str(e)}", ephemeral=True)
                logger.error(f'Error with discord command "/suspend": {str(e)}')

    @slash_command(name="giveclient", description="Make a user a client.")
    async def giveclient_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        if self.flask_app is None:
            await ctx.respond("Error: Flask app not initialized", ephemeral=True)
            return
        with self.flask_app.app_context():
            if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
                await ctx.respond("You do not have permission to use this command.", ephemeral=True)
                return
            email = (email or "").strip()
            if "@" not in email:
                await ctx.respond(
                    "That doesn't look like an email address. Please enter the account's email, not the username.",
                    ephemeral=True,
                )
                return
            try:
                user_row = DatabaseManager.execute_query(
                    "SELECT role FROM users WHERE email = %s",
                    (email,)
                )
                if not user_row:
                    await ctx.respond(
                        f"No account found with the email `{email}`. Please double-check it.",
                        ephemeral=True,
                    )
                    return
                if user_row[0] in ("admin", "support"):
                    await ctx.respond(
                        f"`{email}` is a staff account ({user_row[0]}). Not changing it to client.",
                        ephemeral=True,
                    )
                    return
                if user_row[0] == "client":
                    await ctx.respond(f"`{email}` is already a client.", ephemeral=True)
                    return
                DatabaseManager.execute_query(
                    "UPDATE users SET role = 'client' WHERE email = %s",
                    (email,)
                )
                # Get new role
                new_role = DatabaseManager.execute_query(
                    "SELECT role FROM users WHERE email = %s",
                    (email,)
                )
                embed = discord.Embed(title="Client Role Given", color=discord.Color.green())
                embed.add_field(name="Email:", value=str(email), inline=False)
                await ctx.respond(embed=embed, ephemeral=True)
                logger.info(f"Client role given to {email}. Current role: {new_role}")
                return
            except Exception as e:
                await ctx.respond(f"Error giving client role to user: {str(e)}", ephemeral=True)
                logger.error(f'Error with discord command "/giveclient": {str(e)}')

    @slash_command(name="unsuspend", description="Unsuspend a user")
    async def unsuspend_command(self, ctx, email: discord.Option(str, "User's email")): # type: ignore
        if self.flask_app is None:
            await ctx.respond("Error: Flask app not initialized", ephemeral=True)
            return
        with self.flask_app.app_context():
            if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
                await ctx.respond("You do not have permission to use this command.", ephemeral=True)
                return
            try:
                email, is_suspended, problem = _find_account(email)
                if problem:
                    await ctx.respond(problem, ephemeral=True)
                    return
                if not is_suspended:
                    await ctx.respond(f"`{email}` isn't suspended, so there's nothing to unsuspend.", ephemeral=True)
                    return
                previous_unbans = UserDB.get_unban_history(email)
                UserDB.unsuspend_user(email, actor=f"{ctx.author} ({ctx.author.id})")
                embed = discord.Embed(title="User Unsuspended", color=discord.Color.green())
                embed.add_field(name="Email:", value=str(email), inline=False)
                if previous_unbans:
                    embed.add_field(name="Heads up:", value=previous_unbans, inline=False)
                await ctx.respond(embed=embed, ephemeral=True)
                logger.info(f"Unsuspended user {email}")
                return
            except Exception as e:
                await ctx.respond(f"Error unsuspending user: {str(e)}", ephemeral=True)
                logger.error(f'Error with discord command "/unsuspend": {str(e)}')

def setup(bot, flask_app=None):
    bot.add_cog(Users(bot, flask_app))
