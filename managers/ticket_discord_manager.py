from typing import Optional

from .database_manager import DatabaseManager


def ensure_link_table() -> None:
    DatabaseManager.execute_query(
        """
        CREATE TABLE IF NOT EXISTS ticket_discord_channels (
            ticket_id INT UNSIGNED NOT NULL PRIMARY KEY,
            channel_id BIGINT UNSIGNED NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
    )


ensure_link_table()


def set_channel(ticket_id: int, channel_id: int) -> None:
    DatabaseManager.execute_query(
        """
        INSERT INTO ticket_discord_channels (ticket_id, channel_id)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id)
        """,
        (ticket_id, channel_id),
    )


def get_channel_id(ticket_id: int) -> Optional[int]:
    record = DatabaseManager.execute_query(
        "SELECT channel_id FROM ticket_discord_channels WHERE ticket_id = %s",
        (ticket_id,),
    )
    return record[0] if record else None


def get_ticket_id(channel_id: int) -> Optional[int]:
    record = DatabaseManager.execute_query(
        "SELECT ticket_id FROM ticket_discord_channels WHERE channel_id = %s",
        (channel_id,),
    )
    return record[0] if record else None


def clear_channel(ticket_id: int) -> None:
    DatabaseManager.execute_query(
        "DELETE FROM ticket_discord_channels WHERE ticket_id = %s",
        (ticket_id,),
    )
