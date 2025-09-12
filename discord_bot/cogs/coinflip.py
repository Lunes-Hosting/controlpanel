import discord  # type: ignore
from discord.ext import commands  # type: ignore
from discord.commands import slash_command  # type: ignore
import random

from ..utils.database import UserDB
from managers.credit_manager import add_credits, remove_credits
from ..utils.logger import logger


class Coinflip(commands.Cog):
    def __init__(self, bot, flask_app=None):
        self.bot = bot
        self.flask_app = flask_app

    @slash_command(name="coinflip", description="Flip a coin and gamble credits")
    async def coinflip(self, ctx: discord.ApplicationContext,
                       credits: discord.Option(int, "Bet amount (credits)"),  # type: ignore
                       side: discord.Option(str, "Choose heads or tails", choices=["heads", "tails"])):  # type: ignore
        await ctx.defer(ephemeral=False)
        # Verify user is linked and not suspended
        info = UserDB.get_discord_user_info(ctx.author.id)
        if isinstance(info, str):
            await ctx.respond("You must be linked to play. Use /getcode and /link to link your account.", ephemeral=True)
            return
        if info.get("suspended"):
            await ctx.respond("Your account is suspended and cannot gamble.", ephemeral=True)
            return

        email = info.get("email")
        current_credits = float(info.get("credits", 0))

        # Validate bet
        if credits is None or credits <= 0:
            await ctx.respond("Please enter a valid positive credit amount to bet.", ephemeral=True)
            return
        if current_credits < credits:
            await ctx.respond(f"Insufficient credits. You have {current_credits}.", ephemeral=True)
            return

        user_choice = side.lower()
        result = random.choice(["heads", "tails"])  # fair 50/50

        win = user_choice == result

        # Settle credits
        try:
            if win:
                add_credits(email, int(credits), set_client=False)
                outcome_text = f"You won! The coin landed on {result}. +{credits} credits"
            else:
                remove_credits(email, float(credits))
                outcome_text = f"You lost! The coin landed on {result}. -{credits} credits"
        except Exception as e:
            logger.error(f"Coinflip credit settlement failed for {email}: {e}")
            await ctx.respond("There was an error updating your credits. Please contact support.", ephemeral=True)
            return

        embed = discord.Embed(title="Coinflip", color=discord.Color.gold())
        embed.add_field(name="Your Pick", value=user_choice.capitalize(), inline=True)
        embed.add_field(name="Result", value=result.capitalize(), inline=True)
        embed.add_field(name="Bet", value=str(credits), inline=True)
        embed.set_footer(text=outcome_text)
        await ctx.respond(embed=embed, ephemeral=False)


def setup(bot, flask_app=None):
    bot.add_cog(Coinflip(bot, flask_app))
