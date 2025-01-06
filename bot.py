import discord
from discord.ext import commands
from threading import Thread
from config import TOKEN, URL, PTERODACTYL_URL
import requests
import secrets
import random
from managers.database_manager import DatabaseManager

bot = discord.Bot()

@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{URL}: dashboard"))

@bot.slash_command(name="add_credits", description="Add or remove credits from a user's account")
async def add_credits_command(ctx, email: discord.Option(str, "User's email"), amount: discord.Option(int, "Credits amount (negative to subtract)")):
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
        
        message = (f"{'Added' if amount > 0 else 'Removed'} {abs(amount)} credits to/from {email}.\n"
                   f"Old balance: {current_credits[0]} | New balance: {new_credits[0]}")
        await ctx.respond(message, ephemeral=False)
        
    except Exception as e:
        await ctx.respond(f"Error modifying credits: {str(e)}", ephemeral=False)

@bot.slash_command(name="info", description="Get user information")
async def info_command(ctx, email: discord.Option(str, "User's email")):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    
    try:
        user_info = DatabaseManager.execute_query(
            "SELECT credits, role, pterodactyl_id, suspended FROM users WHERE email = %s",
            (email,)
        )
        
        if not user_info:
            await ctx.respond(f"Error: User with email {email} not found", ephemeral=True)
            return
        
        embed = discord.Embed(title=f"User Information: {email}", color=discord.Color.blue())
        embed.add_field(name="Credits", value=user_info[0], inline=True)
        embed.add_field(name="Role", value=user_info[1], inline=True)
        embed.add_field(name="Pterodactyl ID", value=user_info[2], inline=True)
        embed.add_field(name="Status", value="Suspended" if user_info[3] else "Active", inline=True)
        
        await ctx.respond(embed=embed, ephemeral=True)
        
    except Exception as e:
        await ctx.respond(f"Error fetching user info: {str(e)}", ephemeral=True)

@bot.slash_command(name="randomnumber", description="Generate a random number between 1 and 1000")
async def randomnumber_command(ctx):
    number = secrets.SystemRandom().randint(1, 1000)
    await ctx.respond(f"🎲 Your random number is: **{number}**", ephemeral=False)

@bot.slash_command(name="tudou", description="Sends a random gif of tudou")
async def tudou_command(ctx):
    number = random.randint(1, 337)
    await ctx.respond(f"https://raw.githubusercontent.com/deplantis/tudou/main/tudou/{number}.gif", ephemeral=False)

@bot.slash_command(name="trigger", description="Show total servers and users")
async def trigger_command(ctx):
    await ctx.defer(ephemeral=True)
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return

    try:
        # Get total users from database
        total_users = DatabaseManager.execute_query("SELECT COUNT(*) FROM users")[0]
        
        # Get total servers from Pterodactyl
        headers = {
            'Authorization': f'Bearer {TOKEN}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        resp = requests.get(f"{PTERODACTYL_URL}api/application/servers?per_page=100000", headers=headers)
        total_servers = len(resp.json()['data'])
        
        # Create embed response
        embed = discord.Embed(title="System Statistics", color=discord.Color.blue())
        embed.add_field(name="Total Users", value=str(total_users), inline=True)
        embed.add_field(name="Total Servers", value=str(total_servers), inline=True)
        
        # Send ephemeral response to user
        await ctx.respond(embed=embed, ephemeral=True)
        
        # Send to specific channel
        channel = bot.get_channel(1284260369925279744)
        if channel:
            await channel.send(embed=embed)
        
    except Exception as e:
        await ctx.respond(f"Error fetching statistics: {str(e)}", ephemeral=True)

async def run_bot():
    await bot.start(TOKEN)
