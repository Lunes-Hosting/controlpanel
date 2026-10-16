"""UUID ticket identifiers and an idempotent migration for existing installs."""

from .database_manager import DatabaseManager


def short_ticket_id(ticket_id):
    """Display the UUID prefix, retaining legacy numeric labels in full."""
    return str(ticket_id).split("-", 1)[0]


def ensure_ticket_id_schema():
    """Widen ticket keys without changing existing IDs or their relationships.

    Run during startup before serving requests. MySQL preserves existing numeric
    IDs as strings; only new tickets receive UUIDs. The account needs ALTER rights
    on the first upgraded startup. Each ALTER is safe to retry after interruption.
    """
    for table, column in (
        ("tickets", "id"),
        ("ticket_comments", "ticket_id"),
        ("ticket_discord_channels", "ticket_id"),
    ):
        row = DatabaseManager.execute_query(
            "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
        # The optional Discord link table may not have been created yet.
        if row is None:
            if table == "ticket_discord_channels":
                continue
            raise RuntimeError(f"Missing required ticket column: {table}.{column}")
        if row[0].lower() in ("char", "varchar") and row[1] >= 36:
            continue
        # Identifiers above are fixed constants, never request input. Omitting
        # AUTO_INCREMENT removes it from tickets.id while preserving its key.
        DatabaseManager.execute_query(
            f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` VARCHAR(36) NOT NULL"
        )


def ensure_comment_auto_increment():
    row = DatabaseManager.execute_query(
        "SELECT EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'ticket_comments' AND COLUMN_NAME = 'id'"
    )
    if not row:
        raise RuntimeError("Missing ticket_comments.id")
    if "auto_increment" not in str(row[0]).lower():
        DatabaseManager.execute_query("ALTER TABLE ticket_comments MODIFY COLUMN id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT")
