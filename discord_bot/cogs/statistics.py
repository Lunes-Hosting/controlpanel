import discord # type: ignore
from discord.commands import slash_command # type: ignore
from discord.ext import commands # type: ignore
from managers.database_manager import DatabaseManager
from ..utils.ptero import PteroAPI
from ..utils.database import UserDB
from ..utils.logger import logger
from managers.credit_manager import convert_to_product
from managers.utils import HEADERS
from config import PTERODACTYL_URL
from security import safe_requests

class Statistics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_member = None

    @slash_command(name="stats", description="Show total servers and users")
    async def trigger_command(self, ctx):
        await ctx.defer(ephemeral=False)
        if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
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

    @slash_command(name="economy_stats", description="Show economy stats like the admin page")
    async def economy_stats(self, ctx):
        await ctx.defer(ephemeral=True)
        if not (ctx.author.guild_permissions.administrator or discord.utils.get(ctx.author.roles, id=1364999900135165993)):
            await ctx.respond("You do not have permission to use this command.", ephemeral=True)
            return
        try:
            # Totals from DB
            total_users = DatabaseManager.execute_query("SELECT COUNT(*) FROM users")
            total_users = int(total_users[0]) if total_users else 0

            total_clients = DatabaseManager.execute_query("SELECT COUNT(*) FROM users WHERE role = 'client'")
            total_clients = int(total_clients[0]) if total_clients else 0

            total_tickets = DatabaseManager.execute_query("SELECT COUNT(*) FROM tickets")
            total_tickets = int(total_tickets[0]) if total_tickets else 0

            total_ticket_messages = DatabaseManager.execute_query("SELECT COUNT(*) FROM ticket_comments")
            total_ticket_messages = int(total_ticket_messages[0]) if total_ticket_messages else 0

            credits_circ = DatabaseManager.execute_query("SELECT COALESCE(SUM(credits), 0) FROM users WHERE role != 'admin'")
            total_credits_circulation = float(credits_circ[0]) if credits_circ else 0.0

            # Servers from Pterodactyl (mirror admin stats logic)
            servers_resp = safe_requests.get(
                f"{PTERODACTYL_URL}api/application/servers?per_page=100000",
                headers=HEADERS,
                timeout=60,
            )
            total_servers = 0
            free_servers = 0
            paid_servers = 0
            plan_counts = {}
            total_monthly_credits_used = 0.0
            if servers_resp.status_code == 200:
                servers_json = servers_resp.json()
                data = servers_json.get("data", [])
                total_servers = len(data)
                for s in data:
                    try:
                        product = convert_to_product(s)
                        price = float(product.get("price", 0) or 0)
                        name = product.get("name", "Unknown")
                        if price == 0:
                            free_servers += 1
                        else:
                            paid_servers += 1
                        total_monthly_credits_used += price
                        plan_counts[name] = plan_counts.get(name, 0) + 1
                    except Exception:
                        plan_counts["Unknown"] = plan_counts.get("Unknown", 0) + 1
            # Build embed
            embed = discord.Embed(title="Lunes Economy Stats", color=discord.Color.green())
            embed.add_field(name="Total Users", value=str(total_users), inline=True)
            embed.add_field(name="Clients", value=str(total_clients), inline=True)
            embed.add_field(name="Credits in Circulation", value=f"{total_credits_circulation:.2f}", inline=False)
            embed.add_field(name="Servers (Total / Free / Paid)", value=f"{total_servers} / {free_servers} / {paid_servers}", inline=False)
            embed.add_field(name="Tickets", value=str(total_tickets), inline=True)
            embed.add_field(name="Ticket Messages", value=str(total_ticket_messages), inline=True)
            embed.add_field(name="Monthly Credits Used (sum of plan prices)", value=f"{total_monthly_credits_used:.2f}", inline=False)
            # Compact plan breakdown
            if plan_counts:
                breakdown = ", ".join([f"{k}: {v}" for k, v in plan_counts.items()])
                embed.add_field(name="Plan Breakdown", value=breakdown[:1024], inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception as e:
            await ctx.respond(f"Error fetching economy stats: {str(e)}", ephemeral=True)
            logger.error(f'Error with discord command "/economy_stats": {str(e)}')

def setup(bot):
    bot.add_cog(Statistics(bot))
