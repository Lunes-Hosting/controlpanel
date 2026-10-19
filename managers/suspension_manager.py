"""Required reasons and source tracking for account and server suspensions."""

from .database_manager import DatabaseManager
from .suspension_notifications import (queue_suspension_email, clear_suspension_email_keys,
                                       kick_suspension_email_delivery)


def ensure_suspension_schema():
    DatabaseManager.execute_query("""
        CREATE TABLE IF NOT EXISTS user_suspensions (
            user_id BIGINT UNSIGNED PRIMARY KEY,
            reason VARCHAR(1000) NOT NULL,
            source VARCHAR(32) NOT NULL,
            actor VARCHAR(255) NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
    """)
    DatabaseManager.execute_query("""
        CREATE TABLE IF NOT EXISTS server_suspensions (
            server_id BIGINT UNSIGNED PRIMARY KEY,
            reason VARCHAR(1000) NOT NULL DEFAULT '',
            source VARCHAR(32) NOT NULL DEFAULT 'unknown',
            active BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB
    """)


def require_reason(reason):
    reason = str(reason or "").strip()
    if not reason or len(reason) > 1000:
        raise ValueError("Enter a suspension reason between 1 and 1000 characters.")
    return reason


def set_user_suspension(user_id, suspended, reason=None, source="manual", actor="unknown"):
    reason = require_reason(reason) if suspended else ""
    with DatabaseManager.transaction() as cursor:
        cursor.execute("SELECT id, email FROM users WHERE id = %s FOR UPDATE", (int(user_id),))
        account = cursor.fetchone()
        if account is None:
            raise ValueError("Account does not exist")
        cursor.execute("UPDATE users SET suspended = %s WHERE id = %s", (bool(suspended), int(user_id)))
        if suspended:
            cursor.execute(
                "INSERT INTO user_suspensions (user_id, reason, source, actor) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE reason=VALUES(reason), source=VALUES(source), actor=VALUES(actor)",
                (int(user_id), reason, source, str(actor)[:255]),
            )
            queue_suspension_email(cursor, "user", user_id, account[1], reason, source=source)
        else:
            cursor.execute("DELETE FROM user_suspensions WHERE user_id = %s", (int(user_id),))
            clear_suspension_email_keys(cursor, "user", user_id)
    if suspended:
        kick_suspension_email_delivery()


def get_user_suspension(user_id):
    row = DatabaseManager.execute_query("SELECT reason FROM user_suspensions WHERE user_id = %s", (int(user_id),))
    return row[0] if row else "Legacy suspension — reason not recorded"


def get_server_suspension(server_id):
    row = DatabaseManager.execute_query(
        "SELECT reason, source, active FROM server_suspensions WHERE server_id = %s", (int(server_id),)
    )
    return {"reason": row[0], "source": row[1], "active": bool(row[2])} if row else None


try:
    ensure_suspension_schema()
except Exception:
    pass
