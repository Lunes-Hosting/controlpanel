import discord
from discord.ext import commands
from threading import Thread
from config import TOKEN, URL
from scripts import use_database, add_credits

bot = discord.Bot()

@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{URL}: dashboard"))

@bot.slash_command(name="add_credits", description="Add or remove credits from a user's account")
async def add_credits_command(ctx, email: discord.Option(str, "User's email"), amount: discord.Option(int, "Credits amount (negative to subtract)")):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    try:
        query = "SELECT credits FROM users WHERE email = %s"
        current_credits = use_database(query, (email,))
        
        if not current_credits:
            await ctx.respond(f"Error: User with email {email} not found", ephemeral=True)
            return
        
        add_credits(email, amount, set_client=False)
        new_credits = use_database(query, (email,))
        
        message = (f"{'Added' if amount > 0 else 'Removed'} {abs(amount)} credits to/from {email}.\n"
                   f"Old balance: {current_credits[0][0]} | New balance: {new_credits[0][0]}")
        await ctx.respond(message, ephemeral=True)
        
    except Exception as e:
        await ctx.respond(f"Error modifying credits: {str(e)}", ephemeral=True)

@bot.slash_command(name="info", description="Get user information")
async def info_command(ctx, email: discord.Option(str, "User's email")):
    if not ctx.author.guild_permissions.administrator:
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    try:
        query = "SELECT credits, role, pterodactyl_id, suspended FROM users WHERE email = %s"
        user_info = use_database(query, (email,))
        
        if not user_info:
            await ctx.respond(f"Error: User with email {email} not found", ephemeral=True)
            return
        
        user_info = user_info[0]
        embed = discord.Embed(title=f"User Information: {email}", color=discord.Color.blue())
        embed.add_field(name="Credits", value=user_info[0], inline=True)
        embed.add_field(name="Role", value=user_info[1], inline=True)
        embed.add_field(name="Pterodactyl ID", value=user_info[2], inline=True)
        embed.add_field(name="Status", value="Suspended" if user_info[3] else "Active", inline=True)
        
        await ctx.respond(embed=embed, ephemeral=True)
        
    except Exception as e:
        await ctx.respond(f"Error fetching user info: {str(e)}", ephemeral=True)

def run_bot():
    bot.run(TOKEN)