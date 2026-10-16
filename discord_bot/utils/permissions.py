"""Hosting staff commands are only authorized in the hosting guild."""

from config import DISCORD_GUILD_ID


def is_staff_context(ctx):
    guild = getattr(ctx, "guild", None)
    permissions = getattr(ctx.author, "guild_permissions", None)
    if not DISCORD_GUILD_ID or not guild or guild.id != int(DISCORD_GUILD_ID) or not permissions:
        return False
    return bool(permissions.administrator or any(
        role.id == 1364999900135165993 for role in getattr(ctx.author, "roles", [])
    ))
