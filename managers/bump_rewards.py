"""Award each DISBOARD confirmation once, across restarts and workers."""

from mysql.connector import IntegrityError
from config import DISCORD_GUILD_ID
from .database_manager import DatabaseManager
from .credit_manager import _add_credits_in_transaction


def award_bump(message_id, guild_id, discord_id):
    if not DISCORD_GUILD_ID or int(guild_id) != int(DISCORD_GUILD_ID):
        return False
    try:
        with DatabaseManager.transaction() as cursor:
            cursor.execute("SELECT email FROM users WHERE discord_id = %s AND suspended = 0 FOR UPDATE", (discord_id,))
            account = cursor.fetchone()
            if not account:
                return False
            cursor.execute(
                "INSERT INTO discord_bump_rewards (message_id, guild_id, discord_id) VALUES (%s, %s, %s)",
                (message_id, guild_id, discord_id),
            )
            _add_credits_in_transaction(cursor, account[0], 4, set_client=False)
    except IntegrityError as exc:
        if exc.errno == 1062:
            return False
        raise
    return True


DatabaseManager.execute_query("""
    CREATE TABLE IF NOT EXISTS discord_bump_rewards (
        message_id BIGINT UNSIGNED PRIMARY KEY,
        guild_id BIGINT UNSIGNED NOT NULL,
        discord_id BIGINT UNSIGNED NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB
""")
