"""Automatic, idempotent performance-index migration; also runnable standalone."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mysql.connector
import logging

INDEXES = (
    ('users', 'users_pterodactyl_id_idx', ('pterodactyl_id',)),
    ('users', 'users_discord_id_idx', ('discord_id',)),
    ('users', 'users_last_seen_idx', ('last_seen',)),
    ('tickets', 'tickets_owner_status_created_idx', ('user_id', 'status', 'created_at', 'id')),
    ('ticket_comments', 'comments_ticket_id_idx', ('ticket_id', 'id')),
    ('ticket_discord_channels', 'ticket_channel_id_idx', ('channel_id',)),
)


def apply_indexes(cursor):
    for table, name, columns in INDEXES:
        cursor.execute(
            "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (table,),
        )
        rows = cursor.fetchall()
        existing = {}
        for index_name, column in rows:
            existing.setdefault(index_name, []).append(column)
        if any(tuple(parts[:len(columns)]) == columns for parts in existing.values()):
            continue
        # Names and columns are fixed above, never user input.
        try:
            cursor.execute(
                f"ALTER TABLE `{table}` ADD INDEX `{name}` ({', '.join('`' + c + '`' for c in columns)})"
            )
        except mysql.connector.Error as exc:
            if exc.errno == 1061:  # Another worker created the same index.
                continue
            raise
        print(f"Added {table}.{name}")


def add_indexes():
    # Standalone connection avoids importing app managers, warming API caches,
    # or running unrelated startup migrations during this maintenance command.
    from config import HOST, USER, PASSWORD, DATABASE
    connection = mysql.connector.connect(host=HOST, user=USER, password=PASSWORD,
                                         database=DATABASE, connection_timeout=5)
    try:
        cursor = connection.cursor(buffered=True)
        try:
            apply_indexes(cursor)
        finally:
            cursor.close()
    finally:
        connection.close()


def ensure_performance_indexes():
    try:
        add_indexes()
    except mysql.connector.Error as exc:
        logging.getLogger(__name__).warning(
            'Optional performance indexes could not be applied (database error %s); continuing startup.',
            exc.errno,
        )


if __name__ == '__main__':
    add_indexes()
