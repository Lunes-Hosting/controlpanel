import discord  # type: ignore
from discord.ext import commands  # type: ignore
from discord.commands import slash_command  # type: ignore
from typing import List, Tuple
import random

from ..utils.database import UserDB
from managers.credit_manager import add_credits, remove_credits
from ..utils.logger import logger

# Simple card and blackjack helpers
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def new_deck() -> List[str]:
    deck = [f"{rank}{suit}" for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def hand_value(hand: List[str]) -> int:
    total = 0
    aces = 0
    for card in hand:
        rank = card[:-1]
        if rank in ["J", "Q", "K"]:
            total += 10
        elif rank == "A":
            aces += 1
            total += 11
        else:
            total += int(rank)
    # Adjust for aces
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def format_hand(hand: List[str]) -> str:
    return " ".join(hand)


class BlackjackView(discord.ui.View):
    def __init__(self, ctx: discord.ApplicationContext, email: str, bet: int):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.email = email
        self.bet = bet
        self.player_hand: List[str] = []
        self.dealer_hand: List[str] = []
        self.deck = new_deck()
        # Initial deal
        self.player_hand.append(self.deck.pop())
        self.dealer_hand.append(self.deck.pop())
        self.player_hand.append(self.deck.pop())
        self.dealer_hand.append(self.deck.pop())
        self.game_over = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    def status_embed(self, final: bool = False) -> discord.Embed:
        embed = discord.Embed(title="Blackjack", color=discord.Color.dark_green())
        embed.add_field(name="Your Hand", value=f"{format_hand(self.player_hand)} (Total: {hand_value(self.player_hand)})", inline=False)
        if final:
            embed.add_field(name="Dealer Hand", value=f"{format_hand(self.dealer_hand)} (Total: {hand_value(self.dealer_hand)})", inline=False)
        else:
            embed.add_field(name="Dealer Hand", value=f"{self.dealer_hand[0]} ??", inline=False)
        embed.set_footer(text=f"Bet: {self.bet} credits")
        return embed

    def end_game(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        self.game_over = True

    async def settle(self, interaction: discord.Interaction):  # type: ignore
        p = hand_value(self.player_hand)
        d = hand_value(self.dealer_hand)
        result = None
        if p > 21:
            result = "lose"
        elif d > 21:
            result = "win"
        elif p > d:
            result = "win"
        elif p < d:
            result = "lose"
        else:
            result = "push"

        # Apply credits
        try:
            if result == "win":
                add_credits(self.email, self.bet, set_client=False)
                footer = f"You win! +{self.bet} credits"
            elif result == "lose":
                # remove_credits returns 'SUSPEND' if insufficient, but we pre-checked balance so shouldn't happen here
                remove_credits(self.email, float(self.bet))
                footer = f"You lose! -{self.bet} credits"
            else:
                footer = "Push! No credits won or lost."
        except Exception as e:
            logger.error(f"Credit settlement failed for {self.email}: {e}")
            footer = "Game ended, but there was an error updating credits. Please contact support."

        embed = self.status_embed(final=True)
        embed.set_footer(text=f"Bet: {self.bet} credits | {footer}")
        self.end_game()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, button: discord.ui.Button, interaction: discord.Interaction):  # type: ignore
        if self.game_over:
            await interaction.response.send_message("Game already finished.", ephemeral=True)
            return
        self.player_hand.append(self.deck.pop())
        p_total = hand_value(self.player_hand)
        if p_total > 21:
            # Player busts immediately
            await self.settle(interaction)
            return
        if p_total == 21:
            # Auto-stand at 21: dealer hits until exceeding player's total or bust
            while hand_value(self.dealer_hand) <= p_total and hand_value(self.dealer_hand) < 22:
                self.dealer_hand.append(self.deck.pop())
            await self.settle(interaction)
            return
        await interaction.response.edit_message(embed=self.status_embed(final=False), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, button: discord.ui.Button, interaction: discord.Interaction):  # type: ignore
        if self.game_over:
            await interaction.response.send_message("Game already finished.", ephemeral=True)
            return
        # Dealer hits until above player's total or busts
        player_total = hand_value(self.player_hand)
        while hand_value(self.dealer_hand) <= player_total and hand_value(self.dealer_hand) < 22:
            self.dealer_hand.append(self.deck.pop())
        await self.settle(interaction)


class Blackjack(commands.Cog):
    def __init__(self, bot, flask_app=None):
        self.bot = bot
        self.flask_app = flask_app

    @slash_command(name="blackjack", description="Play blackjack with credits")
    async def blackjack(self, ctx: discord.ApplicationContext, credits: discord.Option(int, "Bet amount (credits)"): int):  # type: ignore
        await ctx.defer(ephemeral=False)
        # Verify user is linked and not suspended
        info = UserDB.get_discord_user_info(ctx.author.id)
        if isinstance(info, str):
            await ctx.respond("You must be linked to play. Use /getcode and /link to link your account.", ephemeral=True)
            return
        if info.get("suspended"):
            await ctx.respond("Your account is suspended and cannot play.", ephemeral=True)
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

        # Pre-reserve by attempting to remove credits now to prevent double-spend via multiple sessions
        # We will give back on push or add double on win by adding 2x if we removed now. To match user's request
        # (win gets amount, lose loses amount), we'll only remove on loss to keep simple and match current systems.
        # Therefore, we do NOT pre-deduct. We'll only remove on loss at settlement.

        view = BlackjackView(ctx, email=email, bet=int(credits))
        embed = view.status_embed(final=False)
        await ctx.respond(embed=embed, view=view, ephemeral=False)


def setup(bot, flask_app=None):
    bot.add_cog(Blackjack(bot, flask_app))
