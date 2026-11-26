from typing import Optional

from .database_manager import DatabaseManager
from .ticket_ids import ensure_ticket_id_schema, ensure_comment_auto_increment


def ensure_link_table() -> None:
    DatabaseManager.execute_query(
        """
        CREATE TABLE IF NOT EXISTS ticket_discord_channels (
            ticket_id VARCHAR(36) NOT NULL PRIMARY KEY,
            channel_id BIGINT UNSIGNED NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


def ensure_language_table() -> None:
    # ticket_key is a web ticket's ID, or the channel ID of a ticket opened in Discord.
    DatabaseManager.execute_query(
        """
        CREATE TABLE IF NOT EXISTS ticket_languages (
            ticket_key VARCHAR(36) NOT NULL PRIMARY KEY,
            language VARCHAR(16) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


ensure_link_table()
ensure_language_table()
ensure_ticket_id_schema()
ensure_comment_auto_increment()


def set_channel(ticket_id: str, channel_id: int) -> None:
    DatabaseManager.execute_query(
        """
        INSERT INTO ticket_discord_channels (ticket_id, channel_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)
        """,
        (ticket_id, channel_id),
    )


def get_channel_id(ticket_id: str) -> Optional[int]:
    record = DatabaseManager.execute_query(
        "SELECT channel_id FROM ticket_discord_channels WHERE ticket_id = %s",
        (ticket_id,),
    )
    return record[0] if record else None


def get_ticket_id(channel_id: int) -> Optional[str]:
    record = DatabaseManager.execute_query(
        "SELECT ticket_id FROM ticket_discord_channels WHERE channel_id = %s",
        (channel_id,),
    )
    return record[0] if record else None


def clear_channel(ticket_id: str) -> None:
    DatabaseManager.execute_query(
        "DELETE FROM ticket_discord_channels WHERE ticket_id = %s",
        (ticket_id,),
    )


def get_ticket_language(ticket_key) -> Optional[str]:
    """The language the ticket's opener writes in, once they have written in one other than English."""
    record = DatabaseManager.execute_query(
        "SELECT language FROM ticket_languages WHERE ticket_key = %s",
        (str(ticket_key),),
    )
    return record[0] if record else None


def set_ticket_language(ticket_key, language: str) -> None:
    DatabaseManager.execute_query(
        """
        INSERT INTO ticket_languages (ticket_key, language)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE language = VALUES(language)
        """,
        (str(ticket_key), language),
    )
