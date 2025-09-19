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
                # Some bots add embeds shortly after sending. Try a few delayed re-fetches.
                logger.debug(f"DISBOARD message {message.id} has 0 embeds initially; starting re-fetch attempts inline")
                await self._refetch_and_process(message)
                return

            await self._process_message(message)
        except Exception as e:
            logger.error(f"Failed to handle DISBOARD bump: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):  # type: ignore
        try:
            logger.debug(
                f"on_message_edit: id={getattr(after, 'id', None)} author_id={getattr(after.author, 'id', None)} "
                f"before_embeds={len(before.embeds) if hasattr(before, 'embeds') and before.embeds else 0} "
                f"after_embeds={len(after.embeds) if hasattr(after, 'embeds') and after.embeds else 0}"
            )
            if getattr(after.author, 'id', None) != DISBOARD_BOT_ID:
                return
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
            logger.debug(
                f"on_raw_message_edit: id={getattr(msg, 'id', None)} author_id={getattr(msg.author, 'id', None)} embeds={len(msg.embeds) if getattr(msg, 'embeds', None) else 0}"
            )
            if getattr(msg.author, 'id', None) != DISBOARD_BOT_ID:
                return
            await self._process_message(msg)
        except Exception as e:
            logger.error(f"Failed in on_raw_message_edit handler: {e}")

    async def _refetch_and_process(self, original: discord.Message):
        """Refetch the message a few times to see if embeds were attached later."""
        try:
            import asyncio
            delays = [1.0, 3.0, 5.0]
            logger.debug(f"_refetch_and_process: starting for message {original.id} with delays={delays}")
            for d in delays:
                logger.debug(f"_refetch_and_process: sleeping {d}s before refetch for message {original.id}")
                await asyncio.sleep(d)
                try:
                    fetched = await original.channel.fetch_message(original.id)
                    logger.debug(
                        f"Refetch attempt after {d}s: message {original.id} embeds={len(fetched.embeds) if fetched.embeds else 0}"
                    )
                    if fetched.embeds:
                        await self._process_message(fetched)
                        return
                except Exception as ex:
                    logger.error(f"Refetch failed for message {original.id} after {d}s: {ex}")
            logger.debug(f"Refetch attempts exhausted for message {original.id}; still no embeds")
        except Exception as e:
            logger.error(f"_refetch_and_process error for message {getattr(original, 'id', None)}: {e}")

    async def _process_message(self, message: discord.Message):
        """Core detection and reward logic, shared by on_message and edit/refetch paths."""
        # Find the bump confirmation embed by image URL
        matched = False
        for idx, emb in enumerate(message.embeds):
            try:
                # discord.EmbedProxy for image with .url attr
                img_url = getattr(emb.image, "url", None) if getattr(emb, "image", None) else None
                logger.debug(
                    f"Process embed[{idx}]: title={getattr(emb, 'title', None)} img_url={img_url} target={DISBOARD_BUMP_IMAGE}"
                )
                if emb.image and img_url == DISBOARD_BUMP_IMAGE:
                    matched = True
                    logger.info(
                        f"Message {message.id}: matched DISBOARD bump embed by image URL (process phase)"
                    )
                    break
            except Exception as ex:
                logger.error(f"Error inspecting embed on message {message.id} (process phase): {ex}")
                continue
        if not matched:
            try:
                embed_summary = [
                    {
                        "title": getattr(e, "title", None),
                        "image": getattr(getattr(e, "image", None), "url", None) if getattr(e, "image", None) else None,
                        "author": getattr(getattr(e, "author", None), "name", None) if getattr(e, "author", None) else None,
                        "description": getattr(e, "description", None),
                    }
                    for e in (message.embeds or [])
                ]
            except Exception as ex:
                embed_summary = f"<error summarizing embeds: {ex}>"
            logger.debug(
                f"Message {message.id}: DISBOARD author but embed did not match bump image URL (process phase); embed_summary={embed_summary}"
            )
            return

        # Identify the user who initiated the slash command
        bumper_id = None
        # discord.py exposes message.interaction (MessageInteraction) with .user
        try:
            logger.debug(
                f"(process) message.interaction present={bool(getattr(message, 'interaction', None))} "
                f"interaction.user={getattr(getattr(message, 'interaction', None), 'user', None)}"
            )
            if message.interaction and getattr(message.interaction, 'user', None):
                bumper_id = message.interaction.user.id  # type: ignore[attr-defined]
        except Exception as ex:
            logger.error(f"Error reading message.interaction for message {message.id} (process phase): {ex}")
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

        # Award 1 credit to the linked email
        email = info.get("email")
        try:
            logger.info(
                f"(process) Awarding +1 credit to email={email} (discord_id={bumper_id}) for DISBOARD bump"
            )
            add_credits(email, 1, set_client=False)
            await message.channel.send(
                content=(
                    f"<@{bumper_id}>, thanks for the bump on [{message.guild.name} | DISBOARD: Discord Server List]"
                    f"(https://disboard.org/server/{message.guild.id})! +1 credit added to your account."
                )
            )
            logger.info(f"(process) Awarded +1 credit to {email} for DISBOARD bump by {bumper_id}")
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
