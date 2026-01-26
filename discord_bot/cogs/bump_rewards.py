import discord  # type: ignore
from discord.ext import commands  # type: ignore
import asyncio

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
            # Only care about messages from the DISBOARD bot
            if message.author.id != DISBOARD_BOT_ID:
                return
            logger.debug(f"DISBOARD message {message.id}: embeds={len(message.embeds) if message.embeds else 0}")
            if not message.embeds:
                # Some bots add embeds shortly after sending. Try a few delayed re-fetches.
                logger.debug(f"DISBOARD message {message.id}: no embeds; scheduling refetch")
                # Run refetch in background to avoid getting cancelled or blocking other events
                asyncio.create_task(self._refetch_and_process(message))
                return

            await self._process_message(message)
        except Exception as e:
            logger.error(f"Failed to handle DISBOARD bump: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):  # type: ignore
        try:
            if getattr(after.author, 'id', None) != DISBOARD_BOT_ID:
                return
            logger.debug(
                f"on_message_edit: id={getattr(after, 'id', None)} embeds={len(after.embeds) if after.embeds else 0}"
            )
            # Re-run the same detection on the edited message
            await self._process_message(after)
        except Exception as e:
            logger.error(f"Failed in on_message_edit handler: {e}")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):  # type: ignore
        try:
            # Raw event fires even if message not in cache. We fetch and process.
            channel = self.bot.get_channel(payload.channel_id)
            if channel is None or not isinstance(channel, discord.abc.Messageable):  # type: ignore
                return
            try:
                msg = await channel.fetch_message(payload.message_id)  # type: ignore[attr-defined]
            except Exception as ex:
                logger.error(f"on_raw_message_edit fetch failed for message {payload.message_id}: {ex}")
                return
            if getattr(msg.author, 'id', None) != DISBOARD_BOT_ID:
                return
            logger.debug(
                f"on_raw_message_edit: id={getattr(msg, 'id', None)} embeds={len(msg.embeds) if getattr(msg, 'embeds', None) else 0}"
            )
            await self._process_message(msg)
        except Exception as e:
            logger.error(f"Failed in on_raw_message_edit handler: {e}")

    async def _refetch_and_process(self, original: discord.Message):
        """Refetch the message a few times to see if embeds were attached later."""
        try:
            delays = [1.0, 3.0, 5.0]
            logger.debug(f"refetch: start message {original.id} delays={delays}")
            for d in delays:
                logger.debug(f"refetch: sleeping {d}s for message {original.id}")
                try:
                    await asyncio.sleep(d)
                except asyncio.CancelledError:
                    logger.debug(f"refetch: task cancelled during sleep for message {original.id}")
                    return
                try:
                    fetched = await original.channel.fetch_message(original.id)
                    if getattr(fetched, 'embeds', None):
                        logger.debug(
                            f"refetch: message {original.id} got embeds={len(fetched.embeds)} after {d}s"
                        )
                    else:
                        logger.debug(
                            f"refetch: message {original.id} still no embeds after {d}s"
                        )
                    if getattr(fetched, 'embeds', None):
                        await self._process_message(fetched)
                        return
                except Exception as ex:
                    logger.error(f"Refetch failed for message {original.id} after {d}s: {ex}")
            logger.debug(f"refetch: done message {original.id}; no embeds found")
        except Exception as e:
            logger.error(f"_refetch_and_process error for message {getattr(original, 'id', None)}: {e}")

    async def _process_message(self, message: discord.Message):
        """Core detection and reward logic, shared by on_message and edit/refetch paths."""
        # Find the bump confirmation by either:
        # - image URL match (classic DISBOARD confirmation), OR
        # - interaction name == 'bump', OR
        # - embed description contains 'Bump done' text
        matched = False
        # Check interaction name first (prefer interaction_metadata; fallback to deprecated interaction)
        try:
            im = getattr(message, 'interaction_metadata', None)
            interaction_name = getattr(im, 'name', None)
            if isinstance(interaction_name, str) and interaction_name.lower() == 'bump':
                matched = True
                logger.info(f"Message {message.id}: matched DISBOARD by interaction name 'bump'")
        except Exception as ex:
            logger.error(f"(process) reading interaction name failed for message {message.id}: {ex}")
        for idx, emb in enumerate(message.embeds):
            try:
                # discord.EmbedProxy for image with .url attr
                img_url = getattr(emb.image, "url", None) if getattr(emb, "image", None) else None
                if not matched and emb.image and img_url == DISBOARD_BUMP_IMAGE:
                    matched = True
                    logger.info(
                        f"Message {message.id}: matched DISBOARD bump embed by image URL (process phase)"
                    )
                    break
                # Text signature fallback when no image; avoid matching /debug by requiring 'Bump done'
                desc = getattr(emb, 'description', '') or ''
                if not matched and isinstance(desc, str) and 'bump done' in desc.lower():
                    matched = True
                    logger.info(
                        f"Message {message.id}: matched DISBOARD bump by description text 'Bump done'"
                    )
                    break
            except Exception as ex:
                logger.error(f"Error inspecting embed on message {message.id} (process phase): {ex}")
                continue
        if not matched:
            return

        # Identify the user who initiated the slash command
        bumper_id = None
        try:
            im = getattr(message, 'interaction_metadata', None)
            bumper_user = getattr(im, 'user', None)
            if bumper_user is None:
                # Fallback for older discord.py
                interaction = getattr(message, 'interaction', None)
                bumper_user = getattr(interaction, 'user', None)
            if bumper_user is not None:
                bumper_id = getattr(bumper_user, 'id', None)
        except Exception as ex:
            logger.error(f"Error reading interaction metadata for message {message.id} (process phase): {ex}")
            bumper_id = None

        if bumper_id is None:
            # Fallback: try to parse from content mention if present (best-effort)
            # Not all locales include a mention, so we safely return if unknown
            logger.debug(
                f"Message {message.id}: Could not determine bumper from interaction; no fallback implemented (process phase)."
            )
            return

        # Check if this Discord user is linked
        logger.debug(f"(process) Looking up linked user for discord_id={bumper_id}")
        info = UserDB.get_discord_user_info(bumper_id)
        if isinstance(info, str):
            # Not linked - prompt to link
            logger.info(
                f"(process) DISBOARD bumper {bumper_id} is not linked; prompting to link."
            )
            try:
                await message.channel.send(
                    content=(
                        f"<@{bumper_id}>, thanks for the bump! To earn credits, link your account: "
                        f"use /getcode with your panel email, then /link with the code."
                    )
                )
            except Exception as e:
                logger.error(f"Failed to prompt unlinked bumper {bumper_id} (process phase): {e}")
            return

        # Award 4 credit to the linked email
        email = info.get("email")
        try:
            logger.info(
                f"(process) Awarding +4 credit to email={email} (discord_id={bumper_id}) for DISBOARD bump"
            )
            add_credits(email, 4, set_client=False)
            await message.channel.send(
                content=(
                    f"<@{bumper_id}>, thanks for the bump on [{message.guild.name} | DISBOARD: Discord Server List]"
                    f"(https://disboard.org/server/{message.guild.id})! +4 credit added to your account."
                )
            )
            logger.info(f"(process) Awarded +4 credit to {email} for DISBOARD bump by {bumper_id}")
        except Exception as e:
            logger.error(f"Failed to add bump credit for {email} ({bumper_id}) (process phase): {e}")
            try:
                await message.channel.send(
                    content=(
                        f"<@{bumper_id}>, thanks for the bump! There was an error adding your credit. "
                        f"Please contact support."
                    )
                )
            except Exception:
                pass


def setup(bot, flask_app=None):
    bot.add_cog(BumpRewards(bot))
