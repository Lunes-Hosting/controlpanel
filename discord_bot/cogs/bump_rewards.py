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
        logger.info("BumpRewards cog initialized")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):  # type: ignore
        try:
            logger.debug(
                f"on_message: id={message.id} author_id={getattr(message.author, 'id', None)} "
                f"author_bot={getattr(message.author, 'bot', None)} guild_id={getattr(message.guild, 'id', None)} "
                f"channel_id={getattr(message.channel, 'id', None)} content_len={len(message.content) if hasattr(message, 'content') and message.content else 0} "
                f"embeds={len(message.embeds) if hasattr(message, 'embeds') and message.embeds else 0}"
            )
            # Only care about messages from the DISBOARD bot
            if message.author.id != DISBOARD_BOT_ID:
                logger.debug(
                    f"Skipping message {message.id}: author {message.author.id} != DISBOARD_BOT_ID {DISBOARD_BOT_ID}"
                )
                return
            if not message.embeds:
                logger.debug(f"Skipping message {message.id}: no embeds on DISBOARD message")
                return

            # Find the bump confirmation embed by image URL
            matched = False
            for idx, emb in enumerate(message.embeds):
                try:
                    # discord.EmbedProxy for image with .url attr
                    img_url = getattr(emb.image, "url", None) if getattr(emb, "image", None) else None
                    logger.debug(
                        f"Inspect embed[{idx}]: title={getattr(emb, 'title', None)} "
                        f"img_url={img_url} target={DISBOARD_BUMP_IMAGE}"
                    )
                    if emb.image and img_url == DISBOARD_BUMP_IMAGE:
                        matched = True
                        logger.info(
                            f"Message {message.id}: matched DISBOARD bump embed by image URL"
                        )
                        break
                except Exception as ex:
                    logger.error(f"Error inspecting embed on message {message.id}: {ex}")
                    continue
            if not matched:
                logger.debug(
                    f"Message {message.id}: DISBOARD author but embed did not match bump image URL"
                )
                return

            # Identify the user who initiated the slash command
            bumper_id = None
            # discord.py exposes message.interaction (MessageInteraction) with .user
            try:
                logger.debug(
                    f"message.interaction present={bool(getattr(message, 'interaction', None))} "
                    f"interaction.user={getattr(getattr(message, 'interaction', None), 'user', None)}"
                )
                if message.interaction and getattr(message.interaction, 'user', None):
                    bumper_id = message.interaction.user.id  # type: ignore[attr-defined]
            except Exception as ex:
                logger.error(f"Error reading message.interaction for message {message.id}: {ex}")
                bumper_id = None

            if bumper_id is None:
                # Fallback: try to parse from content mention if present (best-effort)
                # Not all locales include a mention, so we safely return if unknown
                logger.debug(
                    f"Message {message.id}: Could not determine bumper from interaction; no fallback implemented."
                )
                return

            # Check if this Discord user is linked
            logger.debug(f"Looking up linked user for discord_id={bumper_id}")
            info = UserDB.get_discord_user_info(bumper_id)
            if isinstance(info, str):
                # Not linked - prompt to link
                logger.info(
                    f"DISBOARD bumper {bumper_id} is not linked; prompting to link."
                )
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
                logger.info(
                    f"Awarding +1 credit to email={email} (discord_id={bumper_id}) for DISBOARD bump"
                )
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
