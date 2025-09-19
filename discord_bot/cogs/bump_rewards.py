import discord  # type: ignore
from discord.ext import commands  # type: ignore

from ..utils.database import UserDB
from managers.credit_manager import add_credits
from ..utils.logger import logger

DISBOARD_BOT_ID = 302050872383242240
DISBOARD_BUMP_IMAGE = "https://disboard.org/images/bot-command-image-bump.png"


class BumpRewards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):  # type: ignore
        try:
            # Only care about messages from the DISBOARD bot
            if message.author.id != DISBOARD_BOT_ID:
                return
            if not message.embeds:
                return

            # Find the bump confirmation embed by image URL
            matched = False
            for emb in message.embeds:
                try:
                    # discord.EmbedProxy for image with .url attr
                    if emb.image and getattr(emb.image, "url", None) == DISBOARD_BUMP_IMAGE:
                        matched = True
                        break
                except Exception:
                    continue
            if not matched:
                return

            # Identify the user who initiated the slash command
            bumper_id = None
            # discord.py exposes message.interaction (MessageInteraction) with .user
            try:
                if message.interaction and message.interaction.user:
                    bumper_id = message.interaction.user.id
            except Exception:
                bumper_id = None

            if bumper_id is None:
                # Fallback: try to parse from content mention if present (best-effort)
                # Not all locales include a mention, so we safely return if unknown
                return

            # Check if this Discord user is linked
            info = UserDB.get_discord_user_info(bumper_id)
            if isinstance(info, str):
                # Not linked - prompt to link
                try:
                    await message.channel.send(
                        content=(
                            f"<@{bumper_id}>, thanks for the bump! To earn credits, link your account: "
                            f"use /getcode with your panel email, then /link with the code."
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to prompt unlinked bumper {bumper_id}: {e}")
                return

            # Award 1 credit to the linked email
            email = info.get("email")
            try:
                add_credits(email, 1, set_client=False)
                await message.channel.send(
                    content=(
                        f"<@{bumper_id}>, thanks for the bump on [{message.guild.name} | DISBOARD: Discord Server List]"
                        f"(https://disboard.org/server/{message.guild.id})! +1 credit added to your account."
                    )
                )
                logger.info(f"Awarded +1 credit to {email} for DISBOARD bump by {bumper_id}")
            except Exception as e:
                logger.error(f"Failed to add bump credit for {email} ({bumper_id}): {e}")
                try:
                    await message.channel.send(
                        content=(
                            f"<@{bumper_id}>, thanks for the bump! There was an error adding your credit. "
                            f"Please contact support."
                        )
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Failed to handle DISBOARD bump: {e}")


def setup(bot, flask_app=None):
    bot.add_cog(BumpRewards(bot))
